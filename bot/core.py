from __future__ import annotations

import asyncio
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from .db import Database
from .modules.minecraft import MinecraftGuildRestrictedError
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
    "bot.modules.minecraft",
    "bot.modules.scheduler",
    "bot.modules.raid",
    "bot.modules.antinuke",
    "bot.modules.starboard",
    "bot.modules.giveaway",
    "bot.modules.poll",
    "bot.modules.tickets",
    "bot.modules.greetings",
    "bot.modules.embedfix",
    "bot.modules.updater",
    "bot.modules.aguiliar",
    "bot.modules.eventlog",
    # "bot.modules.counters",
    # "bot.modules.leveling",
]

DEFAULT_PREFIX = os.getenv("DEFAULT_PREFIX", "!")


class ModBot(commands.Bot):
    def __init__(self, *, presences: bool = True):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        # Presence is a privileged intent: it must ALSO be ticked in the Discord
        # developer portal, and asking for it without that is a hard login
        # failure, not a degraded start. __main__.py therefore tries it, catches
        # PrivilegedIntentsRequired and starts again without it, so the bot can
        # never be taken down by a toggle that has not been flipped yet.
        # aguiliar.py reads self.intents.presences before offering to report
        # anyone's status - with the intent off, every member looks offline, and
        # reporting that would be a confident lie.
        intents.presences = presences
        # help_command=None: status.py defines its own /help hybrid command,
        # which would collide with discord.py's default one otherwise.
        super().__init__(
            command_prefix=self._get_prefix,
            intents=intents,
            help_command=None,
            # Everything this bot echoes back can contain user-authored text (tags,
            # buckets, markov chains). Default to resolving no mentions at all; the
            # handful of sends that are *meant* to ping opt in explicitly. (review F6)
            allowed_mentions=discord.AllowedMentions.none(),
        )

        db_path = os.getenv("DB_PATH", "/app/data/bot.db")
        self.db = Database(db_path)
        self.stores = Stores(self.db)
        self.start_time: float | None = None
        # Populated by producer cogs' cog_load (moderation.py, giveaway.py,
        # poll.py, scheduler.py itself) - see scheduler.py's module
        # docstring. Initialized here, not in Scheduler's own __init__, so
        # it exists no matter what order MODULES loads in.
        self.scheduler_handlers: dict = {}

    async def _get_prefix(self, bot: "ModBot", message: discord.Message):
        if message.guild is None:
            return DEFAULT_PREFIX
        prefix = await self.stores.config.get(message.guild.id, "commandprefix", DEFAULT_PREFIX)
        # An empty/whitespace prefix makes when_mentioned_or("") match every
        # message in the guild, so the bot tries full command resolution on all
        # traffic - and there'd be no working way to set it back. /setconfig can
        # still write one directly, so coerce defensively here. (review F13)
        prefix = prefix.strip() if isinstance(prefix, str) else ""
        if not prefix:
            prefix = DEFAULT_PREFIX
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
                # Clears any global registration left over from before
                # GUILD_ID was set (or from earlier testing) - without
                # this, Discord's / picker shows every command twice, one
                # global copy and one guild-scoped copy, even though only
                # the guild-scoped one is ever actually live.
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
                logger.info("Cleared any stale global command registrations")
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

    async def _report_error(self, ctx: commands.Context, content: str) -> None:
        """Best-effort - reporting the error can itself fail for the same
        underlying reason as the original error (most commonly: the
        interaction already expired, since Discord invalidates it ~3s
        after dispatch if nothing's responded yet). Without this, that
        second failure was an unhandled exception discord.py could only
        log as "Ignoring exception in on_command_error" - silent from the
        user's side, since neither the original error nor this report
        ever reached them."""
        try:
            await ctx.send(content)
        except discord.HTTPException:
            logger.warning("Couldn't deliver an error message for %s", ctx.command)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            await self._report_error(ctx, "That command only works in a server, not in a DM.")
            return
        if isinstance(error, MinecraftGuildRestrictedError):
            await self._report_error(ctx, str(error))
            return
        if isinstance(error, commands.MissingPermissions):
            await self._report_error(ctx, "You don't have permission to do that.")
            return
        if isinstance(error, commands.BotMissingPermissions):
            await self._report_error(ctx, "I don't have the permissions I need to do that.")
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await self._report_error(ctx, f"Missing argument: `{error.param.name}`")
            return
        if isinstance(error, commands.BadArgument):
            await self._report_error(ctx, f"Bad argument: {error}")
            return
        if isinstance(error, commands.HybridCommandError) and isinstance(
            error.original, app_commands.TransformerError
        ):
            # A hybrid command's Member/Role/Channel-typed parameter failed to
            # resolve (e.g. a typo'd name) - discord.py routes this through
            # its own transformer machinery for both invocation paths, not
            # through commands.BadArgument, so it needs its own branch here.
            await self._report_error(ctx, f"Couldn't resolve `{error.original.value}` to a valid argument.")
            return
        logger.exception("Unhandled command error in %s", ctx.command, exc_info=error)
        await self._report_error(ctx, "Something went wrong running that command. It's been logged.")
