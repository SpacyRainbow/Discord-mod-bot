"""
moderation - kick/ban/mute/warn commands, all recorded to the cases table.

Mute is implemented with Discord's native timeout feature (communication_disabled_until)
rather than a separate mute role - simpler, no role-hierarchy setup required,
and it's what current Discord clients show natively as "Timed Out".
"""

from __future__ import annotations

import datetime

import discord
from discord.ext import commands


def _duration_to_timedelta(duration: str) -> datetime.timedelta:
    """Parses simple durations like '10m', '2h', '1d'. Raises ValueError on
    anything else so the command's error handler can report a clean message."""
    units = {"m": "minutes", "h": "hours", "d": "days"}
    unit = duration[-1].lower()
    if unit not in units:
        raise ValueError(f"Unknown duration unit '{unit}'. Use m/h/d, e.g. 10m, 2h, 1d.")
    amount = int(duration[:-1])
    return datetime.timedelta(**{units[unit]: amount})


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="kick")
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        await member.kick(reason=reason)
        case_id = await self.bot.stores.cases.add(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
        await ctx.send(f"Kicked {member} (case #{case_id}): {reason}")

    @commands.hybrid_command(name="ban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        await member.ban(reason=reason)
        case_id = await self.bot.stores.cases.add(ctx.guild.id, member.id, ctx.author.id, "ban", reason)
        await ctx.send(f"Banned {member} (case #{case_id}): {reason}")

    @commands.hybrid_command(name="mute")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str = "10m",
        *,
        reason: str = "No reason given",
    ):
        try:
            delta = _duration_to_timedelta(duration)
        except ValueError as e:
            await ctx.send(str(e))
            return
        await member.timeout(discord.utils.utcnow() + delta, reason=reason)
        case_id = await self.bot.stores.cases.add(
            ctx.guild.id, member.id, ctx.author.id, "mute", f"{reason} ({duration})"
        )
        await ctx.send(f"Muted {member} for {duration} (case #{case_id}): {reason}")

    @commands.hybrid_command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        await member.timeout(None)
        await ctx.send(f"Unmuted {member}.")

    @commands.hybrid_command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        case_id = await self.bot.stores.cases.add(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
        await ctx.send(f"Warned {member} (case #{case_id}): {reason}")
        try:
            await member.send(f"You were warned in **{ctx.guild.name}**: {reason}")
        except discord.Forbidden:
            pass  # DMs closed - the warning still exists in case history

    @commands.hybrid_command(name="cases")
    @commands.has_permissions(moderate_members=True)
    async def cases(self, ctx: commands.Context, member: discord.Member):
        rows = await self.bot.stores.cases.for_user(ctx.guild.id, member.id)
        if not rows:
            await ctx.send(f"No case history for {member}.")
            return
        lines = [f"#{r[0]} [{r[1]}] {r[2]} (by <@{r[3]}> at {r[4]})" for r in rows]
        embed = discord.Embed(title=f"Case history: {member}", description="\n".join(lines[:20]))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="case")
    @commands.has_permissions(moderate_members=True)
    async def case(self, ctx: commands.Context, case_id: int):
        row = await self.bot.stores.cases.get(ctx.guild.id, case_id)
        if not row:
            await ctx.send(f"No case #{case_id} found.")
            return
        _, user_id, action, reason, moderator_id, created_at = row
        embed = discord.Embed(title=f"Case #{case_id}")
        embed.add_field(name="User", value=f"<@{user_id}>")
        embed.add_field(name="Action", value=action)
        embed.add_field(name="Moderator", value=f"<@{moderator_id}>")
        embed.add_field(name="Reason", value=reason or "No reason given", inline=False)
        embed.add_field(name="Date", value=created_at)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
