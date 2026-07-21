from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.greetings import Greetings, format_greeting
from bot.stores import Stores

GUILD = 111


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    return bot


def _make_guild(channel=None):
    guild = MagicMock()
    guild.id = GUILD
    guild.name = "Test Server"
    guild.member_count = 42
    guild.get_channel = MagicMock(return_value=channel)
    return guild


def _make_member(guild):
    member = MagicMock()
    member.guild = guild
    member.mention = "<@2>"
    member.__str__.return_value = "user#2"
    return member


def test_format_greeting_substitutes_all_placeholders():
    template = "Hi {member} ({member_name}) - {server} has {member_count} members"
    text = format_greeting(template, "user#2", "<@2>", "Test Server", 42)
    assert text == "Hi <@2> (user#2) - Test Server has 42 members"


def test_format_greeting_leaves_unknown_placeholders_alone():
    assert format_greeting("{unknown}", "n", "m", "s", 1) == "{unknown}"


@pytest.mark.asyncio
async def test_join_does_nothing_when_no_channel_configured(db):
    bot = _make_bot(db)
    cog = Greetings(bot)
    guild = _make_guild()
    member = _make_member(guild)

    await cog.on_member_join(member)  # must not raise, nothing to assert on


@pytest.mark.asyncio
async def test_join_posts_default_welcome_message(db):
    bot = _make_bot(db)
    cog = Greetings(bot)
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = _make_guild(channel)
    await bot.stores.config.set(GUILD, "welcome.channel_id", "555")
    member = _make_member(guild)

    await cog.on_member_join(member)

    channel.send.assert_awaited_once()
    assert "<@2>" in channel.send.await_args.args[0]
    assert "Test Server" in channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_join_uses_custom_template(db):
    bot = _make_bot(db)
    cog = Greetings(bot)
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = _make_guild(channel)
    await bot.stores.config.set(GUILD, "welcome.channel_id", "555")
    await bot.stores.config.set(GUILD, "welcome.message", "Hey {member_name}!")
    member = _make_member(guild)

    await cog.on_member_join(member)

    assert channel.send.await_args.args[0] == "Hey user#2!"


@pytest.mark.asyncio
async def test_leave_posts_default_message(db):
    bot = _make_bot(db)
    cog = Greetings(bot)
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = _make_guild(channel)
    await bot.stores.config.set(GUILD, "leave.channel_id", "555")
    member = _make_member(guild)

    await cog.on_member_remove(member)

    channel.send.assert_awaited_once()
    assert "user#2" in channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_join_tolerates_forbidden(db):
    bot = _make_bot(db)
    cog = Greetings(bot)
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions"))
    guild = _make_guild(channel)
    await bot.stores.config.set(GUILD, "welcome.channel_id", "555")
    member = _make_member(guild)

    await cog.on_member_join(member)  # must not raise


@pytest.mark.asyncio
async def test_join_does_nothing_when_configured_channel_no_longer_exists(db):
    bot = _make_bot(db)
    cog = Greetings(bot)
    guild = _make_guild(channel=None)  # get_channel returns None
    await bot.stores.config.set(GUILD, "welcome.channel_id", "555")
    member = _make_member(guild)

    await cog.on_member_join(member)  # must not raise
