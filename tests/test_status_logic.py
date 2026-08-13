from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.modules import status as status_module
from bot.modules.status import LAST_UPDATED, VERSION, Status
from bot.modules.updater import UpdateStatus
from bot.stores import Stores

GUILD = 111


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.cogs = {}
    bot.get_cog = MagicMock(return_value=None)
    return bot


def _make_ctx():
    ctx = MagicMock()
    ctx.guild.id = GUILD
    ctx.send = AsyncMock()
    ctx.defer = AsyncMock()
    return ctx


def _make_updater(status):
    """/about asks for a live check now, not the loop's cached attribute."""
    updater_cog = MagicMock()
    updater_cog.current_status = AsyncMock(return_value=status)
    return updater_cog


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
async def test_about_shows_version_and_the_head_commit_date(db, monkeypatch):
    # "Last updated" is derived, not a hand-bumped constant that goes stale.
    bot = _make_bot(db)
    bot.latency = 0.01
    bot.db.available = True
    monkeypatch.setattr(status_module, "head_commit_date", AsyncMock(return_value="2026-08-13"))
    cog = Status(bot)
    ctx = _make_ctx()

    await Status.about.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    embed = ctx.send.await_args.kwargs["embed"]
    assert any(field.name == "Version" and field.value == VERSION for field in embed.fields)
    assert any(field.name == "Last updated" and field.value == "2026-08-13" for field in embed.fields)


@pytest.mark.asyncio
async def test_about_falls_back_when_the_commit_date_is_unavailable(db, monkeypatch):
    bot = _make_bot(db)
    bot.latency = 0.01
    bot.db.available = True
    monkeypatch.setattr(status_module, "head_commit_date", AsyncMock(return_value=None))
    cog = Status(bot)
    ctx = _make_ctx()

    await Status.about.callback(cog, ctx)

    embed = ctx.send.await_args.kwargs["embed"]
    assert any(field.name == "Last updated" and field.value == LAST_UPDATED for field in embed.fields)


@pytest.mark.asyncio
async def test_about_defers_before_the_git_fetch(db):
    # current_status() shells out to git; without a defer the slash command
    # can blow through Discord's 3s response window.
    bot = _make_bot(db)
    bot.latency = 0.01
    bot.db.available = True
    bot.get_cog = MagicMock(return_value=_make_updater(UpdateStatus(checked=True, available=False)))
    cog = Status(bot)
    ctx = _make_ctx()

    await Status.about.callback(cog, ctx)

    ctx.defer.assert_awaited_once()


@pytest.mark.asyncio
async def test_about_asks_for_a_live_check_not_the_cached_attribute(db):
    # The bug this fixes: /about read the 30-minute loop's cached value, so it
    # reported "Up to date" for up to half an hour after a push.
    bot = _make_bot(db)
    bot.latency = 0.01
    bot.db.available = True
    updater_cog = _make_updater(UpdateStatus(checked=True, available=True, behind=1, latest_summary="new"))
    updater_cog.status = UpdateStatus(checked=True, available=False)  # the stale cache
    bot.get_cog = MagicMock(return_value=updater_cog)
    cog = Status(bot)
    ctx = _make_ctx()

    await Status.about.callback(cog, ctx)

    updater_cog.current_status.assert_awaited_once()
    embed = ctx.send.await_args.kwargs["embed"]
    assert any("1 commit(s) behind" in f.value for f in embed.fields if f.name == "Updates")


@pytest.mark.asyncio
async def test_about_shows_up_to_date_and_no_button_when_no_update(db):
    bot = _make_bot(db)
    bot.latency = 0.01
    bot.db.available = True
    bot.get_cog = MagicMock(return_value=_make_updater(UpdateStatus(checked=True, available=False)))
    cog = Status(bot)
    ctx = _make_ctx()

    await Status.about.callback(cog, ctx)

    embed = ctx.send.await_args.kwargs["embed"]
    assert any(field.name == "Updates" and field.value == "Up to date" for field in embed.fields)
    assert ctx.send.await_args.kwargs["view"] is None


@pytest.mark.asyncio
async def test_about_shows_apply_button_when_update_available(db):
    bot = _make_bot(db)
    bot.latency = 0.01
    bot.db.available = True
    bot.get_cog = MagicMock(
        return_value=_make_updater(
            UpdateStatus(checked=True, available=True, behind=2, latest_summary="fix bug")
        )
    )
    cog = Status(bot)
    ctx = _make_ctx()

    await Status.about.callback(cog, ctx)

    embed = ctx.send.await_args.kwargs["embed"]
    assert any("2 commit(s) behind" in field.value for field in embed.fields if field.name == "Updates")
    view = ctx.send.await_args.kwargs["view"]
    assert view is not None
    assert any(item.label == "Apply update" for item in view.children)


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
