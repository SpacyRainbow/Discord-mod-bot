from unittest.mock import AsyncMock, MagicMock

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
async def test_on_command_error_still_logs_genuinely_unhandled_errors():
    bot = ModBot()
    ctx = MagicMock()
    ctx.send = AsyncMock()

    await bot.on_command_error(ctx, commands.CommandError("something unexpected"))

    ctx.send.assert_awaited_once()
    assert "went wrong" in ctx.send.await_args.args[0]
