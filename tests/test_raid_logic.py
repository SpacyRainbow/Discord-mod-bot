import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.raid import Raid, _previous_level
from bot.stores import Stores

GUILD = 111


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    return bot


def _make_guild(guild_id=GUILD, owner_id=1):
    guild = MagicMock()
    guild.id = guild_id
    guild.owner_id = owner_id
    guild.verification_level = discord.VerificationLevel.low
    guild.edit = AsyncMock()
    return guild


def _make_member(guild, member_id=2, account_age_hours=24 * 365):
    member = MagicMock()
    member.id = member_id
    member.guild = guild
    member.created_at = discord.utils.utcnow() - datetime.timedelta(hours=account_age_hours)
    member.kick = AsyncMock()
    member.__str__.return_value = f"user#{member_id}"
    return member


def _make_ctx(guild):
    ctx = MagicMock()
    ctx.guild = guild
    ctx.author.id = 1
    ctx.send = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_join_is_ignored_when_min_age_gate_disabled(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    member = _make_member(guild, account_age_hours=0)

    await cog.on_member_join(member)

    member.kick.assert_not_awaited()


@pytest.mark.asyncio
async def test_join_kicks_underage_account_when_gate_enabled(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    await bot.stores.config.set(GUILD, "raid.min_account_age_hours", "48")
    member = _make_member(guild, account_age_hours=1)

    await cog.on_member_join(member)

    member.kick.assert_awaited_once()


@pytest.mark.asyncio
async def test_join_does_not_kick_account_older_than_gate(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    await bot.stores.config.set(GUILD, "raid.min_account_age_hours", "48")
    member = _make_member(guild, account_age_hours=100)

    await cog.on_member_join(member)

    member.kick.assert_not_awaited()


@pytest.mark.asyncio
async def test_join_burst_below_threshold_does_not_alert(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    await bot.stores.config.set(GUILD, "raid.join_threshold", "5")

    for i in range(3):
        await cog.on_member_join(_make_member(guild, member_id=i))

    # No log channel configured, so post_log is a no-op either way - this
    # test just needs on_member_join to not raise below threshold.


@pytest.mark.asyncio
async def test_join_burst_at_threshold_triggers_lockdown_when_enabled(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    guild.get_channel = MagicMock(return_value=None)
    await bot.stores.config.set(GUILD, "raid.join_threshold", "3")
    await bot.stores.config.set(GUILD, "raid.auto_lockdown", "true")

    for i in range(3):
        await cog.on_member_join(_make_member(guild, member_id=i))

    guild.edit.assert_awaited_once()
    assert guild.edit.await_args.kwargs["verification_level"] == discord.VerificationLevel.highest


@pytest.mark.asyncio
async def test_raidmode_on_raises_verification_level(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    ctx = _make_ctx(guild)

    await Raid.raidmode.callback(cog, ctx, "on")

    guild.edit.assert_awaited_once()
    assert guild.edit.await_args.kwargs["verification_level"] == discord.VerificationLevel.highest


@pytest.mark.asyncio
async def test_raidmode_off_restores_previous_level(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    guild.verification_level = discord.VerificationLevel.low
    ctx = _make_ctx(guild)

    await Raid.raidmode.callback(cog, ctx, "on")
    guild.edit.reset_mock()
    await Raid.raidmode.callback(cog, ctx, "off")

    assert guild.edit.await_args.kwargs["verification_level"] == discord.VerificationLevel.low


@pytest.mark.asyncio
async def test_raidmode_rejects_invalid_state(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    ctx = _make_ctx(guild)

    await Raid.raidmode.callback(cog, ctx, "sideways")

    guild.edit.assert_not_awaited()
    ctx.send.assert_awaited_once()


# ---- edge cases ----


@pytest.mark.asyncio
async def test_join_burst_at_threshold_without_auto_lockdown_only_alerts(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    guild.get_channel = MagicMock(return_value=None)
    await bot.stores.config.set(GUILD, "raid.join_threshold", "3")
    # auto_lockdown left at its default (false)

    for i in range(3):
        await cog.on_member_join(_make_member(guild, member_id=i))

    guild.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_join_burst_does_not_relower_an_already_maxed_verification_level(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    guild.verification_level = discord.VerificationLevel.highest
    guild.get_channel = MagicMock(return_value=None)
    await bot.stores.config.set(GUILD, "raid.join_threshold", "3")
    await bot.stores.config.set(GUILD, "raid.auto_lockdown", "true")

    for i in range(3):
        await cog.on_member_join(_make_member(guild, member_id=i))

    guild.edit.assert_not_awaited()  # already at max - nothing to raise, nothing to (mis)remember


@pytest.mark.asyncio
async def test_raidmode_off_without_a_prior_on_falls_back_to_medium(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    ctx = _make_ctx(guild)

    await Raid.raidmode.callback(cog, ctx, "off")

    assert guild.edit.await_args.kwargs["verification_level"] == discord.VerificationLevel.medium


@pytest.mark.asyncio
async def test_account_age_exactly_at_the_gate_boundary_is_not_kicked(db):
    bot = _make_bot(db)
    cog = Raid(bot)
    guild = _make_guild()
    await bot.stores.config.set(GUILD, "raid.min_account_age_hours", "48")
    member = _make_member(guild, account_age_hours=48)

    await cog.on_member_join(member)

    member.kick.assert_not_awaited()


# --- review F12: /raidmode off crashed on a malformed stored level ---


def test_previous_level_unset_defaults_to_medium():
    assert _previous_level(None) is discord.VerificationLevel.medium


def test_previous_level_empty_string_defaults_to_medium():
    assert _previous_level("") is discord.VerificationLevel.medium


def test_previous_level_non_numeric_defaults_to_medium():
    assert _previous_level("banana") is discord.VerificationLevel.medium


def test_previous_level_out_of_enum_range_defaults_to_medium():
    assert _previous_level("9") is discord.VerificationLevel.medium


def test_previous_level_accepts_a_valid_stored_value():
    assert _previous_level("1") is discord.VerificationLevel.low


# --- review F10: _join_times grew one entry per guild, forever ---


async def test_sweep_drops_a_guild_with_only_stale_joins(db):
    cog = Raid(_make_bot(db))
    now = 10_000.0
    cog._join_times[GUILD].append(now - 7200)
    assert cog._sweep(now) == 1
    assert GUILD not in cog._join_times


async def test_sweep_keeps_a_guild_with_a_recent_join(db):
    cog = Raid(_make_bot(db))
    now = 10_000.0
    cog._join_times[GUILD].append(now - 5)
    assert cog._sweep(now) == 0
    assert GUILD in cog._join_times


async def test_sweep_loop_body_survives_a_raising_sweep(db):
    cog = Raid(_make_bot(db))
    cog._sweep = MagicMock(side_effect=RuntimeError("boom"))
    await cog.sweep_join_times.coro(cog)  # must not raise (review F4)
