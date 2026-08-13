"""
greetings - welcome/leave messages. No dedicated commands - these reuse the
generic /setconfig-/getconfig escape hatch (see status.py) since it's just
a channel ID and a text template each, the same way antispam/automod's
thresholds are configured rather than each getting their own command.

Config keys:
  welcome.channel_id - channel to post to on join (0/unset = disabled)
  welcome.message    - template, default below - {member}/{member_name}/
                       {server}/{member_count} get substituted
  leave.channel_id   - channel to post to on leave (0/unset = disabled)
  leave.message      - template, same placeholders
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

logger = logging.getLogger("bot.modules.greetings")

DEFAULT_WELCOME_MESSAGE = "Welcome {member} to {server}!"
DEFAULT_LEAVE_MESSAGE = "{member_name} has left {server}."


def format_greeting(
    template: str, member_name: str, member_mention: str, guild_name: str, member_count: int
) -> str:
    """Pure: substitutes the documented placeholders in a welcome/leave
    template. Unknown placeholders are left as-is rather than raising, so a
    typo'd template still posts something instead of failing the listener."""
    return (
        template.replace("{member}", member_mention)
        .replace("{member_name}", member_name)
        .replace("{server}", guild_name)
        .replace("{member_count}", str(member_count))
    )


class Greetings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._post(member.guild, member, "welcome", DEFAULT_WELCOME_MESSAGE)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._post(member.guild, member, "leave", DEFAULT_LEAVE_MESSAGE)

    async def _post(
        self, guild: discord.Guild, member: discord.Member, prefix: str, default_message: str
    ) -> None:
        channel_id = await self.bot.stores.config.get_int(guild.id, f"{prefix}.channel_id", 0)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            return
        template = await self.bot.stores.config.get(guild.id, f"{prefix}.message", default_message)
        text = format_greeting(template, str(member), member.mention, guild.name, guild.member_count or 0)
        try:
            # Opts back in to the client-wide AllowedMentions.none() default: the
            # {member} placeholder is member.mention and a welcome that doesn't
            # ping is broken. users=True only, so a mod can't put @everyone in the
            # template and have it fire on every join. (review F6)
            await channel.send(
                text,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=True
                ),
            )
        except discord.Forbidden:
            logger.warning("Missing permission to post a %s message in guild %s", prefix, guild.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Greetings(bot))
