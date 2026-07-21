"""
raid - join-raid protection: a minimum account-age gate, and rolling
join-rate burst detection, plus a manual /raidmode toggle. Both automatic
checks are off by default (0 = disabled) - a fresh fork of this bot doesn't
suddenly start kicking new members or alerting on ordinary traffic until
you explicitly configure a threshold.

Config keys:
  raid.min_account_age_hours - kick on join if the account is younger than
                                this many hours (0 = disabled, default 0)
  raid.join_threshold        - joins within raid.join_window_seconds that
                                count as a burst (0 = disabled, default 0)
  raid.join_window_seconds   - rolling window size in seconds, default 30
  raid.auto_lockdown         - "true"/"false" - bump verification level
                                automatically when a burst is detected,
                                default false (alert-only otherwise)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

import discord
from discord.ext import commands

from .logging_module import post_log

logger = logging.getLogger("bot.modules.raid")


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._join_times: dict[int, deque[float]] = defaultdict(deque)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        min_age_hours = await self.bot.stores.config.get_int(guild.id, "raid.min_account_age_hours", 0)
        if min_age_hours > 0:
            account_age = discord.utils.utcnow() - member.created_at
            if account_age.total_seconds() < min_age_hours * 3600:
                try:
                    await member.kick(reason=f"Account younger than {min_age_hours}h (raid protection)")
                except discord.Forbidden:
                    logger.warning("Missing permission to kick underage-account join in %s", guild.id)
                else:
                    created = discord.utils.format_dt(member.created_at, "R")
                    embed = discord.Embed(
                        title="Raid protection: underage account kicked",
                        description=f"{member} (account created {created})",
                        color=discord.Color.orange(),
                    )
                    await post_log(self.bot, guild, embed)
                    return  # don't also count a kicked join toward the burst detector

        threshold = await self.bot.stores.config.get_int(guild.id, "raid.join_threshold", 0)
        if threshold <= 0:
            return
        window_seconds = await self.bot.stores.config.get_int(guild.id, "raid.join_window_seconds", 30)

        times = self._join_times[guild.id]
        now = time.monotonic()
        times.append(now)
        while times and now - times[0] > window_seconds:
            times.popleft()

        if len(times) < threshold:
            return

        times.clear()  # one alert per burst, not one per member past the threshold
        embed = discord.Embed(
            title="Possible raid detected",
            description=f"{threshold}+ members joined within {window_seconds}s.",
            color=discord.Color.red(),
        )
        await post_log(self.bot, guild, embed)

        auto_lockdown = await self.bot.stores.config.get_bool(guild.id, "raid.auto_lockdown", False)
        if auto_lockdown:
            await self._enable_lockdown(guild)

    async def _enable_lockdown(self, guild: discord.Guild) -> None:
        if guild.verification_level == discord.VerificationLevel.highest:
            return  # already at max, nothing to raise or remember
        current = await self.bot.stores.config.get(guild.id, "raid.previous_verification_level")
        if not current:  # None (never stored) or "" (cleared by a previous raidmode off)
            await self.bot.stores.config.set(
                guild.id, "raid.previous_verification_level", str(guild.verification_level.value)
            )
        try:
            await guild.edit(
                verification_level=discord.VerificationLevel.highest, reason="Raid protection auto-lockdown"
            )
        except discord.Forbidden:
            logger.warning("Missing permission to raise verification level in guild %s", guild.id)

    @commands.hybrid_command(
        name="raidmode", description="Toggle a temporary verification-level lockdown (on/off)"
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_guild=True)
    async def raidmode(self, ctx: commands.Context, state: str):
        state = state.strip().lower()
        if state not in ("on", "off"):
            await ctx.send("Give `on` or `off`.")
            return

        if state == "on":
            await self._enable_lockdown(ctx.guild)
            await ctx.send("Raid mode on - verification level raised to the maximum.")
            return

        stored = await self.bot.stores.config.get(ctx.guild.id, "raid.previous_verification_level")
        previous = discord.VerificationLevel(int(stored)) if stored else discord.VerificationLevel.medium
        try:
            await ctx.guild.edit(verification_level=previous, reason=f"Raid mode lifted by {ctx.author}")
        except discord.Forbidden:
            await ctx.send("I don't have permission to change the verification level.")
            return
        await self.bot.stores.config.set(ctx.guild.id, "raid.previous_verification_level", "")
        await ctx.send(f"Raid mode off - verification level restored to {previous.name}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Raid(bot))
