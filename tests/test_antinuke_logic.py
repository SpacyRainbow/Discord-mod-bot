from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.antinuke import AntiNuke
from bot.stores import Stores

GUILD = 111


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    return bot


def _make_guild(owner_id=1, audit_entries=None):
    guild = MagicMock()
    guild.id = GUILD
    guild.owner_id = owner_id
    guild.audit_logs = MagicMock(return_value=_AsyncIter(audit_entries or []))
    return guild


def _make_executor(user_id, is_bot=False):
    user = MagicMock()
    user.id = user_id
    user.bot = is_bot
    user.mention = f"<@{user_id}>"
    return user


def _make_audit_entry(target_id, executor):
    entry = MagicMock()
    entry.target = MagicMock(id=target_id)
    entry.user = executor
    return entry


def _make_member_with_role(user_id, role, guild):
    member = MagicMock()
    member.id = user_id
    member.roles = [role]
    member.remove_roles = AsyncMock()
    guild.get_member = MagicMock(return_value=member)
    return member


def _make_role(is_default=False, dangerous=True):
    role = MagicMock()
    role.name = "Mod" if dangerous else "Member"
    role.is_default = MagicMock(return_value=is_default)
    perms = discord.Permissions.none()
    if dangerous:
        perms.ban_members = True
    role.permissions = perms
    return role


@pytest.mark.asyncio
async def test_disabled_by_default_takes_no_action(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    executor = _make_executor(2)
    guild = _make_guild(audit_entries=[_make_audit_entry(99, executor)])
    role = _make_role()
    member = _make_member_with_role(2, role, guild)

    for i in range(5):
        await cog.on_guild_channel_delete(MagicMock(guild=guild, id=99))

    member.remove_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_is_never_punished(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    await bot.stores.config.set(GUILD, "antinuke.action_threshold", "2")
    executor = _make_executor(1)  # matches owner_id below
    guild = _make_guild(owner_id=1, audit_entries=[_make_audit_entry(99, executor)])
    role = _make_role()
    member = _make_member_with_role(1, role, guild)

    for i in range(5):
        guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(99, executor)]))
        await cog.on_guild_channel_delete(MagicMock(guild=guild, id=99))

    member.remove_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_executor_is_never_punished(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    await bot.stores.config.set(GUILD, "antinuke.action_threshold", "2")
    executor = _make_executor(2, is_bot=True)
    guild = _make_guild(audit_entries=[])
    role = _make_role()
    member = _make_member_with_role(2, role, guild)

    for i in range(5):
        guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(99, executor)]))
        await cog.on_guild_channel_delete(MagicMock(guild=guild, id=99))

    member.remove_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_burst_past_threshold_strips_dangerous_roles(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    await bot.stores.config.set(GUILD, "antinuke.action_threshold", "3")
    executor = _make_executor(2)
    guild = _make_guild()
    role = _make_role(dangerous=True)
    member = _make_member_with_role(2, role, guild)

    for i in range(3):
        guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(99 + i, executor)]))
        await cog.on_guild_channel_delete(MagicMock(guild=guild, id=99 + i))

    member.remove_roles.assert_awaited_once_with(role, reason="Anti-nuke: mass destructive actions")


@pytest.mark.asyncio
async def test_burst_below_threshold_does_not_punish(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    await bot.stores.config.set(GUILD, "antinuke.action_threshold", "5")
    executor = _make_executor(2)
    guild = _make_guild()
    role = _make_role(dangerous=True)
    member = _make_member_with_role(2, role, guild)

    for i in range(3):
        guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(99 + i, executor)]))
        await cog.on_guild_channel_delete(MagicMock(guild=guild, id=99 + i))

    member.remove_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_dangerous_roles_are_left_alone(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    await bot.stores.config.set(GUILD, "antinuke.action_threshold", "1")
    executor = _make_executor(2)
    guild = _make_guild()
    role = _make_role(dangerous=False)
    member = _make_member_with_role(2, role, guild)
    guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(99, executor)]))

    await cog.on_guild_channel_delete(MagicMock(guild=guild, id=99))

    member.remove_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_executor_returns_none_on_forbidden(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    guild = MagicMock()
    guild.id = GUILD
    guild.audit_logs = MagicMock(side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions"))

    result = await cog._find_executor(guild, discord.AuditLogAction.channel_delete, 99)

    assert result is None


@pytest.mark.asyncio
async def test_strip_dangerous_roles_tolerates_forbidden(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    await bot.stores.config.set(GUILD, "antinuke.action_threshold", "1")
    executor = _make_executor(2)
    guild = _make_guild()
    role = _make_role(dangerous=True)
    member = _make_member_with_role(2, role, guild)
    member.remove_roles = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(99, executor)]))

    await cog.on_guild_channel_delete(MagicMock(guild=guild, id=99))  # must not raise


