from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.modules.antispam import AntiSpam
from bot.stores import Stores

GUILD = 555


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.user = MagicMock(id=999)
    bot.loop = None
    return bot


class FakeChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.delete_messages = AsyncMock()

    def __str__(self):
        return f"channel#{self.id}"


class FakeAuthor:
    def __init__(self, user_id):
        self.id = user_id
        self.bot = False
        self.guild_permissions = MagicMock(manage_messages=False)
        self.timeout = AsyncMock()
        self.mention = f"<@{user_id}>"

    def __str__(self):
        return f"user#{self.id}"


class FakeMessage:
    def __init__(self, author, channel, guild_id, content="spam", mentions=None, role_mentions=None):
        self.author = author
        self.channel = channel
        self.guild = MagicMock(id=guild_id)
        self.content = content
        self.mentions = mentions or []
        self.role_mentions = role_mentions or []


async def _configure_thresholds(bot, *, max_messages, window_seconds, max_duplicates=3, max_mentions=5):
    await bot.stores.config.set(GUILD, "spam.max_messages", str(max_messages))
    await bot.stores.config.set(GUILD, "spam.window_seconds", str(window_seconds))
    await bot.stores.config.set(GUILD, "spam.max_duplicates", str(max_duplicates))
    await bot.stores.config.set(GUILD, "spam.max_mentions", str(max_mentions))


@pytest.mark.asyncio
async def test_flooding_bulk_deletes_the_entire_burst_not_just_the_tail(db):
    bot = _make_bot(db)
    await _configure_thresholds(bot, max_messages=3, window_seconds=60)
    cog = AntiSpam(bot)
    channel = FakeChannel(1)
    bot.get_channel = MagicMock(return_value=channel)
    author = FakeAuthor(1)

    # max_messages=3 means the 4th message is what crosses the threshold -
    # the whole burst of 4 should get deleted, not just the messages after
    # the first 3 "allowed" ones. Distinct content so the duplicate-spam
    # check (also configured, default max_duplicates=3) can't trip first.
    messages = [FakeMessage(author, channel, GUILD, content=f"message {i}") for i in range(4)]
    for message in messages:
        await cog.on_message(message)

    channel.delete_messages.assert_awaited_once()
    deleted = channel.delete_messages.await_args.args[0]
    assert len(deleted) == 4
    assert deleted == messages


@pytest.mark.asyncio
async def test_history_resets_after_violation_so_next_message_does_not_immediately_retrigger(db):
    bot = _make_bot(db)
    await _configure_thresholds(bot, max_messages=2, window_seconds=60)
    cog = AntiSpam(bot)
    channel = FakeChannel(1)
    bot.get_channel = MagicMock(return_value=channel)
    author = FakeAuthor(1)

    for _ in range(3):
        await cog.on_message(FakeMessage(author, channel, GUILD))

    channel.delete_messages.assert_awaited_once()
    channel.delete_messages.reset_mock()

    await cog.on_message(FakeMessage(author, channel, GUILD))

    channel.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_burst_spanning_two_channels_gets_one_delete_call_per_channel(db):
    bot = _make_bot(db)
    await _configure_thresholds(bot, max_messages=2, window_seconds=60)
    cog = AntiSpam(bot)
    channel_a = FakeChannel(1)
    channel_b = FakeChannel(2)
    channels = {1: channel_a, 2: channel_b}
    bot.get_channel = MagicMock(side_effect=lambda cid: channels[cid])
    author = FakeAuthor(1)

    await cog.on_message(FakeMessage(author, channel_a, GUILD))
    await cog.on_message(FakeMessage(author, channel_b, GUILD))
    await cog.on_message(FakeMessage(author, channel_a, GUILD))  # crosses the threshold

    channel_a.delete_messages.assert_awaited_once()
    channel_b.delete_messages.assert_awaited_once()
    assert len(channel_a.delete_messages.await_args.args[0]) == 2
    assert len(channel_b.delete_messages.await_args.args[0]) == 1


@pytest.mark.asyncio
async def test_duplicate_spam_bulk_deletes_the_repeated_run(db):
    bot = _make_bot(db)
    await _configure_thresholds(bot, max_messages=100, window_seconds=60, max_duplicates=3)
    cog = AntiSpam(bot)
    channel = FakeChannel(1)
    bot.get_channel = MagicMock(return_value=channel)
    author = FakeAuthor(1)

    for _ in range(3):
        await cog.on_message(FakeMessage(author, channel, GUILD, content="same message"))

    channel.delete_messages.assert_awaited_once()
    assert len(channel.delete_messages.await_args.args[0]) == 3


@pytest.mark.asyncio
async def test_violation_records_a_case(db):
    bot = _make_bot(db)
    await _configure_thresholds(bot, max_messages=1, window_seconds=60)
    cog = AntiSpam(bot)
    channel = FakeChannel(1)
    bot.get_channel = MagicMock(return_value=channel)
    author = FakeAuthor(42)

    await cog.on_message(FakeMessage(author, channel, GUILD))
    await cog.on_message(FakeMessage(author, channel, GUILD))

    rows = await bot.stores.cases.for_user(GUILD, 42)
    assert len(rows) == 1
    assert rows[0][1] == "spam"
