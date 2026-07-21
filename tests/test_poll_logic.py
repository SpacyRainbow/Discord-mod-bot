import datetime
import json
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.poll import Poll
from bot.stores import Stores

GUILD = 111


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.scheduler_handlers = {}
    bot.add_view = MagicMock()
    return bot


def _make_ctx():
    ctx = MagicMock()
    ctx.guild.id = GUILD
    ctx.channel.id = 20
    message = MagicMock(id=500)
    ctx.send = AsyncMock(return_value=message)
    return ctx


def _make_interaction(user_id=2):
    interaction = MagicMock()
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_poll_rejects_fewer_than_two_options(db):
    cog = Poll(_make_bot(db))
    ctx = _make_ctx()

    await Poll.poll.callback(cog, ctx, "Best color?", "Red", None)

    ctx.send.assert_awaited_once()
    assert "at least two" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_poll_rejects_bad_duration(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()

    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue", duration="10x")

    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_creates_row_and_view(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()

    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")

    ctx.send.assert_awaited_once()
    row = await bot.stores.polls.get(1)
    assert row[4] == "Best color?"
    assert row[3] == 500  # message id backfilled after send


@pytest.mark.asyncio
async def test_poll_with_duration_schedules_auto_close(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()

    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue", duration="10m")

    far_future = (discord.utils.utcnow() + datetime.timedelta(days=1)).isoformat()
    due = await bot.stores.scheduled.due(far_future)
    assert len(due) == 1
    assert due[0][2] == "poll_close"


@pytest.mark.asyncio
async def test_poll_without_duration_does_not_schedule_close(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()

    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")

    far_future = (discord.utils.utcnow() + datetime.timedelta(days=1)).isoformat()
    due = await bot.stores.scheduled.due(far_future)
    assert due == []


@pytest.mark.asyncio
async def test_vote_is_recorded(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()
    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")
    interaction = _make_interaction()

    await cog.handle_vote(interaction, 1, 0)

    counts = await bot.stores.polls.vote_counts(1, 2)
    assert counts == [1, 0]
    interaction.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_clicking_same_option_retracts_vote(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()
    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")
    interaction = _make_interaction()

    await cog.handle_vote(interaction, 1, 0)
    await cog.handle_vote(interaction, 1, 0)

    counts = await bot.stores.polls.vote_counts(1, 2)
    assert counts == [0, 0]


@pytest.mark.asyncio
async def test_switching_option_moves_the_vote(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()
    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")
    interaction = _make_interaction()

    await cog.handle_vote(interaction, 1, 0)
    await cog.handle_vote(interaction, 1, 1)

    counts = await bot.stores.polls.vote_counts(1, 2)
    assert counts == [0, 1]


@pytest.mark.asyncio
async def test_vote_on_closed_poll_is_rejected(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()
    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")
    await bot.stores.polls.close(1)
    interaction = _make_interaction()

    await cog.handle_vote(interaction, 1, 0)

    interaction.response.send_message.assert_awaited_once()
    assert "closed" in interaction.response.send_message.await_args.args[0]
    counts = await bot.stores.polls.vote_counts(1, 2)
    assert counts == [0, 0]


@pytest.mark.asyncio
async def test_vote_on_missing_poll_reports_cleanly(db):
    cog = Poll(_make_bot(db))
    interaction = _make_interaction()

    await cog.handle_vote(interaction, 999, 0)

    interaction.response.send_message.assert_awaited_once()
    assert "Couldn't find" in interaction.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_close_poll_edits_message_and_marks_closed(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()
    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")
    channel = MagicMock()
    message = MagicMock()
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._close_poll(1)

    message.edit.assert_awaited_once()
    row = await bot.stores.polls.get(1)
    assert row[7] == 1  # closed


@pytest.mark.asyncio
async def test_pollclose_cmd_reports_missing(db):
    cog = Poll(_make_bot(db))
    ctx = _make_ctx()

    await Poll.poll_close_cmd.callback(cog, ctx, 999)

    assert "No poll found" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_cog_load_rehydrates_open_polls(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()
    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")

    bot.add_view.reset_mock()
    await cog.cog_load()

    bot.add_view.assert_called_once()
    assert bot.add_view.call_args.kwargs["message_id"] == 500
    assert bot.scheduler_handlers["poll_close"] == cog._handle_poll_close


@pytest.mark.asyncio
async def test_cog_load_skips_closed_polls(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()
    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")
    await bot.stores.polls.close(1)

    bot.add_view.reset_mock()
    await cog.cog_load()

    bot.add_view.assert_not_called()


# ---- edge cases ----


@pytest.mark.asyncio
async def test_close_poll_twice_is_idempotent(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()
    await Poll.poll.callback(cog, ctx, "Best color?", "Red", "Blue")
    channel = MagicMock()
    message = MagicMock()
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._close_poll(1)
    await cog._close_poll(1)  # must not re-post or raise

    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_caps_at_five_options(db):
    bot = _make_bot(db)
    cog = Poll(bot)
    ctx = _make_ctx()

    await Poll.poll.callback(
        cog, ctx, "Q?", "A", "B", option3="C", option4="D", option5="E"
    )

    row = await bot.stores.polls.get(1)
    assert len(json.loads(row[5])) == 5
