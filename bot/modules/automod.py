"""
automod - content-based filtering (as opposed to antispam.py, which is
behavior-based: rate/frequency). Sweetie Bot's "filtermodule" equivalent,
plus Dyno-style invite blocking and caps spam.

Config keys:
  automod.block_invites   - "true"/"false", default true
  automod.caps_threshold  - int 0-100, percent caps that triggers a flag,
                            default 70. Set to 0 to disable caps checking.
  automod.caps_minlen     - minimum message length before caps checking
                            applies (avoids false positives on "LOL"), default 10

Banned words themselves live in the banned_words table (via FilterStore),
managed with !filter add / !filter remove / !filter list.
"""

from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands

from .logging_module import post_log

logger = logging.getLogger("bot.modules.automod")

INVITE_RE = re.compile(r"(discord\.gg|discord(app)?\.com/invite)/([A-Za-z0-9-]+)", re.IGNORECASE)


def contains_invite_link(content: str) -> bool:
    return bool(INVITE_RE.search(content))


def caps_percentage(content: str) -> float:
    """Percentage of alphabetic characters that are uppercase. Returns 0 for
    strings with no letters, to avoid flagging emoji/punctuation-only messages."""
    letters = [c for c in content if c.isalpha()]
    if not letters:
        return 0.0
    upper = sum(1 for c in letters if c.isupper())
    return (upper / len(letters)) * 100


def contains_banned_word(content: str, banned_words: list[str]) -> str | None:
    """Returns the matched word, or None. Uses word boundaries so 'ass' in
    'class' doesn't match, matching how Sweetie Bot's filter help page
    describes its regex-based approach."""
    lowered = content.lower()
    for word in banned_words:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return word
    return None


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if message.author.guild_permissions.manage_messages:
            return  # don't automod moderators

        guild_id = message.guild.id

        block_invites = await self.bot.stores.config.get_bool(guild_id, "automod.block_invites", True)
        if block_invites and contains_invite_link(message.content):
            await self._take_action(message, "posted an invite link")
            return

        banned_words = await self.bot.stores.filters.all(guild_id)
        if banned_words:
            hit = contains_banned_word(message.content, banned_words)
            if hit:
                await self._take_action(message, f"used a filtered word ({hit})")
                return

        threshold = await self.bot.stores.config.get_int(guild_id, "automod.caps_threshold", 70)
        minlen = await self.bot.stores.config.get_int(guild_id, "automod.caps_minlen", 10)
        if threshold and len(message.content) >= minlen:
            if caps_percentage(message.content) >= threshold:
                await self._take_action(message, "excessive caps")
                return

    async def _take_action(self, message: discord.Message, reason: str) -> None:
        try:
            await message.delete()
        except discord.Forbidden:
            logger.warning("Missing permission to delete message in %s", message.channel)
            return
        embed = discord.Embed(
            title="Automod action",
            description=f"Deleted a message from {message.author.mention}: {reason}",
            color=discord.Color.red(),
        )
        await post_log(self.bot, message.guild, embed)

    @commands.hybrid_group(name="filter", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def filter_group(self, ctx: commands.Context):
        words = await self.bot.stores.filters.all(ctx.guild.id)
        if not words:
            await ctx.send("No filtered words configured.")
            return
        await ctx.send(f"Filtered words: {', '.join(words)}")

    @filter_group.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def filter_add(self, ctx: commands.Context, *, word: str):
        await self.bot.stores.filters.add(ctx.guild.id, word)
        await ctx.send(f"Added `{word}` to the filter list.")

    @filter_group.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def filter_remove(self, ctx: commands.Context, *, word: str):
        await self.bot.stores.filters.remove(ctx.guild.id, word)
        await ctx.send(f"Removed `{word}` from the filter list.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
