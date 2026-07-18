"""
minecraft - PHASE 6 STRETCH GOAL, NOT YET IMPLEMENTED.

Not loaded by default (commented out in core.py's MODULES list).

This one specifically needs details from your actual Crafty Controller setup
before it can be written for real:
  - Crafty exposes a REST API (typically on the same host, a different port)
    that needs an API token generated from its web UI.
  - Simpler alternative that avoids Crafty auth entirely: query the Minecraft
    server directly over the query/status protocol using the `mcstatus`
    package - gives player count + online/offline without touching Crafty's
    API at all. Probably the faster path to a working !mcstatus command.
  - Command sketch either way: `!mcstatus` posts an embed with online/offline,
    player count, and (if using Crafty's API) server name/version.

Bring the Crafty API details (or confirm you want the mcstatus-only route)
and this gets filled in for real.
"""

from __future__ import annotations

from discord.ext import commands


class Minecraft(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Minecraft(bot))
