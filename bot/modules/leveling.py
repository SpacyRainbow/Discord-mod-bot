"""
leveling - PHASE 6 STRETCH GOAL, NOT YET IMPLEMENTED.

Not loaded by default (commented out in core.py's MODULES list).

Design notes:
  - Needs an `xp` table: (guild_id, user_id, xp, level, PRIMARY KEY(guild_id, user_id)).
  - Award XP in an on_message listener, with a per-user cooldown (e.g. one
    award per 60s) so people can't farm it by spamming - which would also
    fight with antispam.py, so the cooldown matters.
  - Pick an XP curve before writing code: linear (100 xp/level) is simplest;
    Dyno-style curves scale up per level. Simplest to start linear and
    tune later.
"""

from __future__ import annotations

from discord.ext import commands


class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))
