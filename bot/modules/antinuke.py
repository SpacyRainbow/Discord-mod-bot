"""
antinuke - detects one non-owner member deleting channels/roles or banning
members in a burst (the signature of a compromised mod/admin account doing
deliberate damage) and strips their dangerous-permission roles, alerting
the mod-log. Off by default (antinuke.enabled) given how large a mistake
here could be - this is the one module in this bot that takes an automatic
action against a real staff member's permissions, not just a rank-and-file
member's messages.

Deliberately narrow in scope: only channel deletes, role deletes, and bans
are watched. Message deletion (e.g. via /purge) is NOT counted here, so a
legitimate mod bulk-cleaning a channel is never mistaken for an attack.

Config keys:
  antinuke.enabled          - "true"/"false", default false
  antinuke.action_threshold - destructive actions within the window before
                              punishing, default 3
  antinuke.window_seconds   - rolling window size in seconds, default 30

Attribution comes from the audit log (needs the bot's own View Audit Log
permission) - matched by the deleted/banned object's ID against the most
recent matching entries. If the audit log can't be read, or no matching
entry is found in time, the event is silently not counted rather than
guessed at.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Optional, Union

import discord
from discord.ext import commands, tasks

from .logging_module import post_log

logger = logging.getLogger("bot.modules.antinuke")

# 3600 is the ceiling antinuke.window_seconds is clamped to, so no live window
# can outlive a swept entry. (review F10)
ACTIONS_MAX_AGE_SECONDS = 3600
ACTIONS_SWEEP_MINUTES = 10

DANGEROUS_PERMISSIONS = (
    "ban_members",
    "kick_members",
    "manage_channels",
    "manage_roles",
    "manage_guild",
)


class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._actions: dict[tuple[int, int], deque[float]] = defaultdict(deque)

    async def cog_load(self) -> None:
        self.sweep_actions.start()

    def cog_unload(self):
        self.sweep_actions.cancel()

    def _sweep(self, now: float, max_age: float = ACTIONS_MAX_AGE_SECONDS) -> int:
        """Drops the action-time deque for any (guild, executor) with no recent
        destructive actions. The dict was keyed by every staff member who has
        ever deleted a channel and nothing was ever removed. (review F10)"""
        dropped = 0
        for key in list(self._actions):
            times = self._actions[key]
            if times and now - times[-1] <= max_age:
                continue
            del self._actions[key]
            dropped += 1
        return dropped

    @tasks.loop(minutes=ACTIONS_SWEEP_MINUTES)
    async def sweep_actions(self):
        # tasks.loop only auto-restarts on network errors; anything else would stop
        # this loop permanently, so nothing may escape the body. (review F4)
        try:
            self._sweep(time.monotonic())
        except Exception:
            logger.exception("antinuke sweep_actions iteration failed")

    @sweep_actions.before_loop
    async def before_sweep_actions(self):
        await self.bot.wait_until_ready()

    async def _find_executor(
        self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int
    ) -> Optional[Union[discord.Member, discord.User]]:
        try:
            async for entry in guild.audit_logs(action=action, limit=5):
                target = entry.target
                if target is not None and getattr(target, "id", None) == target_id and entry.user:
                    return entry.user
        except discord.Forbidden:
            logger.warning("Missing View Audit Log permission in guild %s", guild.id)
        return None

    async def _handle_destructive_action(
        self, guild: discord.Guild, executor: Optional[Union[discord.Member, discord.User]], description: str
    ) -> None:
        if executor is None or executor.bot or executor.id == guild.owner_id:
            return
        if not await self.bot.stores.config.get_bool(guild.id, "antinuke.enabled", False):
            return

        # Bounded so a stored negative/absurd value can't make anti-nuke fire
        # on a single action or never fire at all. (review F14)
        threshold = await self.bot.stores.config.get_int(
            guild.id, "antinuke.action_threshold", 3, minimum=1, maximum=1000
        )
        window_seconds = await self.bot.stores.config.get_int(
            guild.id, "antinuke.window_seconds", 30, minimum=1, maximum=3600
        )

        key = (guild.id, executor.id)
        times = self._actions[key]
        now = time.monotonic()
        times.append(now)
        while times and now - times[0] > window_seconds:
            times.popleft()
        if len(times) < threshold:
            return
        times.clear()  # one response per burst, not one per action past the threshold

        stripped = await self._strip_dangerous_roles(guild, executor.id)

        summary = f"Stripped roles: {', '.join(stripped)}" if stripped else (
            "Couldn't strip any roles (check my role is above theirs)."
        )
        headline = f"{executor.mention} - {description} ({threshold}+ within {window_seconds}s)"
        embed = discord.Embed(
            title="Anti-nuke triggered",
            description=f"{headline}\n{summary}",
            color=discord.Color.dark_red(),
        )
        await post_log(self.bot, guild, embed)

    async def _strip_dangerous_roles(self, guild: discord.Guild, user_id: int) -> list:
        member = guild.get_member(user_id)
        if member is None:
            return []
        stripped = []
        for role in list(member.roles):
            if role.is_default():
                continue
            if not any(getattr(role.permissions, name) for name in DANGEROUS_PERMISSIONS):
                continue
            try:
                await member.remove_roles(role, reason="Anti-nuke: mass destructive actions")
                stripped.append(role.name)
            except discord.Forbidden:
                continue
        return stripped

    async def _enabled(self, guild: discord.Guild) -> bool:
        """Cheap config read, checked *before* _find_executor's audit-log API
        call. The module is off by default, so without this every guild pays
        rate-limit budget on every channel/role delete and ban for a feature
        that never fires - worst of all during a mass-ban burst. The same
        check stays inside _handle_destructive_action so that method is still
        safe to call from anywhere. (review F11)"""
        return await self.bot.stores.config.get_bool(guild.id, "antinuke.enabled", False)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not await self._enabled(channel.guild):
            return
        executor = await self._find_executor(
            channel.guild, discord.AuditLogAction.channel_delete, channel.id
        )
        await self._handle_destructive_action(channel.guild, executor, "deleting channels")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if not await self._enabled(role.guild):
            return
        executor = await self._find_executor(role.guild, discord.AuditLogAction.role_delete, role.id)
        await self._handle_destructive_action(role.guild, executor, "deleting roles")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: Union[discord.User, discord.Member]):
        if not await self._enabled(guild):
            return
        executor = await self._find_executor(guild, discord.AuditLogAction.ban, user.id)
        await self._handle_destructive_action(guild, executor, "mass-banning members")


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))
