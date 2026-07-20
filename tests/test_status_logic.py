from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.modules.status import Status
from bot.stores import Stores

GUILD = 111


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.cogs = {}
    return bot


def _make_ctx():
    ctx = MagicMock()
    ctx.guild.id = GUILD
    ctx.send = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_setconfig_writes_the_value(db):
    cog = Status(_make_bot(db))
    ctx = _make_ctx()

    await Status.set_config.callback(cog, ctx, "spam.max_messages", value="10")

    stored = await cog.bot.stores.config.get(GUILD, "spam.max_messages")
    assert stored == "10"
    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_getconfig_reports_unset_key(db):
    cog = Status(_make_bot(db))
    ctx = _make_ctx()

    await Status.get_config.callback(cog, ctx, "spam.max_messages")

    ctx.send.assert_awaited_once()
    assert "isn't set" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_getconfig_reports_a_set_value(db):
    cog = Status(_make_bot(db))
    ctx = _make_ctx()
    await cog.bot.stores.config.set(GUILD, "spam.max_messages", "7")

    await Status.get_config.callback(cog, ctx, "spam.max_messages")

    ctx.send.assert_awaited_once()
    assert "7" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_help_lists_commands_grouped_by_cog(db):
    bot = _make_bot(db)
    fake_command = MagicMock()
    fake_command.name = "ping"
    fake_cog = MagicMock()
    fake_cog.get_commands = MagicMock(return_value=[fake_command])
    bot.cogs = {"Status": fake_cog}
    cog = Status(bot)
    ctx = _make_ctx()

    await Status.help_cmd.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    embed = ctx.send.await_args.kwargs["embed"]
    assert any(field.name == "Status" and "`/ping`" in field.value for field in embed.fields)
    assert "github.com" in embed.description.lower()


@pytest.mark.asyncio
async def test_help_skips_cogs_with_no_commands(db):
    bot = _make_bot(db)
    empty_cog = MagicMock()
    empty_cog.get_commands = MagicMock(return_value=[])
    bot.cogs = {"AntiSpam": empty_cog}
    cog = Status(bot)
    ctx = _make_ctx()

    await Status.help_cmd.callback(cog, ctx)

    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.fields == []
