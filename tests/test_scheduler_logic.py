import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.scheduler import Scheduler
from bot.stores import Stores

GUILD = 111


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.wait_until_ready = AsyncMock()
    bot.scheduler_handlers = {}
    return bot


def _make_cog(bot):
    cog = Scheduler(bot)
    cog.check_due_tasks.cancel()
    return cog


def _make_ctx(guild_id=GUILD):
    ctx = MagicMock()
    if guild_id is None:
        ctx.guild = None
    else:
        ctx.guild.id = guild_id
    ctx.author.id = 42
    ctx.channel.id = 55
    ctx.send = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_cog_load_registers_reminder_handler(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)

    await cog.cog_load()

    assert bot.scheduler_handlers["reminder"] == cog._handle_reminder


@pytest.mark.asyncio
async def test_remind_rejects_bad_duration(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    ctx = _make_ctx()

    await Scheduler.remind.callback(cog, ctx, "10x", text="hi")

    ctx.send.assert_awaited_once()
    far_future = (discord.utils.utcnow() + datetime.timedelta(days=400)).isoformat()
    due = await bot.stores.scheduled.due(far_future)
    assert due == []


@pytest.mark.asyncio
async def test_remind_schedules_a_task(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    ctx = _make_ctx()

    await Scheduler.remind.callback(cog, ctx, "10m", text="drink water")

    far_future = (discord.utils.utcnow() + datetime.timedelta(days=1)).isoformat()
    due = await bot.stores.scheduled.due(far_future)
    assert len(due) == 1
    assert due[0][2] == "reminder"
    assert due[0][3] == {"user_id": 42, "channel_id": 55, "text": "drink water"}
    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_remind_works_outside_a_guild(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    ctx = _make_ctx(guild_id=None)

    await Scheduler.remind.callback(cog, ctx, "10m", text="hi")

    far_future = (discord.utils.utcnow() + datetime.timedelta(days=1)).isoformat()
    due = await bot.stores.scheduled.due(far_future)
    assert len(due) == 1
    assert due[0][1] == 0  # guild_id sentinel used outside a guild


@pytest.mark.asyncio
async def test_handle_reminder_dms_the_user(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)

    await cog._handle_reminder(GUILD, {"user_id": 42, "channel_id": 55, "text": "drink water"})

    user.send.assert_awaited_once()
    assert "drink water" in user.send.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_reminder_falls_back_to_channel_if_dm_closed(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    user = MagicMock()
    user.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Cannot send messages to this user")
    )
    bot.get_user = MagicMock(return_value=user)
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)

    await cog._handle_reminder(GUILD, {"user_id": 42, "channel_id": 55, "text": "drink water"})

    channel.send.assert_awaited_once()
    assert "drink water" in channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_reminder_falls_back_to_channel_if_user_not_cached(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    bot.get_user = MagicMock(return_value=None)
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)

    await cog._handle_reminder(GUILD, {"user_id": 42, "channel_id": 55, "text": "drink water"})

    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_due_tasks_dispatches_to_registered_handler(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    handler = AsyncMock()
    bot.scheduler_handlers["reminder"] = handler
    await bot.stores.scheduled.add(
        GUILD, "reminder", {"x": 1}, discord.utils.utcnow() - datetime.timedelta(seconds=1)
    )

    await cog.check_due_tasks.coro(cog)

    handler.assert_awaited_once_with(GUILD, {"x": 1})
    due = await bot.stores.scheduled.due(discord.utils.utcnow().isoformat())
    assert due == []


@pytest.mark.asyncio
async def test_check_due_tasks_marks_done_even_if_handler_raises(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    bot.scheduler_handlers["reminder"] = handler
    await bot.stores.scheduled.add(
        GUILD, "reminder", {"x": 1}, discord.utils.utcnow() - datetime.timedelta(seconds=1)
    )

    await cog.check_due_tasks.coro(cog)  # must not raise

    due = await bot.stores.scheduled.due(discord.utils.utcnow().isoformat())
    assert due == []


@pytest.mark.asyncio
async def test_check_due_tasks_skips_unregistered_kind_without_raising(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    await bot.stores.scheduled.add(
        GUILD, "unknown_kind", {}, discord.utils.utcnow() - datetime.timedelta(seconds=1)
    )

    await cog.check_due_tasks.coro(cog)  # must not raise

    due = await bot.stores.scheduled.due(discord.utils.utcnow().isoformat())
    assert due == []
