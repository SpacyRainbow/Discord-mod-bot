"""
status - bot info/status commands (ping/uptime/about/help), plus the
generic /setconfig-/getconfig escape hatch for any per-guild config key
that doesn't have its own dedicated command.
"""

from __future__ import annotations

import time

import discord
from discord.ext import commands

GITHUB_URL = "https://github.com/SpacyRainbow/Discord-mod-bot"


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

    @commands.hybrid_command(name="help", description="List everything this bot can do")
    async def help_cmd(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Bot commands",
            description=(
                f"Every command below works as both `!prefix` and `/slash`. "
                f"Full docs, config reference, and setup instructions: [GitHub]({GITHUB_URL})"
            ),
            color=discord.Color.blurple(),
        )
        for cog_name in sorted(self.bot.cogs):
            cog = self.bot.cogs[cog_name]
            cmds = sorted(cog.get_commands(), key=lambda c: c.name)
            if not cmds:
                continue
            embed.add_field(
                name=cog_name,
                value=", ".join(f"`/{c.name}`" for c in cmds),
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="setconfig", description="Set any per-guild config key directly (see the README's config table)"
    )
    @commands.has_permissions(manage_guild=True)
    async def set_config(self, ctx: commands.Context, key: str, *, value: str):
        await self.bot.stores.config.set(ctx.guild.id, key, value)
        await ctx.send(f"Set `{key}` = `{value}`.")

    @commands.hybrid_command(name="getconfig", description="Show the current value of a per-guild config key")
    @commands.has_permissions(manage_guild=True)
    async def get_config(self, ctx: commands.Context, key: str):
        value = await self.bot.stores.config.get(ctx.guild.id, key)
        if value is None:
            await ctx.send(f"`{key}` isn't set (using its default).")
        else:
            await ctx.send(f"`{key}` = `{value}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Status(bot))
