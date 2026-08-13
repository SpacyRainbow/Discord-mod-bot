"""
moderation - kick/ban/mute/warn commands, all recorded to the cases table.

Mute has two mechanisms depending on how long it needs to last:
  - <=28 days: Discord's native timeout (communication_disabled_until) -
    simplest, no role-hierarchy setup, shows natively as "Timed Out".
  - >28 days, or indefinite (the default when no duration is given): a
    "Mute" role, auto-created on first use, with a deny-overwrite for
    sending messages/reactions/threads applied to every channel (and any
    channel created afterward). Native timeout has a hard 28-day cap, so
    anything longer has no choice but to be role-based. Indefinite mutes
    are lifted only by /unmute; longer-than-28-day mutes get an entry in
    the mute_expirations table and are auto-lifted by mute_expiry_check.

Config keys:
  moderation.mute_role - role ID of the auto-created "Mute" role
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

import discord
from discord.ext import commands, tasks

from ..durations import parse_duration

logger = logging.getLogger("bot.modules.moderation")

MUTE_ROLE_NAME = "Mute"
MUTE_ROLE_OVERWRITE = discord.PermissionOverwrite(
    send_messages=False,
    send_messages_in_threads=False,
    create_public_threads=False,
    create_private_threads=False,
    add_reactions=False,
)
MUTE_EXPIRY_CHECK_SECONDS = 60
PERMANENT_DURATION_KEYWORDS = {"perm", "permanent", "indefinite", "forever"}
# Past this, the honest word is "permanent" - it also keeps `utcnow() + delta`
# safely inside datetime's representable range (max year 9999), which a
# multi-thousand-year duration could otherwise overflow.
MAX_MUTE_DURATION = datetime.timedelta(days=3650)
# Same cap as mutes, for the same reason (honesty past this point, plus the
# datetime overflow guard) - applies to /ban's optional duration.
MAX_TEMPBAN_DURATION = datetime.timedelta(days=3650)
SLOWMODE_MAX_SECONDS = 21600  # Discord's own cap: 6 hours


def _duration_to_timedelta(duration: str) -> datetime.timedelta:
    """Parses simple durations like '10m', '2h', '1d'. Raises ValueError on
    anything else - a bad unit, a non-numeric amount, a zero/negative
    amount, or a duration long enough to overflow - so the command's error
    handler can report a clean message instead of crashing."""
    if not duration:
        raise ValueError("Give a duration like 10m, 2h, or 1d.")
    units = {"m": "minutes", "h": "hours", "d": "days"}
    unit = duration[-1].lower()
    if unit not in units:
        raise ValueError(f"Unknown duration unit '{unit}'. Use m/h/d, e.g. 10m, 2h, 1d.")
    amount_text = duration[:-1]
    try:
        amount = int(amount_text)
    except ValueError:
        raise ValueError(f"'{amount_text}' isn't a whole number. Use m/h/d, e.g. 10m, 2h, 1d.") from None
    if amount <= 0:
        raise ValueError("Duration must be a positive number, e.g. 10m, 2h, 1d.")
    try:
        delta = datetime.timedelta(**{units[unit]: amount})
    except OverflowError:
        raise ValueError("That duration is too long. Use 'perm' for an indefinite mute instead.") from None
    if delta > MAX_MUTE_DURATION:
        raise ValueError(
            f"That duration is too long (max {MAX_MUTE_DURATION.days} days). "
            "Use 'perm' for an indefinite mute instead."
        )
    return delta


def is_permanent_duration(duration: Optional[str]) -> bool:
    """No duration given, or an explicit perm/permanent/indefinite/forever
    keyword, both mean "mute until manually /unmute'd"."""
    return duration is None or duration.strip().lower() in PERMANENT_DURATION_KEYWORDS


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.mute_expiry_check.start()

    def cog_unload(self):
        self.mute_expiry_check.cancel()

    async def cog_load(self) -> None:
        self.bot.scheduler_handlers["tempban_unban"] = self._handle_tempban_unban

    async def _handle_tempban_unban(self, guild_id: int, payload: dict) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        user_id = payload["user_id"]
        try:
            await guild.unban(discord.Object(id=user_id), reason="Temp-ban expired")
        except discord.NotFound:
            return  # already unbanned manually - nothing to do
        except discord.Forbidden:
            logger.warning("Missing permission to lift expired temp-ban in guild %s", guild_id)
            return
        try:
            await self.bot.stores.cases.add(
                guild_id, user_id, self.bot.user.id, "unban", "Temp-ban expired"
            )
        except RuntimeError:
            pass  # DB unavailable - the unban itself already happened, degrade gracefully

    # ---- mute role ----

    async def get_mute_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        role_id = await self.bot.stores.config.get_int(guild.id, "moderation.mute_role", 0)
        if not role_id:
            return None
        return guild.get_role(role_id)

    async def ensure_mute_role(self, guild: discord.Guild) -> discord.Role:
        role = await self.get_mute_role(guild)
        if role is None:
            role = await guild.create_role(
                name=MUTE_ROLE_NAME,
                permissions=discord.Permissions.none(),
                reason="Auto-created mute role",
            )
            await self.bot.stores.config.set(guild.id, "moderation.mute_role", str(role.id))
            # Backfill only on creation. This used to run unconditionally, so
            # every mute issued one API call per text channel (200 channels =
            # 200 requests, rate-limited, and _mute_with_role awaits all of it
            # *before* the mute lands). on_guild_channel_create keeps channels
            # made later in sync, and /syncmuterole is the manual repair path.
            # (review F15)
            await self._sync_mute_role_channels(guild, role)
        return role

    async def _sync_mute_role_channels(self, guild: discord.Guild, role: discord.Role) -> int:
        """Applies MUTE_ROLE_OVERWRITE to every text channel. One API call per
        channel, so this is a one-time/manual operation, never on the mute hot
        path. Returns how many channels were successfully updated."""
        synced = 0
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(
                    role, overwrite=MUTE_ROLE_OVERWRITE, reason="Mute role setup"
                )
            except discord.Forbidden:
                continue
            synced += 1
        return synced

    @commands.hybrid_command(
        name="syncmuterole",
        description="Re-apply the mute role's permission overwrite to every text channel",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def sync_mute_role(self, ctx: commands.Context):
        """Manual repair for the one-time backfill that ensure_mute_role now
        only does at creation time. (review F15)"""
        role = await self.get_mute_role(ctx.guild)
        if role is None:
            await ctx.send("There's no mute role yet - it's created the first time someone is muted.")
            return
        await ctx.defer()
        synced = await self._sync_mute_role_channels(ctx.guild, role)
        total = len(ctx.guild.text_channels)
        await ctx.send(f"Synced `{role.name}` overwrites in {synced}/{total} text channels.")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.TextChannel):
            return
        role = await self.get_mute_role(channel.guild)
        if role is None:
            return  # never force role creation just because a channel was made
        try:
            await channel.set_permissions(role, overwrite=MUTE_ROLE_OVERWRITE, reason="Mute role setup")
        except discord.Forbidden:
            pass

    @tasks.loop(seconds=MUTE_EXPIRY_CHECK_SECONDS)
    async def mute_expiry_check(self):
        # tasks.loop only auto-restarts on network errors; anything else would stop
        # this loop permanently, so nothing may escape the body. (review F4)
        try:
            now_iso = discord.utils.utcnow().isoformat()
            for guild_id, user_id in await self.bot.stores.mutes.due(now_iso):
                # Per-row isolation: one guild we can't reach must not skip the rest.
                try:
                    guild = self.bot.get_guild(guild_id)
                    if guild is None:
                        await self.bot.stores.mutes.clear(guild_id, user_id)
                        continue
                    member = guild.get_member(user_id)
                    role = await self.get_mute_role(guild)
                    if member is not None and role is not None:
                        try:
                            await member.remove_roles(role, reason="Mute expired")
                        except discord.Forbidden:
                            logger.warning(
                                "Missing permission to lift expired mute in guild %s", guild_id
                            )
                    await self.bot.stores.mutes.clear(guild_id, user_id)
                except Exception:
                    logger.exception(
                        "Failed to lift expired mute for user %s in guild %s", user_id, guild_id
                    )
        except Exception:
            logger.exception("mute_expiry_check iteration failed")

    @mute_expiry_check.before_loop
    async def before_mute_expiry_check(self):
        await self.bot.wait_until_ready()

    async def _mute_with_role(
        self,
        ctx: commands.Context,
        member: discord.Member,
        delta: Optional[datetime.timedelta],
        reason: str,
    ) -> None:
        role = await self.ensure_mute_role(ctx.guild)
        if not await self._try_action(ctx, member, member.add_roles(role, reason=f"Mute: {reason}")):
            return
        # The mute has already landed on Discord by this point. Every write
        # below raises RuntimeError when the DB is unavailable (phase 1's write
        # contract), and letting that escape showed the moderator a generic
        # "Something went wrong" while a *timed* mute had silently become
        # permanent. Always confirm the mute that really happened, and say
        # plainly what wasn't recorded or scheduled. Note there's no case_id to
        # interpolate on the failure paths. (review F16)
        if delta is None:
            try:
                await self.bot.stores.mutes.clear(ctx.guild.id, member.id)
                case_id = await self.bot.stores.cases.add(
                    ctx.guild.id, member.id, ctx.author.id, "mute", f"{reason} (indefinite)"
                )
            except RuntimeError:
                await ctx.send(
                    f"Muted {member} indefinitely: {reason}\n"
                    "The database is unavailable, so this wasn't recorded in the case history."
                )
                return
            await ctx.send(f"Muted {member} indefinitely (case #{case_id}): {reason}")
            return

        expires_at = discord.utils.utcnow() + delta
        until = discord.utils.format_dt(expires_at, "f")
        try:
            await self.bot.stores.mutes.schedule(ctx.guild.id, member.id, expires_at.isoformat())
        except RuntimeError:
            await ctx.send(
                f"Muted {member}: {reason}\n"
                "Couldn't schedule the automatic unmute (database unavailable) - this mute will "
                f"NOT lift on its own at {until}, use `/unmute` to lift it."
            )
            return
        try:
            case_id = await self.bot.stores.cases.add(
                ctx.guild.id, member.id, ctx.author.id, "mute", f"{reason} (until {expires_at.isoformat()})"
            )
        except RuntimeError:
            await ctx.send(
                f"Muted {member} until {until}: {reason}\n"
                "The database is unavailable, so this wasn't recorded in the case history."
            )
            return
        await ctx.send(f"Muted {member} until {until} (case #{case_id}): {reason}")

    # ---- commands ----

    async def _try_action(self, ctx: commands.Context, member: discord.Member, action) -> bool:
        """Runs a moderation action (kick/ban/timeout/add_roles/...) and
        reports a clear message on discord.Forbidden - either a missing
        permission bit, or (just as common) the bot's own role sitting
        below the target's highest role - instead of crashing to the
        generic error handler."""
        try:
            await action
            return True
        except discord.Forbidden:
            await ctx.send(
                f"I don't have permission to do that to {member}. Check that my role is "
                "above their highest role, and that I have the permission for this action."
            )
            return False

    @commands.hybrid_command(name="kick")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        if not await self._try_action(ctx, member, member.kick(reason=reason)):
            return
        case_id = await self.bot.stores.cases.add(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
        await ctx.send(f"Kicked {member} (case #{case_id}): {reason}")

    @commands.hybrid_command(
        name="ban",
        description="Ban a member - give a duration (e.g. 7d) for a temp-ban, or omit for permanent",
    )
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: Optional[str] = None,
        *,
        reason: str = "No reason given",
    ):
        if not await self._try_action(ctx, member, member.ban(reason=reason)):
            return
        case_id = await self.bot.stores.cases.add(ctx.guild.id, member.id, ctx.author.id, "ban", reason)

        if not duration:
            await ctx.send(f"Banned {member} (case #{case_id}): {reason}")
            return

        try:
            delta = parse_duration(duration, max_duration=MAX_TEMPBAN_DURATION)
        except ValueError as e:
            await ctx.send(
                f"Banned {member} (case #{case_id}): {reason}\n"
                f"{e} This ban is permanent since the duration wasn't understood - use `/unban` to lift it."
            )
            return

        unban_at = discord.utils.utcnow() + delta
        try:
            # Known limitation: re-banning someone who already has a
            # pending temp-ban schedules a second, independent auto-unban
            # rather than replacing the first - whichever one is due
            # first will unban them even if a later ban meant to be
            # permanent or longer. Rare in practice (re-banning an
            # already-banned member), not worth a ban-generation-tracking
            # system for.
            await self.bot.stores.scheduled.add(
                ctx.guild.id, "tempban_unban", {"user_id": member.id}, unban_at
            )
        except RuntimeError:
            await ctx.send(
                f"Banned {member} (case #{case_id}): {reason}\n"
                "Couldn't schedule the auto-unban (database unavailable) - this ban is permanent "
                "until manually `/unban`'d."
            )
            return

        until = discord.utils.format_dt(unban_at, "f")
        await ctx.send(f"Banned {member} until {until} (case #{case_id}): {reason}")

    @commands.hybrid_command(name="unban")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason given"):
        try:
            await ctx.guild.unban(user, reason=reason)
        except discord.NotFound:
            await ctx.send(f"{user} isn't banned.")
            return
        except discord.Forbidden:
            await ctx.send("I don't have permission to do that.")
            return
        case_id = await self.bot.stores.cases.add(ctx.guild.id, user.id, ctx.author.id, "unban", reason)
        await ctx.send(f"Unbanned {user} (case #{case_id}): {reason}")

    @commands.hybrid_command(
        name="purge", description="Delete the last N messages in this channel (optionally, only one member's)"
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, amount: int, member: Optional[discord.Member] = None):
        if not 1 <= amount <= 100:
            await ctx.send("Give an amount between 1 and 100.")
            return

        await ctx.defer(ephemeral=True)

        def check(message: discord.Message) -> bool:
            return member is None or message.author.id == member.id

        try:
            deleted = await ctx.channel.purge(limit=amount, check=check)
        except discord.Forbidden:
            await ctx.send("I don't have permission to delete messages here.")
            return
        except discord.HTTPException as e:
            await ctx.send(f"Couldn't delete messages: {e}")
            return
        await ctx.send(f"Deleted {len(deleted)} message(s).", ephemeral=True)

    @commands.hybrid_command(name="slowmode", description="Set this (or another) channel's slowmode delay")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(
        self, ctx: commands.Context, seconds: int, channel: Optional[discord.TextChannel] = None
    ):
        if not 0 <= seconds <= SLOWMODE_MAX_SECONDS:
            await ctx.send(f"Give a value between 0 (off) and {SLOWMODE_MAX_SECONDS} (6 hours).")
            return
        target = channel or ctx.channel
        try:
            await target.edit(slowmode_delay=seconds, reason=f"Slowmode set by {ctx.author}")
        except discord.Forbidden:
            await ctx.send("I don't have permission to edit that channel.")
            return
        if seconds == 0:
            await ctx.send(f"Slowmode turned off in {target.mention}.")
        else:
            await ctx.send(f"Slowmode set to {seconds}s in {target.mention}.")

    @commands.hybrid_command(
        name="lockdown",
        description="Toggle blocking @everyone from sending messages in this (or another) channel",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def lockdown(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        target = channel or ctx.channel
        everyone = ctx.guild.default_role
        existing = await self.bot.stores.channel_locks.get(ctx.guild.id, target.id)

        if existing is not None:
            overwrite = target.overwrites_for(everyone)
            overwrite.send_messages = self.bot.stores.channel_locks.decode(existing)
            try:
                await target.set_permissions(
                    everyone, overwrite=overwrite, reason=f"Lockdown lifted by {ctx.author}"
                )
            except discord.Forbidden:
                await ctx.send("I don't have permission to edit that channel's permissions.")
                return
            await self.bot.stores.channel_locks.clear(ctx.guild.id, target.id)
            await ctx.send(f"{target.mention} is no longer locked down.")
            return

        overwrite = target.overwrites_for(everyone)
        previous = overwrite.send_messages
        overwrite.send_messages = False
        try:
            await target.set_permissions(everyone, overwrite=overwrite, reason=f"Lockdown by {ctx.author}")
        except discord.Forbidden:
            await ctx.send("I don't have permission to edit that channel's permissions.")
            return
        await self.bot.stores.channel_locks.set(ctx.guild.id, target.id, previous)
        await ctx.send(f"{target.mention} is now locked down - @everyone can't send messages.")

    @commands.hybrid_command(
        name="mute",
        description="Mute a member - omit duration (or use 'perm') for indefinite",
    )
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: Optional[str] = None,
        *,
        reason: str = "No reason given",
    ):
        if is_permanent_duration(duration):
            await self._mute_with_role(ctx, member, None, reason)
            return

        try:
            delta = _duration_to_timedelta(duration)
        except ValueError as e:
            await ctx.send(str(e))
            return

        if delta > datetime.timedelta(days=28):
            await self._mute_with_role(ctx, member, delta, reason)
            return

        if not await self._try_action(
            ctx, member, member.timeout(discord.utils.utcnow() + delta, reason=reason)
        ):
            return
        case_id = await self.bot.stores.cases.add(
            ctx.guild.id, member.id, ctx.author.id, "mute", f"{reason} ({duration})"
        )
        await ctx.send(f"Muted {member} for {duration} (case #{case_id}): {reason}")

    @commands.hybrid_command(name="unmute")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        if not await self._try_action(ctx, member, member.timeout(None)):
            return
        role = await self.get_mute_role(ctx.guild)
        if role is not None:
            try:
                await member.remove_roles(role, reason="Unmuted")
            except discord.Forbidden:
                pass
        await self.bot.stores.mutes.clear(ctx.guild.id, member.id)
        await ctx.send(f"Unmuted {member}.")

    @commands.hybrid_command(name="warn")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        case_id = await self.bot.stores.cases.add(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
        await ctx.send(f"Warned {member} (case #{case_id}): {reason}")
        try:
            await member.send(f"You were warned in **{ctx.guild.name}**: {reason}")
        except discord.Forbidden:
            pass  # DMs closed - the warning still exists in case history

    @commands.hybrid_command(name="cases")
    @commands.guild_only()
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
    @commands.guild_only()
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

    @commands.hybrid_command(name="editcase", description="Change a case's recorded reason")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def editcase(self, ctx: commands.Context, case_id: int, *, reason: str):
        row = await self.bot.stores.cases.get(ctx.guild.id, case_id)
        if not row:
            await ctx.send(f"No case #{case_id} found.")
            return
        await self.bot.stores.cases.update_reason(ctx.guild.id, case_id, reason)
        await ctx.send(f"Updated case #{case_id}'s reason to: {reason}")

    @commands.hybrid_command(name="deletecase", description="Delete a case from the case history")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def deletecase(self, ctx: commands.Context, case_id: int):
        row = await self.bot.stores.cases.get(ctx.guild.id, case_id)
        if not row:
            await ctx.send(f"No case #{case_id} found.")
            return
        await self.bot.stores.cases.delete(ctx.guild.id, case_id)
        await ctx.send(f"Deleted case #{case_id}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
