"""
bored - watches a configured channel and posts something once it's been
quiet for a while, to nudge conversation back to life.

Config keys:
  bored.channel      - channel ID to watch (0/unset = disabled)
  bored.idle_seconds - seconds of silence before firing, default 1800 (30 min)
  bored.message      - text to post when triggered, default a generic nudge
"""

from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands, tasks

logger = logging.getLogger("bot.modules.bored")

DEFAULT_MESSAGE = "...it's quiet in here. Too quiet."

# Channels not seen for this long are dropped from the activity/latch dicts,
# which otherwise held one entry per channel that has ever had a message.
# (review F10)
ACTIVITY_MAX_AGE_SECONDS = 86400  # 24h


class Bored(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_activity: dict[int, float] = {}
        self._fired: dict[int, bool] = {}
        self.check_loop.start()

    def cog_unload(self):
        self.check_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Without the author.bot guard the nudge check_loop posts re-enters here,
        # resets _last_activity and clears the _fired latch that exists solely to
        # stop repeat nudges - so a silent channel gets nudged forever. (review F5)
        if message.author.bot or message.guild is None:
            return
        self._last_activity[message.channel.id] = time.monotonic()
        self._fired[message.channel.id] = False

    @tasks.loop(seconds=60)
    async def check_loop(self):
        # tasks.loop only auto-restarts on network errors; anything else would stop
        # this loop permanently, so nothing may escape the body. (review F4)
        try:
            watched: set[int] = set()
            for guild in self.bot.guilds:
                channel_id = await self.bot.stores.config.get_int(guild.id, "bored.channel", 0)
                if not channel_id:
                    continue
                watched.add(channel_id)
                # Floor of 60s: 0 would nudge on every single check. (review F14)
                idle_seconds = await self.bot.stores.config.get_int(
                    guild.id, "bored.idle_seconds", 1800, minimum=60, maximum=86400
                )
                last = self._last_activity.get(channel_id, time.monotonic())
                if self._fired.get(channel_id):
                    continue
                if time.monotonic() - last >= idle_seconds:
                    channel = guild.get_channel(channel_id)
                    if channel is None:
                        continue
                    message_text = await self.bot.stores.config.get(
                        guild.id, "bored.message", DEFAULT_MESSAGE
                    )
                    try:
                        await channel.send(message_text)
                    except discord.Forbidden:
                        pass
                    self._fired[channel_id] = True
            self._sweep_activity(time.monotonic(), watched)
        except Exception:
            logger.exception("bored check_loop iteration failed")

    def _sweep_activity(
        self, now: float, watched: set[int], max_age: float = ACTIVITY_MAX_AGE_SECONDS
    ) -> int:
        """Drops channels not seen for `max_age` from both dicts, which
        otherwise grew one entry per channel that has ever had a message.
        Currently-watched bored channels are always kept: evicting one would
        also clear its _fired latch and re-arm the nudge, undoing F5. (review F10)"""
        dropped = 0
        for channel_id in list(self._last_activity):
            if channel_id in watched:
                continue
            if now - self._last_activity[channel_id] <= max_age:
                continue
            self._last_activity.pop(channel_id, None)
            self._fired.pop(channel_id, None)
            dropped += 1
        return dropped

    @check_loop.before_loop
    async def before_check_loop(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="setboredchannel")
    @commands.has_permissions(manage_guild=True)
    async def set_bored_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.bot.stores.config.set(ctx.guild.id, "bored.channel", str(channel.id))
        await ctx.send(f"I'll speak up in {channel.mention} when it goes quiet.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Bored(bot))
