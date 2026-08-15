"""
embedfix - rewrites links that Discord refuses to embed (X/Twitter, TikTok,
Instagram, ...) onto proxy hosts that serve real OpenGraph/video metadata, so
the content plays inline instead of forcing everyone to open the platform.

The bot replies with the fixed link, suppresses the original message's broken
embed, and puts a cross-mark on its own reply as an undo button. The cross-mark
is taken back off once the poster's undo window closes, so it is only ever
showing while it actually does something.

Config keys:
  embedfix.enabled             - "true"/"false", default true
  embedfix.suppress_original   - "true"/"false", default true. Needs Manage
                                 Messages; degrades to reply-only without it.
  embedfix.remove_seconds      - int 0-3600, how long the original poster may
                                 undo a fix, default 120. Moderators (anyone
                                 with manage_messages) are never time-limited -
                                 once the cross-mark is gone they undo by
                                 adding one back by hand, since the reaction
                                 handler below is stateless. 0 means the poster
                                 can never undo, so no cross-mark is added.
  embedfix.platform.<name>     - "true"/"false" per entry in PLATFORMS,
                                 default true

No HTTP is involved: the rewrite is pure string work on the URL, so nothing is
fetched and no new dependency is needed.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import discord
from discord.ext import commands

logger = logging.getLogger("bot.modules.embedfix")

CROSS_MARK = "\N{CROSS MARK}"

# name -> (host pattern, replacement host, tracking params to drop)
# The host pattern is matched against the whole hostname, case-insensitively.
PLATFORMS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "twitter": (r"(?:www\.|mobile\.)?(?:twitter|x)\.com", "fxtwitter.com", ("t", "s")),
    "tiktok": (r"(?:www\.|vm\.|vt\.|m\.)?tiktok\.com", "tiktokfix.com", ("_t", "_r")),
    "instagram": (r"(?:www\.)?instagram\.com", "kkinstagram.com", ("igsh", "igshid")),
    "reddit": (r"(?:www\.|old\.|new\.)?reddit\.com", "rxddit.com", ("share_id", "utm_source")),
    "bluesky": (r"(?:www\.)?bsky\.app", "fxbsky.app", ()),
    "pixiv": (r"(?:www\.)?pixiv\.net", "phixiv.net", ()),
    "twitch": (r"(?:www\.|clips\.)?twitch\.tv", "fxtwitch.seria.moe", ()),
}

# Every host we might *produce*, plus the common alternatives people paste by
# hand. A URL already on one of these is left alone, which is what stops the
# bot fixing its own reply (its messages are skipped anyway) and stops it
# "fixing" a link someone already fixed themselves.
PROXY_HOSTS = frozenset(
    [replacement for _pattern, replacement, _drop in PLATFORMS.values()]
    + [
        "fxtwitter.com",
        "fixupx.com",
        "vxtwitter.com",
        "twittpr.com",
        "girlcockx.com",
        "tiktokfix.com",
        "vxtiktok.com",
        "tnktok.com",
        "tfxktok.com",
        "ddinstagram.com",
        "kkinstagram.com",
        "eeinstagram.com",
        "rxddit.com",
        "vxreddit.com",
        "fxbsky.app",
        "bskx.app",
        "phixiv.net",
        "fxtwitch.seria.moe",
    ]
)

# Deliberately narrow: only http(s), and stop at whitespace or the characters
# Discord markdown wraps links in. The trailing-punctuation strip in
# _clean_url() handles "look at https://x.com/a/1." and "(https://x.com/a/1)".
URL_RE = re.compile(r"https?://[^\s<>\"'`|]+", re.IGNORECASE)

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Discord's own "don't embed this" syntax. Honouring it means a user can opt a
# single link out of the fixer with no configuration at all.
_ANGLE_WRAPPED_RE = re.compile(r"<[^<>\s]+>")

_TRAILING_PUNCT = ".,!?;:'\"*_~)]}>"

_HOST_MATCHERS = {
    name: re.compile(rf"^{pattern}$", re.IGNORECASE) for name, (pattern, _r, _d) in PLATFORMS.items()
}

MAX_LINKS = 3


def strip_uneditable(content: str) -> str:
    """Pure: blanks out the regions of a message whose links must not be
    touched - fenced code blocks, inline code, and <angle-wrapped> URLs.
    Replaced with spaces rather than deleted so offsets stay meaningful and
    two links can't be glued into one."""

    def blank(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    content = _CODE_BLOCK_RE.sub(blank, content)
    content = _INLINE_CODE_RE.sub(blank, content)
    content = _ANGLE_WRAPPED_RE.sub(blank, content)
    return content


def _clean_url(url: str) -> str:
    """Strips trailing punctuation a URL regex inevitably swallows from prose.
    Closing brackets are only dropped when unbalanced, so a genuine
    'wiki/Foo_(bar)' style path survives."""
    while url and url[-1] in _TRAILING_PUNCT:
        if url[-1] == ")" and url.count(")") <= url.count("("):
            break  # balanced, so the paren belongs to the path
        url = url[:-1]
    return url


def find_links(content: str) -> list[str]:
    """Pure: every http(s) URL in the message, minus the regions users have
    opted out (see strip_uneditable)."""
    return [_clean_url(m) for m in URL_RE.findall(strip_uneditable(content))]


def platform_for(url: str) -> str | None:
    """Pure: the PLATFORMS key this URL belongs to, or None. Returns None for
    a URL already on a proxy host, so rewriting is idempotent."""
    host = urlsplit(url).hostname
    if not host:
        return None
    host = host.lower()
    if host in PROXY_HOSTS:
        return None
    for name, matcher in _HOST_MATCHERS.items():
        if matcher.match(host):
            return name
    return None


def rewrite(url: str, enabled: set[str]) -> str | None:
    """Pure: the proxy-host equivalent of `url`, or None if it isn't a
    fixable link or its platform is switched off for this guild. Path and
    query are preserved; only that platform's known tracking params are
    dropped, because they are noise and (on TikTok) can break the proxy."""
    name = platform_for(url)
    if name is None or name not in enabled:
        return None

    _pattern, replacement, drop = PLATFORMS[name]
    parts = urlsplit(url)
    if not parts.path or parts.path == "/":
        return None  # a bare domain link has nothing to embed

    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in drop])
    # Fragments are dropped: no proxy uses them and they are usually app junk.
    return urlunsplit(("https", replacement, parts.path, query, ""))


