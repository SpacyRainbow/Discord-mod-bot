from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import AppCommandOptionType, app_commands
from discord.ext import commands

from bot.core import ModBot


@pytest.mark.asyncio
async def test_on_command_error_reports_clean_message_for_bad_member_argument():
    """Regression test: a hybrid command's Member-typed parameter failing to
    resolve (e.g. a typo'd name in `!warn vbgas ...`) used to crash to the
    generic "something went wrong" handler, because discord.py wraps this as
    HybridCommandError(TransformerError), not commands.BadArgument."""
    bot = ModBot()
    ctx = MagicMock()
    ctx.send = AsyncMock()

    transformer = MagicMock()
    transformer._error_display_name = "Member"
    original = app_commands.TransformerError("vbgas", AppCommandOptionType.user, transformer)
    error = commands.HybridCommandError(original)

    await bot.on_command_error(ctx, error)

    ctx.send.assert_awaited_once()
    assert "vbgas" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_on_command_error_reports_clean_message_for_dm_only_command():
    """Regression test: moderation commands access ctx.guild.id directly with
    no guild_only() guard - invoking one from a DM used to crash with an
    uncaught AttributeError on ctx.guild.id instead of a clean message."""
    bot = ModBot()
    ctx = MagicMock()
    ctx.send = AsyncMock()

    await bot.on_command_error(ctx, commands.NoPrivateMessage("this command cannot be used in DMs"))

    ctx.send.assert_awaited_once()
    assert "server" in ctx.send.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_on_command_error_survives_a_dead_interaction():
    """Regression test: if the interaction already expired (Discord's ~3s
    ack window passed before anything responded - the same "Unknown
    interaction" 404 a slow command can hit), ctx.send() for the error
    report itself raises discord.NotFound. That used to be an unhandled
    exception discord.py could only log as "Ignoring exception in
    on_command_error" - silent from the user's side either way."""
    bot = ModBot()
    ctx = MagicMock()
    response = MagicMock()
    response.status = 404
    response.reason = "Not Found"
    ctx.send = AsyncMock(
        side_effect=discord.NotFound(response, {"code": 10062, "message": "Unknown interaction"})
    )

    await bot.on_command_error(ctx, commands.CommandError("something unexpected"))

    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_command_error_still_logs_genuinely_unhandled_errors():
    bot = ModBot()
    ctx = MagicMock()
    ctx.send = AsyncMock()

    await bot.on_command_error(ctx, commands.CommandError("something unexpected"))

    ctx.send.assert_awaited_once()
    assert "went wrong" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_sync_commands_clears_stale_global_commands_when_guild_id_set(monkeypatch):
    """Regression test: guild-scoped sync alone never removes a global
    registration left over from before GUILD_ID was set (or from earlier
    testing) - without also clearing the global tree, Discord's / picker
    shows every command twice, one global copy and one guild-scoped copy,
    even though only the guild-scoped one is ever live."""
    monkeypatch.setenv("GUILD_ID", "12345")
    bot = ModBot()
    bot.tree.copy_global_to = MagicMock()
    bot.tree.clear_commands = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])

    await bot._sync_commands()

    bot.tree.clear_commands.assert_called_once_with(guild=None)
    assert bot.tree.sync.await_count == 2  # once guild-scoped, once globally to clear


@pytest.mark.asyncio
async def test_sync_commands_does_not_clear_global_tree_without_guild_id(monkeypatch):
    monkeypatch.delenv("GUILD_ID", raising=False)
    bot = ModBot()
    bot.tree.clear_commands = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])

    await bot._sync_commands()

    bot.tree.clear_commands.assert_not_called()
    assert bot.tree.sync.await_count == 1
