"""
bored - watches a configured channel and posts something once it's been
quiet for a while, to nudge conversation back to life.

Config keys:
  bored.channel      - channel ID to watch (0/unset = disabled)
  bored.idle_seconds - seconds of silence before firing, default 1800 (30 min)
  bored.message      - text to post when triggered, default a generic nudge
"""

from __future__ import annotations

import time

import discord
from discord.ext import commands, tasks

DEFAULT_MESSAGE = "...it's quiet in here. Too quiet."


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
        if message.guild is None:
            return
        self._last_activity[message.channel.id] = time.monotonic()
        self._fired[message.channel.id] = False

    @tasks.loop(seconds=60)
    async def check_loop(self):
        for guild in self.bot.guilds:
            channel_id = await self.bot.stores.config.get_int(guild.id, "bored.channel", 0)
            if not channel_id:
                continue
            idle_seconds = await self.bot.stores.config.get_int(guild.id, "bored.idle_seconds", 1800)
            last = self._last_activity.get(channel_id, time.monotonic())
            if self._fired.get(channel_id):
                continue
            if time.monotonic() - last >= idle_seconds:
                channel = guild.get_channel(channel_id)
                if channel is None:
                    continue
                message_text = await self.bot.stores.config.get(guild.id, "bored.message", DEFAULT_MESSAGE)
                try:
                    await channel.send(message_text)
                except discord.Forbidden:
                    pass
                self._fired[channel_id] = True

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