def fix_links(content: str, enabled: set[str], limit: int = MAX_LINKS) -> list[str]:
    """Pure: the rewritten links for a message, de-duplicated and capped so a
    message full of links can't turn into a wall of bot replies."""
    fixed: list[str] = []
    for url in find_links(content):
        new = rewrite(url, enabled)
        if new and new not in fixed:
            fixed.append(new)
            if len(fixed) >= limit:
                break
    return fixed


class EmbedFix(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.scheduler_handlers["embedfix_expire"] = self._handle_expire

    async def _remove_window(self, guild_id: int) -> int:
        return await self.bot.stores.config.get_int(
            guild_id, "embedfix.remove_seconds", 120, minimum=0, maximum=3600
        )

    async def _enabled_platforms(self, guild_id: int) -> set[str]:
        enabled = set()
        for name in PLATFORMS:
            if await self.bot.stores.config.get_bool(guild_id, f"embedfix.platform.{name}", True):
                enabled.add(name)
        return enabled

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        guild_id = message.guild.id
        if not await self.bot.stores.config.get_bool(guild_id, "embedfix.enabled", True):
            return

        # Cheap pre-check before touching the database once per platform.
        if not any(platform_for(url) for url in find_links(message.content)):
            return

        fixed = fix_links(message.content, await self._enabled_platforms(guild_id))
        if not fixed:
            return

        try:
            reply = await message.reply("\n".join(fixed), mention_author=False)
        except discord.HTTPException:
            # automod.py may have deleted the source message out from under us
            # (NotFound), or we may lack Send Messages here. Either way there's
            # nothing left to do for this message.
            logger.warning("Failed to post embed fix in %s", message.channel, exc_info=True)
            return

        # A window of 0 means the poster can never undo, so the cross-mark would
        # be dead on arrival - don't offer it at all.
        window = await self._remove_window(guild_id)
        if window > 0:
            try:
                await reply.add_reaction(CROSS_MARK)
            except discord.HTTPException:
                logger.warning("Failed to add undo reaction in %s", message.channel, exc_info=True)
            else:
                await self._schedule_expiry(guild_id, reply, window)

        if await self.bot.stores.config.get_bool(guild_id, "embedfix.suppress_original", True):
            try:
                # The SUPPRESS_EMBEDS flag also blocks embeds Discord attaches
                # later, so there's no need to wait for the broken one to land.
                await message.edit(suppress=True)
            except discord.HTTPException:
                # Needs Manage Messages. Without it the reply still stands, so
                # this is a degradation, not a failure.
                logger.warning("Failed to suppress original embed in %s", message.channel, exc_info=True)

    async def _schedule_expiry(self, guild_id: int, reply: discord.Message, window: int) -> None:
        """Books the cross-mark's removal with the shared scheduler rather than
        sleeping in-process, because an in-process timer dies on restart and
        would leave the stale cross-mark this is meant to prevent."""
        run_at = reply.created_at + timedelta(seconds=window)
        payload = {"channel_id": reply.channel.id, "message_id": reply.id}
        try:
            await self.bot.stores.scheduled.add(guild_id, "embedfix_expire", payload, run_at)
        except RuntimeError:
            # Database unavailable. The fix itself still stands and _may_remove
            # still refuses late undos, so this is cosmetic - the reaction-add
            # path below cleans up the leftover cross-mark on the first click.
            logger.warning("Failed to schedule undo-reaction expiry in %s", reply.channel, exc_info=True)

    async def _handle_expire(self, guild_id: int, payload: dict) -> None:
        """Takes the undo affordance away once the poster's window has closed.
        Cosmetic only - _may_remove stays the authority on who may undo."""
        channel = self.bot.get_channel(payload["channel_id"])
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            return
        try:
            reply = await channel.fetch_message(payload["message_id"])
        except discord.HTTPException:
            return  # already undone and deleted, which is the happy path
        await self._drop_cross_mark(reply)

    async def _drop_cross_mark(self, reply: discord.Message) -> None:
        """Removes only the bot's own cross-mark. clear_reaction() would take
        everyone's but needs Manage Messages; this needs no permission at all."""
        try:
            await reply.remove_reaction(CROSS_MARK, self.bot.user)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Undo. Deliberately stateless - everything needed is recoverable from
        the messages themselves, so undo still works after a restart (which
        matters for moderators, whose window never closes)."""
        if payload.guild_id is None or str(payload.emoji) != CROSS_MARK:
            return
        if payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel_or_thread(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            return

        try:
            reply = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        if reply.author.id != self.bot.user.id or reply.reference is None:
            return
        if reply.reference.message_id is None:
            return

        member = payload.member or guild.get_member(payload.user_id)
        if member is None:
            return

        source = None
        try:
            source = await channel.fetch_message(reply.reference.message_id)
        except discord.HTTPException:
            # The original is gone; only a moderator can clean up the orphan.
            logger.warning("Embed-fix source message %s is gone", reply.reference.message_id)

        if not await self._may_remove(reply, source, member):
            try:
                await reply.remove_reaction(payload.emoji, member)
            except discord.HTTPException:
                pass
            # Self-heal: if our own cross-mark is still up on an already-expired
            # fix - a scheduled row lost to a database outage, or the up-to-30s
            # gap before the scheduler's next sweep - take it down now. Gated on
            # the window really being over, because a bystander clicking during
            # the window is also refused and must not cost the poster their undo.
            if await self._expired(reply, guild.id):
                await self._drop_cross_mark(reply)
            return

        try:
            await reply.delete()
        except discord.HTTPException:
            logger.warning("Failed to delete embed fix in %s", channel, exc_info=True)
            return

        if source is not None:
            try:
                # Give back whatever embed the original would have had.
                await source.edit(suppress=False)
            except discord.HTTPException:
                logger.warning("Failed to unsuppress original in %s", channel, exc_info=True)

    async def _may_remove(
        self,
        reply: discord.Message,
        source: discord.Message | None,
        member: discord.Member,
    ) -> bool:
        if member.guild_permissions.manage_messages:
            return True  # moderators, any time
        if source is None or source.author.id != member.id:
            return False
        return not await self._expired(reply, member.guild.id)

    async def _expired(self, reply: discord.Message, guild_id: int) -> bool:
        window = await self._remove_window(guild_id)
        age = (discord.utils.utcnow() - reply.created_at).total_seconds()
        return age > window


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedFix(bot))
