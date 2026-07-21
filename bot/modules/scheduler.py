"""
scheduler - a single generic "run this at time T" engine, shared by every
feature that needs to do something later: /remind (this module), tempban
auto-unban (moderation.py), giveaway endings (giveaway.py), and poll
auto-close (poll.py). One scheduled_tasks table + one tasks.loop watchdog,
instead of each feature reinventing its own polling loop (moderation.py's
mute_expiry_check predates this and was left as its own thing rather than
migrated, to avoid touching already-working, already-tested code).

How other cogs plug in: bot.scheduler_handlers is a plain dict, initialized
in ModBot.__init__ (core.py) so it exists regardless of module load order.
Each producer cog registers into it from its own cog_load, e.g.:

    self.bot.scheduler_handlers["tempban_unban"] = self._handle_tempban_unban

A handler is `async def handler(guild_id: int, payload: dict) -> None`.
Scheduling a task: `await bot.stores.scheduled.add(guild_id, kind, payload,
run_at)` - payload is any JSON-serializable dict. A task is marked done
right after its handler runs (or fails) - at-most-once, no retry/backoff,
matching this bot's existing "log and move on" philosophy elsewhere (e.g.
antispam's bulk-delete failures) rather than building a queue that can get
stuck retrying a permanently-broken task forever.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from ..durations import parse_duration

logger = logging.getLogger("bot.modules.scheduler")

CHECK_INTERVAL_SECONDS = 30


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_due_tasks.start()

    def cog_unload(self):
        self.check_due_tasks.cancel()

    async def cog_load(self) -> None:
        self.bot.scheduler_handlers["reminder"] = self._handle_reminder

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def check_due_tasks(self):
        now_iso = discord.utils.utcnow().isoformat()
        for task_id, guild_id, kind, payload in await self.bot.stores.scheduled.due(now_iso):
            handler = self.bot.scheduler_handlers.get(kind)
            if handler is None:
                logger.warning("No handler registered for scheduled task kind=%s", kind)
            else:
                try:
                    await handler(guild_id, payload)
                except Exception:
                    logger.exception("Scheduled task handler failed for kind=%s", kind)
            await self.bot.stores.scheduled.mark_done(task_id)

    @check_due_tasks.before_loop
    async def before_check_due_tasks(self):
        await self.bot.wait_until_ready()

    async def _handle_reminder(self, guild_id: int, payload: dict) -> None:
        user_id = payload["user_id"]
        text = payload["text"]
        content = f"Reminder: {text}"
        user = self.bot.get_user(user_id)
        if user is not None:
            try:
                await user.send(content)
                return
            except discord.Forbidden:
                pass
        channel = self.bot.get_channel(payload.get("channel_id"))
        if channel is not None:
            try:
                await channel.send(f"<@{user_id}> {content}")
            except discord.HTTPException:
                pass

    @commands.hybrid_command(name="remind", description="DMs you a reminder after a delay, e.g. 10m, 2h, 1d")
    async def remind(self, ctx: commands.Context, duration: str, *, text: str):
        try:
            delta = parse_duration(duration)
        except ValueError as e:
            await ctx.send(str(e))
            return

        run_at = discord.utils.utcnow() + delta
        guild_id = ctx.guild.id if ctx.guild is not None else 0
        payload = {"user_id": ctx.author.id, "channel_id": ctx.channel.id, "text": text}
        try:
            await self.bot.stores.scheduled.add(guild_id, "reminder", payload, run_at)
        except RuntimeError:
            await ctx.send("Couldn't schedule that reminder (database unavailable).")
            return

        when = discord.utils.format_dt(run_at, "R")
        await ctx.send(f"Okay, I'll remind you {when}: {text}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
