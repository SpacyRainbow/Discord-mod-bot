import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.moderation import Moderation, _duration_to_timedelta, is_permanent_duration
from bot.stores import Stores

GUILD = 999


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.wait_until_ready = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)
    return bot


def _make_cog(bot):
    """Cancels the real 60s mute_expiry_check loop right away - otherwise it
    starts running for real against the test's mocks (and can race a test
    that schedules a due-now expiration itself, e.g. double-calling
    remove_roles). Tests that want to exercise the expiry logic call
    `cog.mute_expiry_check.coro(cog)` directly instead."""
    cog = Moderation(bot)
    cog.mute_expiry_check.cancel()
    return cog


def _make_guild(existing_role=None):
    guild = MagicMock()
    guild.id = GUILD
    guild.text_channels = []
    guild.get_role = MagicMock(return_value=existing_role)
    created_role = MagicMock()
    created_role.id = 4242
    guild.create_role = AsyncMock(return_value=created_role)
    return guild


def _make_ctx(guild=None):
    ctx = MagicMock()
    ctx.guild = guild or _make_guild()
    ctx.author.id = 1
    ctx.send = AsyncMock()
    return ctx


def _make_member(member_id=2):
    member = MagicMock()
    member.id = member_id
    member.__str__.return_value = f"user#{member_id}"
    member.kick = AsyncMock()
    member.ban = AsyncMock()
    member.timeout = AsyncMock()
    member.send = AsyncMock()
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


def test_duration_to_timedelta_minutes():
    assert _duration_to_timedelta("10m").total_seconds() == 600


def test_duration_to_timedelta_hours():
    assert _duration_to_timedelta("2h").total_seconds() == 7200


def test_duration_to_timedelta_days():
    assert _duration_to_timedelta("1d").total_seconds() == 86400


def test_duration_to_timedelta_rejects_unknown_unit():
    with pytest.raises(ValueError):
        _duration_to_timedelta("10x")


def test_duration_to_timedelta_rejects_non_numeric_amount():
    with pytest.raises(ValueError):
        _duration_to_timedelta("xm")


