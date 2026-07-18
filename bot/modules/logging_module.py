"""
logging_module - message edit/delete logging, join/leave logging, and mod-log.

Config keys:
  logging.channel        - channel ID all logs are posted to
  logging.edits          - "true"/"false", default true
  logging.deletes        - "true"/"false", default true
  logging.joins          - "true"/"false", default true
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

logger = logging.getLogger("bot.modules.logging")


async def post_log(bot: commands.Bot, guild: discord.Guild, embed: discord.Embed) -> None:
    """Shared by moderation.py and antispam.py to post mod-log entries.
    Silently does nothing if no log channel is configured - this is not
    itself an error worth surfacing to the user."""
    channel_id = await bot.stores.config.get_int(guild.id, "logging.channel", 0)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        logger.warning("Missing permission to post in log channel %s (guild %s)", channel_id, guild.id)


class LoggingModule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        enabled = await self.bot.stores.config.get_bool(message.guild.id, "logging.deletes", True)
        if not enabled:
            return
        embed = discord.Embed(
            title="Message deleted",
            description=message.content or "*(no text content)*",
            color=discord.Color.red(),
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Channel", value=message.channel.mention)
        await post_log(self.bot, message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.guild is None or before.content == after.content:
            return
        enabled = await self.bot.stores.config.get_bool(before.guild.id, "logging.edits", True)
        if not enabled:
            return
        embed = discord.Embed(title="Message edited", color=discord.Color.gold())
        embed.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        embed.add_field(name="Before", value=before.content or "*(empty)*", inline=False)
        embed.add_field(name="After", value=after.content or "*(empty)*", inline=False)
        embed.add_field(name="Channel", value=before.channel.mention)
        await post_log(self.bot, before.guild, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        enabled = await self.bot.stores.config.get_bool(member.guild.id, "logging.joins", True)
        if not enabled:
            return
        embed = discord.Embed(
            title="Member joined",
            description=f"{member.mention} ({member})",
            color=discord.Color.green(),
        )
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"))
        await post_log(self.bot, member.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        enabled = await self.bot.stores.config.get_bool(member.guild.id, "logging.joins", True)
        if not enabled:
            return
        embed = discord.Embed(
            title="Member left",
            description=f"{member.mention} ({member})",
            color=discord.Color.orange(),
        )
        await post_log(self.bot, member.guild, embed)

    @commands.command(name="setlogchannel")
    @commands.has_permissions(manage_guild=True)
    async def set_log_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel mod-log, edits/deletes, and join/leave events post to."""
        await self.bot.stores.config.set(ctx.guild.id, "logging.channel", str(channel.id))
        await ctx.send(f"Logging to {channel.mention} from now on.")


async def setup(bot: commands.Bot):
    await bot.add_cog(LoggingModule(bot))
