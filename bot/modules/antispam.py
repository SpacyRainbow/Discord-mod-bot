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
from discord.ext import commands

from .logging_module import post_log

logger = logging.getLogger("bot.modules.antispam")


class _UserHistory:
    """Per-user rolling window of recent messages + last content, kept in
    memory (not the DB) since this needs to be checked on every message with
    no I/O latency. Cleared on bot restart, which is fine - spam bursts
    don't survive a restart anyway."""

    __slots__ = ("timestamps", "messages", "last_content", "duplicate_count")

    def __init__(self):
        self.timestamps: deque[float] = deque()
        self.messages: deque[discord.Message] = deque()
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


def group_by_channel(messages: list[discord.Message]) -> dict[int, list[discord.Message]]:
    """Bulk delete is channel-scoped, so a burst spanning multiple channels
    needs one delete call per channel."""
    grouped: dict[int, list[discord.Message]] = {}
    for message in messages:
        grouped.setdefault(message.channel.id, []).append(message)
    return grouped


class AntiSpam(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._history: dict[tuple[int, int], _UserHistory] = defaultdict(_UserHistory)
        self._locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)

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
            history.messages.append(message)

            window = await self.bot.stores.config.get_int(guild_id, "spam.window_seconds", 6)
            max_msgs = await self.bot.stores.config.get_int(guild_id, "spam.max_messages", 5)
            max_dupes = await self.bot.stores.config.get_int(guild_id, "spam.max_duplicates", 3)
            max_mentions = await self.bot.stores.config.get_int(guild_id, "spam.max_mentions", 5)

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
        self, message: discord.Message, reason: str, burst: list[discord.Message]
    ) -> None:
        for channel_id, group in group_by_channel(burst).items():
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                continue
            try:
                # delete_messages() handles 0/1/many messages gracefully on
                # its own (a single message falls back to a normal delete).
                await channel.delete_messages(group, reason=f"Anti-spam: {reason}")
            except discord.HTTPException:
                # Missing permission, already deleted, rate-limited, etc. -
                # none of these should abort the timeout/case-recording that
                # follows.
                logger.warning("Failed to bulk-delete flagged messages in %s", channel, exc_info=True)

        timeout_seconds = await self.bot.stores.config.get_int(
            message.guild.id, "spam.timeout_seconds", 300
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
