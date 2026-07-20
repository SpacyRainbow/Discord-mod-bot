from __future__ import annotations

import asyncio
import logging
import os
import time

import discord
from discord.ext import commands

from .db import Database
from .stores import Stores

logger = logging.getLogger("bot.core")

# Phase 1-5 modules load by default. Phase 6 stretch goals are commented out
# until their design questions are settled (see README "Roadmap").
MODULES = [
    "bot.modules.status",
    "bot.modules.automod",
    "bot.modules.antispam",
    "bot.modules.moderation",
    "bot.modules.logging_module",
    "bot.modules.roles",
    "bot.modules.quote",
    "bot.modules.tag",
    "bot.modules.bucket",
    "bot.modules.witty",
    "bot.modules.bored",
    "bot.modules.markov",
    "bot.modules.music",
    "bot.modules.setup",
    # "bot.modules.scheduler",
    # "bot.modules.counters",
    # "bot.modules.leveling",
    # "bot.modules.minecraft",
]

DEFAULT_PREFIX = os.getenv("DEFAULT_PREFIX", "!")


class ModBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        # help_command=None: status.py defines its own /help hybrid command,
        # which would collide with discord.py's default one otherwise.
        super().__init__(command_prefix=self._get_prefix, intents=intents, help_command=None)

        db_path = os.getenv("DB_PATH", "/app/data/bot.db")
        self.db = Database(db_path)
        self.stores = Stores(self.db)
        self.start_time: float | None = None

    async def _get_prefix(self, bot: "ModBot", message: discord.Message):
        if message.guild is None:
            return DEFAULT_PREFIX
        prefix = await self.stores.config.get(message.guild.id, "commandprefix", DEFAULT_PREFIX)
        return commands.when_mentioned_or(prefix)(bot, message)

    async def setup_hook(self) -> None:
        try:
            await self.db.connect()
        except Exception:
            logger.exception("Initial database connection failed - starting in degraded mode")

        for module in MODULES:
            try:
                await self.load_extension(module)
                logger.info("Loaded module: %s", module)
            except Exception:
                logger.exception("Failed to load module: %s", module)

        await self._sync_commands()
        self.loop.create_task(self._db_watchdog())

    async def _sync_commands(self) -> None:
        """Registers hybrid/slash commands with Discord. Guild-scoped sync
        (via GUILD_ID) propagates instantly; a global sync can take up to an
        hour, so guild-scoped is the better default for a single-server bot."""
        guild_id = os.getenv("GUILD_ID")
        try:
            if guild_id:
                guild_obj = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild_obj)
                synced = await self.tree.sync(guild=guild_obj)
                logger.info("Synced %d command(s) to guild %s", len(synced), guild_id)
            else:
                synced = await self.tree.sync()
                logger.info("Synced %d command(s) globally (may take up to an hour to appear)", len(synced))
        except Exception:
            logger.exception("Failed to sync application commands")

    async def _db_watchdog(self) -> None:
        """Retry the DB connection every 30s while it's down, matching the
        Sweetie Bot pattern of degrading gracefully instead of crashing."""
        await self.wait_until_ready()
        while not self.is_closed():
            if not self.db.available:
                logger.warning("Database unavailable, attempting reconnect...")
                await self.db.ping()
                if self.db.available:
                    logger.info("Database reconnected")
            await asyncio.sleep(30)

    async def on_ready(self):
        self.start_time = time.time()
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id)
        logger.info("Connected to %d guild(s)", len(self.guilds))

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You don't have permission to do that.")
            return
        if isinstance(error, commands.BotMissingPermissions):
            await ctx.send("I don't have the permissions I need to do that.")
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing argument: `{error.param.name}`")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"Bad argument: {error}")
            return
        logger.exception("Unhandled command error in %s", ctx.command, exc_info=error)
        await ctx.send("Something went wrong running that command. It's been logged.")