@pytest.mark.asyncio
async def test_kick_calls_discord_and_records_case(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()

    await Moderation.kick.callback(cog, ctx, member, reason="testing")

    member.kick.assert_awaited_once_with(reason="testing")
    rows = await cog.bot.stores.cases.for_user(GUILD, member.id)
    assert len(rows) == 1
    assert rows[0][1] == "kick"
    assert rows[0][2] == "testing"
    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_ban_calls_discord_and_records_case(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()

    await Moderation.ban.callback(cog, ctx, member, reason="testing")

    member.ban.assert_awaited_once_with(reason="testing")
    rows = await cog.bot.stores.cases.for_user(GUILD, member.id)
    assert rows[0][1] == "ban"


@pytest.mark.asyncio
async def test_mute_applies_timeout_and_records_duration_in_reason(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()

    await Moderation.mute.callback(cog, ctx, member, "2h", reason="loud")

    member.timeout.assert_awaited_once()
    rows = await cog.bot.stores.cases.for_user(GUILD, member.id)
    assert rows[0][1] == "mute"
    assert rows[0][2] == "loud (2h)"


@pytest.mark.asyncio
async def test_mute_rejects_bad_duration_without_touching_discord_or_db(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()

    await Moderation.mute.callback(cog, ctx, member, "10x", reason="loud")

    member.timeout.assert_not_awaited()
    rows = await cog.bot.stores.cases.for_user(GUILD, member.id)
    assert rows == []
    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_unmute_clears_timeout(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()

    await Moderation.unmute.callback(cog, ctx, member)

    member.timeout.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_warn_records_case_and_dms_member(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()

    await Moderation.warn.callback(cog, ctx, member, reason="be nice")

    member.send.assert_awaited_once()
    rows = await cog.bot.stores.cases.for_user(GUILD, member.id)
    assert rows[0][1] == "warn"
    assert rows[0][2] == "be nice"


@pytest.mark.asyncio
async def test_warn_succeeds_even_if_member_has_dms_closed(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()
    member.send.side_effect = discord.Forbidden(MagicMock(status=403), "Cannot send messages to this user")

    await Moderation.warn.callback(cog, ctx, member, reason="be nice")

    rows = await cog.bot.stores.cases.for_user(GUILD, member.id)
    assert len(rows) == 1  # case history still recorded despite the DM failing


@pytest.mark.asyncio
async def test_cases_reports_no_history_for_clean_member(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()

    await Moderation.cases.callback(cog, ctx, member)

    ctx.send.assert_awaited_once()
    assert "No case history" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_case_looks_up_a_specific_case_by_id(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()
    member = _make_member()
    await Moderation.warn.callback(cog, ctx, member, reason="first offense")

    ctx.send.reset_mock()
    await Moderation.case.callback(cog, ctx, 1)

    ctx.send.assert_awaited_once()
    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.title == "Case #1"


@pytest.mark.asyncio
async def test_case_reports_missing_case_id(db):
    cog = _make_cog(_make_bot(db))
    ctx = _make_ctx()

    await Moderation.case.callback(cog, ctx, 999)

    ctx.send.assert_awaited_once()
    assert "No case #999" in ctx.send.await_args.args[0]


def test_is_permanent_duration_none_is_permanent():
    assert is_permanent_duration(None) is True


def test_is_permanent_duration_keywords_are_permanent():
    assert is_permanent_duration("perm") is True
    assert is_permanent_duration("PERMANENT") is True
    assert is_permanent_duration("indefinite") is True
    assert is_permanent_duration("Forever") is True


def test_is_permanent_duration_normal_duration_is_not_permanent():
    assert is_permanent_duration("10m") is False
    assert is_permanent_duration("45d") is False


@pytest.mark.asyncio
async def test_ensure_mute_role_creates_and_persists_on_first_call(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    guild = _make_guild()

    role = await cog.ensure_mute_role(guild)

    guild.create_role.assert_awaited_once()
    assert role.id == 4242
    stored = await bot.stores.config.get_int(GUILD, "moderation.mute_role", 0)
    assert stored == 4242


@pytest.mark.asyncio
async def test_ensure_mute_role_reuses_existing_role_on_second_call(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    guild = _make_guild()

    first = await cog.ensure_mute_role(guild)
    guild.get_role = MagicMock(return_value=first)  # simulate the role now existing
    second = await cog.ensure_mute_role(guild)

    guild.create_role.assert_awaited_once()  # not called again
    assert second is first


@pytest.mark.asyncio
async def test_mute_with_no_duration_uses_role_and_no_expiration(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    guild = _make_guild()
    ctx = _make_ctx(guild=guild)
    member = _make_member()

    await Moderation.mute.callback(cog, ctx, member, None, reason="loud")

    member.add_roles.assert_awaited_once()
    member.timeout.assert_not_awaited()
    due = await bot.stores.mutes.due(discord.utils.utcnow().isoformat())
    assert due == []
    rows = await bot.stores.cases.for_user(GUILD, member.id)
    assert rows[0][2] == "loud (indefinite)"


@pytest.mark.asyncio
async def test_mute_perm_keyword_also_uses_role(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    guild = _make_guild()
    ctx = _make_ctx(guild=guild)
    member = _make_member()

    await Moderation.mute.callback(cog, ctx, member, "perm", reason="loud")

    member.add_roles.assert_awaited_once()
    member.timeout.assert_not_awaited()


@pytest.mark.asyncio
async def test_mute_over_28_days_uses_role_with_scheduled_expiration(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    guild = _make_guild()
    ctx = _make_ctx(guild=guild)
    member = _make_member()

    await Moderation.mute.callback(cog, ctx, member, "45d", reason="loud")

    member.add_roles.assert_awaited_once()
    member.timeout.assert_not_awaited()
    far_future = (discord.utils.utcnow() + datetime.timedelta(days=100)).isoformat()
    due = await bot.stores.mutes.due(far_future)
    assert due == [(GUILD, member.id)]


@pytest.mark.asyncio
async def test_mute_under_28_days_still_uses_native_timeout(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    guild = _make_guild()
    ctx = _make_ctx(guild=guild)
    member = _make_member()

    await Moderation.mute.callback(cog, ctx, member, "10m", reason="loud")

    member.timeout.assert_awaited_once()
    member.add_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmute_clears_role_and_pending_expiration_unconditionally(db):
    bot = _make_bot(db)
    cog = _make_cog(bot)
    mute_role = MagicMock(id=4242)
    guild = _make_guild(existing_role=mute_role)
    ctx = _make_ctx(guild=guild)
    member = _make_member()
    await bot.stores.config.set(GUILD, "moderation.mute_role", str(mute_role.id))
    await bot.stores.mutes.schedule(GUILD, member.id, discord.utils.utcnow().isoformat())

    await Moderation.unmute.callback(cog, ctx, member)

    member.timeout.assert_awaited_once_with(None)
    member.remove_roles.assert_awaited_once_with(mute_role, reason="Unmuted")
    due = await bot.stores.mutes.due((discord.utils.utcnow() + datetime.timedelta(days=1)).isoformat())
    assert due == []


@pytest.mark.asyncio
async def test_mute_expiry_check_lifts_due_mutes(db):
    bot = _make_bot(db)
    mute_role = MagicMock(id=4242)
    guild = _make_guild(existing_role=mute_role)
    bot.get_guild = MagicMock(return_value=guild)
    member = _make_member()
    guild.get_member = MagicMock(return_value=member)
    cog = _make_cog(bot)
    await bot.stores.config.set(GUILD, "moderation.mute_role", str(mute_role.id))
    past = (discord.utils.utcnow() - datetime.timedelta(seconds=1)).isoformat()
    await bot.stores.mutes.schedule(GUILD, member.id, past)

    await cog.mute_expiry_check.coro(cog)

    member.remove_roles.assert_awaited_once_with(mute_role, reason="Mute expired")
    due = await bot.stores.mutes.due((discord.utils.utcnow() + datetime.timedelta(days=1)).isoformat())
    assert due == []
