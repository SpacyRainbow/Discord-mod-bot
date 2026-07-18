"""
scheduler - PHASE 6 STRETCH GOAL, NOT YET IMPLEMENTED.

Not loaded by default (commented out in core.py's MODULES list).

Design notes for when this gets built:
  - Needs a `scheduled_events` table: (id, guild_id, channel_id, message,
    next_run TEXT, rrule TEXT or interval_seconds INTEGER).
  - A single tasks.loop(seconds=60) checks all guilds' due events, matching
    the pattern already used in bored.py - copy that structure.
  - Decide up front: one-off reminders, recurring (e.g. "every Friday"), or
    both? That changes the table schema, so it's worth answering before
    writing code rather than after.
"""

from __future__ import annotations

from discord.ext import commands


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