# ---- edge cases ----


@pytest.mark.asyncio
async def test_two_executors_each_below_threshold_do_not_combine(db):
    """Tracking is per (guild, executor), not a guild-wide aggregate - two
    different members each doing 2 destructive actions (4 total) must not
    trip a threshold of 3 that neither of them individually reached."""
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    await bot.stores.config.set(GUILD, "antinuke.action_threshold", "3")
    executor_a = _make_executor(2)
    executor_b = _make_executor(3)
    guild = _make_guild()
    role_a = _make_role(dangerous=True)
    role_b = _make_role(dangerous=True)
    member_a = MagicMock(id=2, roles=[role_a])
    member_a.remove_roles = AsyncMock()
    member_b = MagicMock(id=3, roles=[role_b])
    member_b.remove_roles = AsyncMock()
    guild.get_member = MagicMock(side_effect=lambda uid: {2: member_a, 3: member_b}.get(uid))

    for i in range(2):
        guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(90 + i, executor_a)]))
        await cog.on_guild_channel_delete(MagicMock(guild=guild, id=90 + i))
    for i in range(2):
        guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(95 + i, executor_b)]))
        await cog.on_guild_channel_delete(MagicMock(guild=guild, id=95 + i))

    member_a.remove_roles.assert_not_awaited()
    member_b.remove_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_role_failing_to_strip_does_not_block_the_other(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    await bot.stores.config.set(GUILD, "antinuke.action_threshold", "1")
    executor = _make_executor(2)
    guild = _make_guild()
    stubborn_role = _make_role(dangerous=True)
    stubborn_role.name = "Stubborn"
    removable_role = _make_role(dangerous=True)
    removable_role.name = "Removable"
    member = MagicMock(id=2, roles=[stubborn_role, removable_role])

    async def remove_roles(role, reason=None):
        if role is stubborn_role:
            raise discord.Forbidden(MagicMock(status=403), "Missing Permissions")

    member.remove_roles = AsyncMock(side_effect=remove_roles)
    guild.get_member = MagicMock(return_value=member)
    guild.audit_logs = MagicMock(return_value=_AsyncIter([_make_audit_entry(99, executor)]))

    await cog.on_guild_channel_delete(MagicMock(guild=guild, id=99))

    assert member.remove_roles.await_count == 2  # both attempted, one failed, one succeeded


# --- review F11: the audit log was queried even when anti-nuke was off ---


async def test_channel_delete_does_not_touch_the_audit_log_when_disabled(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    guild = _make_guild()
    channel = MagicMock(guild=guild, id=77)

    await cog.on_guild_channel_delete(channel)

    guild.audit_logs.assert_not_called()


async def test_role_delete_does_not_touch_the_audit_log_when_disabled(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    guild = _make_guild()
    role = MagicMock(guild=guild, id=88)

    await cog.on_guild_role_delete(role)

    guild.audit_logs.assert_not_called()


async def test_member_ban_does_not_touch_the_audit_log_when_disabled(db):
    bot = _make_bot(db)
    cog = AntiNuke(bot)
    guild = _make_guild()

    await cog.on_member_ban(guild, MagicMock(id=99))

    guild.audit_logs.assert_not_called()


async def test_channel_delete_still_queries_the_audit_log_when_enabled(db):
    bot = _make_bot(db)
    await bot.stores.config.set(GUILD, "antinuke.enabled", "true")
    cog = AntiNuke(bot)
    executor = _make_executor(5)
    guild = _make_guild(audit_entries=[_make_audit_entry(77, executor)])
    channel = MagicMock(guild=guild, id=77)

    await cog.on_guild_channel_delete(channel)

    guild.audit_logs.assert_called_once()


# --- review F10: _actions grew one entry per (guild, executor), forever ---


async def test_sweep_drops_a_stale_executor_entry(db):
    cog = AntiNuke(_make_bot(db))
    now = 10_000.0
    cog._actions[(GUILD, 5)].append(now - 7200)
    assert cog._sweep(now) == 1
    assert (GUILD, 5) not in cog._actions


async def test_sweep_keeps_a_recent_executor_entry(db):
    cog = AntiNuke(_make_bot(db))
    now = 10_000.0
    cog._actions[(GUILD, 5)].append(now - 5)
    assert cog._sweep(now) == 0
    assert (GUILD, 5) in cog._actions


async def test_sweep_loop_body_survives_a_raising_sweep(db):
    cog = AntiNuke(_make_bot(db))
    cog._sweep = MagicMock(side_effect=RuntimeError("boom"))
    await cog.sweep_actions.coro(cog)  # must not raise (review F4)
