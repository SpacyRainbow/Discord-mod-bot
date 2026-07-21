import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.giveaway import Giveaway, GiveawayView
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
    ctx.author.id = 1
    ctx.author.mention = "<@1>"
    ctx.channel.id = 20
    message = MagicMock(id=500)
    ctx.send = AsyncMock(return_value=message)
    return ctx


def _make_interaction(message_id=500, user_id=2):
    interaction = MagicMock()
    interaction.guild_id = GUILD
    interaction.message = MagicMock(id=message_id)
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_cog_load_registers_view_and_handler(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)

    await cog.cog_load()

    bot.add_view.assert_called_once()
    assert bot.scheduler_handlers["giveaway_end"] == cog._handle_giveaway_end


@pytest.mark.asyncio
async def test_start_rejects_zero_winners(db):
    cog = Giveaway(_make_bot(db))
    ctx = _make_ctx()

    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 0, prize="Nitro")

    ctx.send.assert_awaited_once()
    assert "between 1" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_start_rejects_too_many_winners(db):
    cog = Giveaway(_make_bot(db))
    ctx = _make_ctx()

    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 21, prize="Nitro")

    ctx.send.assert_awaited_once()
    assert "between 1" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_start_rejects_bad_duration(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)
    ctx = _make_ctx()

    await Giveaway.giveaway_start.callback(cog, ctx, "10x", 1, prize="Nitro")

    ctx.send.assert_awaited_once()
    far_future = (discord.utils.utcnow() + datetime.timedelta(days=1)).isoformat()
    due = await bot.stores.scheduled.due(far_future)
    assert due == []  # nothing scheduled - rejected before creating anything


@pytest.mark.asyncio
async def test_start_creates_giveaway_and_schedules_end(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)
    ctx = _make_ctx()

    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 2, prize="Nitro")

    ctx.send.assert_awaited_once()
    far_future = (discord.utils.utcnow() + datetime.timedelta(days=1)).isoformat()
    due = await bot.stores.scheduled.due(far_future)
    assert len(due) == 1
    assert due[0][2] == "giveaway_end"
    row = await bot.stores.giveaways.get(due[0][3]["giveaway_id"])
    assert row[4] == "Nitro"
    assert row[5] == 2


@pytest.mark.asyncio
async def test_enter_button_toggles_entry(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)
    ctx = _make_ctx()
    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 1, prize="Nitro")
    view = GiveawayView(cog)
    interaction = _make_interaction()

    await view.enter_button.callback(interaction)
    entries = await bot.stores.giveaways.entries(1)
    assert entries == [2]

    await view.enter_button.callback(interaction)
    entries = await bot.stores.giveaways.entries(1)
    assert entries == []


@pytest.mark.asyncio
async def test_enter_button_reports_ended_giveaway(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)
    ctx = _make_ctx()
    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 1, prize="Nitro")
    await bot.stores.giveaways.mark_ended(1)
    view = GiveawayView(cog)
    interaction = _make_interaction()

    await view.enter_button.callback(interaction)

    interaction.followup.send.assert_awaited_once()
    assert "ended" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_finish_giveaway_announces_winner_and_marks_ended(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)
    ctx = _make_ctx()
    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 1, prize="Nitro")
    await bot.stores.giveaways.toggle_entry(1, 42)
    channel = MagicMock()
    message = MagicMock()
    message.embeds = [MagicMock()]
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock()
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._finish_giveaway(1, reroll=False)

    channel.send.assert_awaited_once()
    assert "42" in channel.send.await_args.args[0]
    row = await bot.stores.giveaways.get(1)
    assert row[8] == 1  # ended


@pytest.mark.asyncio
async def test_finish_giveaway_with_no_entries_announces_no_winner(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)
    ctx = _make_ctx()
    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 1, prize="Nitro")
    channel = MagicMock()
    message = MagicMock()
    message.embeds = [MagicMock()]
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock()
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._finish_giveaway(1, reroll=False)

    assert "No valid entries" in channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_giveaway_end_cmd_reports_missing(db):
    cog = Giveaway(_make_bot(db))
    ctx = _make_ctx()

    await Giveaway.giveaway_end_cmd.callback(cog, ctx, 999)

    assert "No giveaway found" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_reroll_rejects_giveaway_still_running(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)
    ctx = _make_ctx()
    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 1, prize="Nitro")

    await Giveaway.giveaway_reroll.callback(cog, ctx, 500)

    assert "hasn't ended" in ctx.send.await_args.args[0]


# ---- edge cases ----


@pytest.mark.asyncio
async def test_enter_button_reports_missing_giveaway(db):
    cog = Giveaway(_make_bot(db))
    interaction = _make_interaction(message_id=999999)
    view = GiveawayView(cog)

    await view.enter_button.callback(interaction)

    interaction.followup.send.assert_awaited_once()
    assert "Couldn't find" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_finish_giveaway_picks_fewer_winners_than_requested_when_entries_run_short(db):
    bot = _make_bot(db)
    cog = Giveaway(bot)
    ctx = _make_ctx()
    await Giveaway.giveaway_start.callback(cog, ctx, "10m", 5, prize="Nitro")  # 5 winners requested
    await bot.stores.giveaways.toggle_entry(1, 42)  # only 1 entrant
    channel = MagicMock()
    message = MagicMock()
    message.embeds = [MagicMock()]
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock()
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._finish_giveaway(1, reroll=False)  # must not raise picking 5 winners from 1 entrant

    channel.send.assert_awaited_once()
    assert "42" in channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_finish_giveaway_does_nothing_for_unknown_id(db):
    cog = Giveaway(_make_bot(db))

    await cog._finish_giveaway(999, reroll=False)  # must not raise
