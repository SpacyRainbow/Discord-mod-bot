"""
antispam - behavior-based spam detection: message flooding, repeated/duplicate
content, and mass mentions. This is the module you're actually replacing -
tune it to taste once it's running against real traffic.

Config keys:
  spam.max_messages   - messages allowed within spam.window_seconds, default 5
  spam.window_seconds - rolling window size in seconds, default 6
  spam.max_duplicates - identical consecutive messages allowed, default 3
  spam.max_mentions   - mentions in a single message before it's flagged, default 5
  spam.timeout_seconds - length of timeout applied on violation, default 300 (5 min)

Matches Sweetie Bot's actual spam module (read directly from
spammodule/SpamModule.go): a violation bulk-deletes the offender's whole
recent burst, not just the message that tripped the threshold.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from collections import defaultdict, deque

import discord
from discord.ext import commands, tasks

from .logging_module import post_log

logger = logging.getLogger("bot.modules.antispam")

# The sweep drops a user's history (and its lock) once nothing has been seen
# from them for longer than any configurable window, so the dicts stop being
# "every member who has ever spoken, forever". 3600 is the upper bound
# spam.window_seconds is clamped to, so no live window can outlive it.
HISTORY_MAX_AGE_SECONDS = 3600
HISTORY_SWEEP_MINUTES = 10


class _UserHistory:
    """Per-user rolling window of recent messages + last content, kept in
    memory (not the DB) since this needs to be checked on every message with
    no I/O latency. Cleared on bot restart, which is fine - spam bursts
    don't survive a restart anyway.

    `messages` holds (channel_id, message_id) pairs, NOT discord.Message
    objects: a Message transitively retains its channel, guild, author,
    attachments and embeds, so a deque of them retained far more than it
    looked like. Everything downstream (grouping by channel, bulk delete)
    only ever needed the two IDs. (review F10)"""

    __slots__ = ("timestamps", "messages", "last_content", "duplicate_count")

    def __init__(self):
        self.timestamps: deque[float] = deque()
        self.messages: deque[tuple[int, int]] = deque()
        self.last_content: str = ""
        self.duplicate_count: int = 0

    def reset(self) -> None:
        self.timestamps.clear()
        self.messages.clear()
        self.last_content = ""
        self.duplicate_count = 0


def is_flooding(history: _UserHistory, now: float, window_seconds: int, max_messages: int) -> bool:
    while history.timestamps and now - history.timestamps[0] > window_seconds:
        history.timestamps.popleft()
    return len(history.timestamps) > max_messages


def is_duplicate_spam(history: _UserHistory, content: str, max_duplicates: int) -> bool:
    if content and content == history.last_content:
        history.duplicate_count += 1
    else:
        history.last_content = content
        history.duplicate_count = 1 if content else 0
    return history.duplicate_count >= max_duplicates


def is_mass_mention(message: discord.Message, max_mentions: int) -> bool:
    return len(message.mentions) + len(message.role_mentions) >= max_mentions


def sync_message_window(history: _UserHistory) -> None:
    """Trims `messages` to match `timestamps`' length after is_flooding() has
    pruned it - mirrors the pruning by count instead of re-implementing the
    time-window comparison a second time."""
    while len(history.messages) > len(history.timestamps):
        history.messages.popleft()


def group_by_channel(refs: list[tuple[int, int]]) -> dict[int, list[int]]:
    """Bulk delete is channel-scoped, so a burst spanning multiple channels
    needs one delete call per channel. Takes (channel_id, message_id) pairs
    and returns {channel_id: [message_id, ...]}. (review F10)"""
    grouped: dict[int, list[int]] = {}
    for channel_id, message_id in refs:
        grouped.setdefault(channel_id, []).append(message_id)
    return grouped


class AntiSpam(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._history: dict[tuple[int, int], _UserHistory] = defaultdict(_UserHistory)
        self._locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def cog_load(self) -> None:
        self.sweep_history.start()

    def cog_unload(self):
        self.sweep_history.cancel()

    def _sweep(self, now: float, max_age: float = HISTORY_MAX_AGE_SECONDS) -> int:
        """Drops history+lock for every user whose newest message is older than
        `max_age`. Returns how many keys were evicted. Both dicts were keyed by
        every (guild, user) that had ever posted and nothing was ever removed.
        (review F10)"""
        dropped = 0
        for key in list(self._history):
            history = self._history[key]
            if history.timestamps and now - history.timestamps[-1] <= max_age:
                continue
            lock = self._locks.get(key)
            if lock is not None and lock.locked():
                # NEVER evict a held lock: the holder and the next caller would
                # get two different Lock objects for the same key and the mutual
                # exclusion would silently stop working. An unheld lock can have
                # no waiters, so dropping it is safe. (review F9/F10)
                continue
            self._history.pop(key, None)
            self._locks.pop(key, None)
            dropped += 1
        return dropped

    @tasks.loop(minutes=HISTORY_SWEEP_MINUTES)
    async def sweep_history(self):
        # tasks.loop only auto-restarts on network errors; anything else would stop
        # this loop permanently, so nothing may escape the body. (review F4)
        try:
            dropped = self._sweep(time.monotonic())
            if dropped:
                logger.debug("Swept %s stale antispam history entries", dropped)
        except Exception:
            logger.exception("antispam sweep_history iteration failed")

    @sweep_history.before_loop
    async def before_sweep_history(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if message.author.guild_permissions.manage_messages:
            return

        guild_id = message.guild.id
        key = (guild_id, message.author.id)

        # Without this lock, a burst of near-simultaneous messages can each
        # get through the awaits below before any of them reaches the
        # detection check, so more than one could independently "discover"
        # the same violation and race on history.reset(). Only ever
        # serializes handling for a single spamming user, never global.
        async with self._locks[key]:
            history = self._history[key]
            now = time.monotonic()
            history.timestamps.append(now)
            history.messages.append((message.channel.id, message.id))

            # Bounded: a negative/zero window or threshold silently disables
            # detection entirely rather than failing loudly. (review F14)
            window = await self.bot.stores.config.get_int(
                guild_id, "spam.window_seconds", 6, minimum=1, maximum=3600
            )
            max_msgs = await self.bot.stores.config.get_int(
                guild_id, "spam.max_messages", 5, minimum=1, maximum=100
            )
            max_dupes = await self.bot.stores.config.get_int(
                guild_id, "spam.max_duplicates", 3, minimum=2, maximum=100
            )
            max_mentions = await self.bot.stores.config.get_int(
                guild_id, "spam.max_mentions", 5, minimum=1, maximum=100
            )

            reason = None
            if is_flooding(history, now, window, max_msgs):
                reason = f"sending messages too quickly (>{max_msgs} in {window}s)"
            elif is_duplicate_spam(history, message.content, max_dupes):
                reason = "repeating the same message"
            elif is_mass_mention(message, max_mentions):
                reason = f"mass mentions (>= {max_mentions})"
            sync_message_window(history)

            if reason:
                burst = list(history.messages)
                history.reset()
                await self._handle_violation(message, reason, burst)

    async def _handle_violation(
        self, message: discord.Message, reason: str, burst: list[tuple[int, int]]
    ) -> None:
        for channel_id, message_ids in group_by_channel(burst).items():
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                continue
            try:
                # delete_messages() handles 0/1/many messages gracefully on
                # its own (a single message falls back to a normal delete),
                # and only ever reads each item's .id - so bare snowflakes
                # work and we don't need to retain Message objects. (review F10)
                group = [discord.Object(id=mid) for mid in message_ids]
                await channel.delete_messages(group, reason=f"Anti-spam: {reason}")
            except discord.HTTPException:
                # Missing permission, already deleted, rate-limited, etc. -
                # none of these should abort the timeout/case-recording that
                # follows.
                logger.warning("Failed to bulk-delete flagged messages in %s", channel, exc_info=True)

        # Capped at Discord's 28-day maximum timeout; anything longer is
        # rejected by the API and every violation logs "could not be timed
        # out". (review F14)
        timeout_seconds = await self.bot.stores.config.get_int(
            message.guild.id, "spam.timeout_seconds", 300, minimum=1, maximum=2419200
        )
        try:
            await message.author.timeout(
                discord.utils.utcnow() + datetime.timedelta(seconds=timeout_seconds),
                reason=f"Anti-spam: {reason}",
            )
            action_taken = f"timed out for {timeout_seconds}s"
        except discord.HTTPException:
            action_taken = "could not be timed out"

        try:
            await self.bot.stores.cases.add(
                message.guild.id, message.author.id, self.bot.user.id, "spam", reason
            )
        except RuntimeError:
            pass  # DB unavailable - degrade gracefully, still take the live action above

        embed = discord.Embed(
            title="Anti-spam triggered",
            description=(
                f"{message.author.mention} - {reason} ({action_taken}), "
                f"{len(burst)} message(s) removed"
            ),
            color=discord.Color.red(),
        )
        await post_log(self.bot, message.guild, embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiSpam(bot))
