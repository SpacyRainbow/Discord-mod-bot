"""
status - bot info/status commands (ping/uptime/about/help), plus the
generic /setconfig-/getconfig escape hatch for any per-guild config key
that doesn't have its own dedicated command.
"""

from __future__ import annotations

import time

import discord
from discord.ext import commands

from bot.modules.updater import UpdateStatus, describe_status

GITHUB_URL = "https://github.com/SpacyRainbow/Discord-mod-bot"
# Bumped by hand when shipping a meaningful set of changes - there's no
# packaging/release process here to derive this from automatically, and the
# Docker image only copies bot/, not .git, so a git-log-based date wouldn't
# work in production anyway.
VERSION = "1.0.1"
LAST_UPDATED = "2026-07-22"


class _ApplyUpdateButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Apply update", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need Manage Server to apply an update.", ephemeral=True
            )
            return
        updater_cog = interaction.client.get_cog("Updater")
        if updater_cog is None:
            await interaction.response.send_message("Updater isn't loaded.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Applying update - restarting now, I'll be back in a few seconds.", ephemeral=True
        )
        await updater_cog.apply_update()


class _UpdateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(_ApplyUpdateButton())


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
        updater_cog = self.bot.get_cog("Updater")
        update_status = updater_cog.status if updater_cog is not None else UpdateStatus(checked=False)

        embed = discord.Embed(title="Bot status")
        embed.add_field(name="Version", value=VERSION)
        embed.add_field(name="Last updated", value=LAST_UPDATED)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms")
        embed.add_field(name="Database", value="connected" if self.bot.db.available else "degraded (no DB)")
        embed.add_field(name="Modules loaded", value=str(len(self.bot.cogs)), inline=False)
        embed.add_field(name="Updates", value=describe_status(update_status), inline=False)

        view = _UpdateView() if update_status.available else None
        await ctx.send(embed=embed, view=view)

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
