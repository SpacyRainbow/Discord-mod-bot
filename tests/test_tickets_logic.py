from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.modules.tickets import Tickets
from bot.stores import Stores

GUILD = 111


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.add_view = MagicMock()
    return bot


def _make_guild():
    guild = MagicMock()
    guild.id = GUILD
    guild.default_role = MagicMock()
    guild.me = MagicMock()
    guild.roles = []
    return guild


def _make_interaction(guild, user_id=2, channel=None):
    interaction = MagicMock()
    interaction.guild = guild
    interaction.guild_id = guild.id if guild else None
    interaction.user = MagicMock(id=user_id)
    interaction.user.name = f"user{user_id}"
    interaction.channel = channel
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_cog_load_registers_both_views(db):
    bot = _make_bot(db)
    cog = Tickets(bot)

    await cog.cog_load()

    assert bot.add_view.call_count == 2


@pytest.mark.asyncio
async def test_open_ticket_creates_channel_and_records_it(db):
    bot = _make_bot(db)
    cog = Tickets(bot)
    guild = _make_guild()
    new_channel = MagicMock(id=999)
    new_channel.mention = "#ticket-user2"
    new_channel.send = AsyncMock()
    guild.create_text_channel = AsyncMock(return_value=new_channel)
    guild.get_channel = MagicMock(return_value=None)
    interaction = _make_interaction(guild)

    await cog.open_ticket(interaction)

    guild.create_text_channel.assert_awaited_once()
    ticket = await bot.stores.tickets.get_by_channel(GUILD, 999)
    assert ticket is not None
    interaction.followup.send.assert_awaited_once()
    assert new_channel.mention in interaction.followup.send.await_args.args[0]
    new_channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_ticket_blocks_duplicate_open_ticket(db):
    bot = _make_bot(db)
    cog = Tickets(bot)
    guild = _make_guild()
    existing_channel = MagicMock(id=888)
    existing_channel.mention = "#ticket-existing"
    guild.get_channel = MagicMock(return_value=existing_channel)
    guild.create_text_channel = AsyncMock()
    await bot.stores.tickets.create(GUILD, 888, 2)
    interaction = _make_interaction(guild)

    await cog.open_ticket(interaction)

    guild.create_text_channel.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert "already have an open ticket" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_open_ticket_reports_forbidden(db):
    bot = _make_bot(db)
    cog = Tickets(bot)
    guild = _make_guild()
    guild.get_channel = MagicMock(return_value=None)
    guild.create_text_channel = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    interaction = _make_interaction(guild)

    await cog.open_ticket(interaction)

    interaction.followup.send.assert_awaited_once()
    assert "don't have permission" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_close_ticket_deletes_channel(db):
    bot = _make_bot(db)
    cog = Tickets(bot)
    guild = _make_guild()
    channel = MagicMock(id=999)
    channel.delete = AsyncMock()
    await bot.stores.tickets.create(GUILD, 999, 2)
    interaction = _make_interaction(guild, channel=channel)

    with patch("bot.modules.tickets.asyncio.sleep", new=AsyncMock()):
        await cog.close_ticket(interaction)

    channel.delete.assert_awaited_once()
    ticket = await bot.stores.tickets.get_by_channel(GUILD, 999)
    assert ticket is None


@pytest.mark.asyncio
async def test_close_ticket_rejects_non_ticket_channel(db):
    bot = _make_bot(db)
    cog = Tickets(bot)
    guild = _make_guild()
    channel = MagicMock(id=999)
    channel.delete = AsyncMock()
    interaction = _make_interaction(guild, channel=channel)

    await cog.close_ticket(interaction)

    channel.delete.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert "isn't an open ticket" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_close_ticket_tolerates_already_deleted_channel(db):
    bot = _make_bot(db)
    cog = Tickets(bot)
    guild = _make_guild()
    channel = MagicMock(id=999)
    channel.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel"))
    await bot.stores.tickets.create(GUILD, 999, 2)
    interaction = _make_interaction(guild, channel=channel)

    with patch("bot.modules.tickets.asyncio.sleep", new=AsyncMock()):
        await cog.close_ticket(interaction)  # must not raise


@pytest.mark.asyncio
async def test_ticketpanel_posts_embed_with_view(db):
    cog = Tickets(_make_bot(db))
    ctx = MagicMock()
    ctx.send = AsyncMock()

    await Tickets.ticketpanel.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    assert "view" in ctx.send.await_args.kwargs


# ---- edge cases ----


@pytest.mark.asyncio
async def test_open_ticket_ignores_category_id_pointing_to_a_non_category_channel(db):
    bot = _make_bot(db)
    cog = Tickets(bot)
    guild = _make_guild()
    not_a_category = MagicMock(spec=discord.TextChannel)
    new_channel = MagicMock(id=999)
    new_channel.mention = "#ticket-user2"
    new_channel.send = AsyncMock()
    guild.create_text_channel = AsyncMock(return_value=new_channel)
    guild.get_channel = MagicMock(side_effect=lambda cid: not_a_category if cid == 42 else None)
    await bot.stores.config.set(GUILD, "tickets.category_id", "42")
    interaction = _make_interaction(guild)

    await cog.open_ticket(interaction)

    guild.create_text_channel.assert_awaited_once()
    assert guild.create_text_channel.await_args.kwargs["category"] is None

# --- review F9: duplicate ticket channels from two fast clicks -----------

@pytest.mark.asyncio
async def test_open_ticket_is_serialised_per_user(db):
    """get_open_for_user -> create_text_channel -> create had no mutual
    exclusion, so two quick Open Ticket clicks both read "no open ticket" and
    created two channels - the duplicate the check exists to prevent."""
    import asyncio

    bot = MagicMock()
    bot.stores = Stores(db)
    cog = Tickets(bot)

    created = []

    async def create_text_channel(name, **kwargs):
        await asyncio.sleep(0)  # yield, so an unguarded second click can interleave
        channel = MagicMock()
        channel.id = 900 + len(created)
        channel.mention = f"#{name}"
        channel.send = AsyncMock()
        created.append(channel)
        return channel

    guild = MagicMock()
    guild.id = 1
    guild.roles = []
    guild.get_channel = MagicMock(return_value=None)
    guild.create_text_channel = AsyncMock(side_effect=create_text_channel)

    def _interaction():
        interaction = MagicMock()
        interaction.guild = guild
        interaction.user.id = 42
        interaction.user.name = "someone"
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    await asyncio.gather(cog.open_ticket(_interaction()), cog.open_ticket(_interaction()))

    assert len(created) == 1
    assert await bot.stores.tickets.get_open_for_user(1, 42) is not None


# --- review F10: _open_locks grew one entry per ticket-open click ---


async def test_sweep_locks_drops_an_unheld_lock(db):
    cog = Tickets(_make_bot(db))
    cog._open_lock_for(GUILD, 7)
    assert cog._sweep_locks() == 1
    assert cog._open_locks == {}


async def test_sweep_locks_never_evicts_a_held_lock(db):
    cog = Tickets(_make_bot(db))
    lock = cog._open_lock_for(GUILD, 7)
    async with lock:
        assert cog._sweep_locks() == 0
        assert cog._open_lock_for(GUILD, 7) is lock


async def test_sweep_locks_loop_body_survives_a_raising_sweep(db):
    cog = Tickets(_make_bot(db))
    cog._sweep_locks = MagicMock(side_effect=RuntimeError("boom"))
    await cog.sweep_locks.coro(cog)  # must not raise (review F4)
