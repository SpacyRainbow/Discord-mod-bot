import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.raid import Raid
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
