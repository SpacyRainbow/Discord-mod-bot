from __future__ import annotations

import time

import discord
from discord.ext import commands


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="ping")
    async def ping(self, ctx: commands.Context):
        await ctx.send(f"Pong. {round(self.bot.latency * 1000)}ms")

    @commands.hybrid_command(name="uptime")
    async def uptime(self, ctx: commands.Context):
        if self.bot.start_time is None:
            await ctx.send("Just started.")
            return
        seconds = int(time.time() - self.bot.start_time)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        await ctx.send(f"Up for {hours}h {minutes}m {secs}s")

    @commands.hybrid_command(name="about")
    async def about(self, ctx: commands.Context):
        embed = discord.Embed(title="Bot status")
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms")
        embed.add_field(name="Database", value="connected" if self.bot.db.available else "degraded (no DB)")
        embed.add_field(name="Modules loaded", value=str(len(self.bot.cogs)), inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Status(bot))
