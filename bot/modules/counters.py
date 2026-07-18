"""
counters - PHASE 6 STRETCH GOAL, NOT YET IMPLEMENTED.

Not loaded by default (commented out in core.py's MODULES list).

Sweetie Bot's original counters module tracked arbitrary named numeric
tallies (e.g. how many times a word's been said). Open design question
before building: what do you actually want counted for this server?
That answer determines the schema (single incrementing counters vs.
per-user counters vs. per-item counters), so it's worth deciding first.
"""

from __future__ import annotations

from discord.ext import commands


class Counters(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Counters(bot))
