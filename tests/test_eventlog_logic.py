import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.modules.eventlog import (
    ATTACHMENTS_MAX,
    CONTENT_CHAR_CAP,
    EVENT_KINDS,
    INSERT_SQL,
    KIND_DELETE,
    KIND_EDIT,
    KIND_MESSAGE,
    KIND_REACTION_ADD,
    WATCH_ALL,
    EventLog,
    describe_attachments,
    is_watched,
    message_row,
    now_iso,
    parse_watched,
    reaction_row,
    row_values,
)


# --- the opt-in, which is the whole safety story ----------------------------

def test_nothing_is_watched_until_somebody_says_so():
    """Empty means NONE here, the opposite of llm.channels. The cost of the
    other default is a verbatim record of a conversation nobody meant to keep."""
    for raw in (None, "", "   "):
        assert parse_watched(raw) == frozenset()
        assert is_watched(123, raw) is False


def test_channels_are_opted_in_by_id():
    assert parse_watched("111,222") == frozenset({111, 222})
    assert parse_watched("111 222") == frozenset({111, 222})
    assert is_watched(111, "111,222") is True
    assert is_watched(333, "111,222") is False


def test_all_has_to_be_spelled_out():
    assert parse_watched("all") == WATCH_ALL
    assert parse_watched("ALL") == WATCH_ALL
    assert is_watched(999, "all") is True


def test_a_junk_channel_list_does_not_widen_the_watch():
    """A typo must never turn into "record everything" - it drops the entry it
    could not read and keeps the ones it could."""
    assert parse_watched("111,notanid,222") == frozenset({111, 222})
    assert parse_watched("notanid") == frozenset()
    assert is_watched(111, "notanid") is False


# --- row shape --------------------------------------------------------------

def _message(*, content="hello", bot=False, message_id=5, reference_id=None):
    message = MagicMock()
    message.id = message_id
    message.content = content
    message.author = MagicMock()
    message.author.id = 7
    message.author.display_name = "Somebody"
    message.author.bot = bot
    message.channel = MagicMock()
    message.channel.id = 11
    message.channel.name = "general"
    message.guild = MagicMock()
    message.guild.id = 13
    message.created_at = None
    message.attachments = []
    if reference_id is None:
        message.reference = None
    else:
        message.reference = MagicMock()
        message.reference.message_id = reference_id
    return message


def test_a_message_row_carries_what_a_comparison_needs():
    row = message_row(_message(reference_id=99))
    assert row["kind"] == KIND_MESSAGE
    assert (row["guild_id"], row["channel_id"], row["message_id"]) == (13, 11, 5)
    assert row["author_name"] == "Somebody"
    assert row["author_bot"] == 0
    assert row["reference_id"] == 99
    assert row["created_at"].endswith("+00:00")


def test_the_bot_s_own_messages_are_recorded_too():
    """Ground truth includes whether the reply actually posted, so its own
    messages are the half that answers that."""
    row = message_row(_message(bot=True, content="1d2: **2**"))
    assert row["author_bot"] == 1
    assert row["content"] == "1d2: **2**"


def test_content_is_capped_but_not_summarised():
    row = message_row(_message(content="x" * (CONTENT_CHAR_CAP + 500)))
    assert len(row["content"]) == CONTENT_CHAR_CAP


def test_an_edit_keeps_both_sides():
    row = message_row(_message(content="after"), KIND_EDIT, old_content="before")
    assert (row["old_content"], row["content"]) == ("before", "after")


def test_attachments_are_described_never_fetched():
    message = _message()
    attachment = MagicMock()
    attachment.filename, attachment.size, attachment.content_type = "a.png", 12, "image/png"
    message.attachments = [attachment] * (ATTACHMENTS_MAX + 3)
    described = json.loads(describe_attachments(message))
    assert len(described) == ATTACHMENTS_MAX
    assert described[0] == {"filename": "a.png", "size": 12, "content_type": "image/png"}
    # No URL: a signed Discord link expires, and a dead link in a log reads as
    # evidence when it is not.
    assert "url" not in described[0]
    assert describe_attachments(_message()) is None


def test_a_reaction_row_records_who_reacted_to_what():
    payload = MagicMock()
    payload.guild_id, payload.channel_id, payload.message_id = 13, 11, 5
    payload.user_id = 7
    payload.emoji = "\U0001f480"
    payload.member = MagicMock()
    payload.member.display_name = "Somebody"
    row = reaction_row(payload, KIND_REACTION_ADD)
    assert row["kind"] == KIND_REACTION_ADD
    assert (row["message_id"], row["actor_id"], row["emoji"]) == (5, 7, "\U0001f480")
    assert row["kind"] in EVENT_KINDS


def test_row_values_match_the_insert_statement():
    """A row built in the wrong order would write a name into the content
    column and nothing would complain, so the ordering is asserted."""
    assert INSERT_SQL.count("?") == len(row_values(message_row(_message())))
    values = row_values(message_row(_message()))
    columns = INSERT_SQL.split("(")[1].split(")")[0].split(", ")
    assert dict(zip(columns, values))["content"] == "hello"
    assert dict(zip(columns, values))["author_name"] == "Somebody"


# --- the cog ----------------------------------------------------------------

def _cog(watched="11"):
    bot = MagicMock()
    bot.stores.config.get = AsyncMock(return_value=watched)
    cog = EventLog(bot)
    cog.db = MagicMock()
    cog.db.available = True
    cog.db.conn = MagicMock()
    cog.db.conn.execute = AsyncMock()
    cog.db.conn.commit = AsyncMock()
    return cog


@pytest.mark.asyncio
async def test_a_message_in_a_watched_channel_is_recorded():
    cog = _cog()
    await cog.on_message(_message())
    cog.db.conn.execute.assert_awaited_once()
    assert cog.db.conn.execute.call_args.args[0] == INSERT_SQL


@pytest.mark.asyncio
async def test_a_message_in_an_unwatched_channel_is_not():
    cog = _cog(watched="999")
    await cog.on_message(_message())
    cog.db.conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_dm_is_never_recorded():
    cog = _cog(watched="all")
    message = _message()
    message.guild = None
    await cog.on_message(message)
    cog.db.conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_write_failure_disables_the_log_and_never_raises():
    """A debugging log that can take a message handler down with it costs more
    than it is worth."""
    cog = _cog()
    cog.db.conn.execute = AsyncMock(side_effect=RuntimeError("disk gone"))
    await cog.on_message(_message())
    assert cog.db.available is False


@pytest.mark.asyncio
async def test_a_broken_listener_never_escapes_into_discord():
    cog = _cog()
    cog.bot.stores.config.get = AsyncMock(side_effect=RuntimeError("db gone"))
    await cog.on_message(_message())        # must not raise
    cog.db.conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_uncached_delete_is_still_recorded_as_a_fact():
    """A gap in the log would be indistinguishable from the message never being
    sent, which is the exact confusion this log exists to remove."""
    cog = _cog()
    payload = MagicMock()
    payload.guild_id, payload.channel_id, payload.message_id = 13, 11, 5
    payload.cached_message = None
    await cog.on_raw_message_delete(payload)
    values = cog.db.conn.execute.call_args.args[1]
    columns = INSERT_SQL.split("(")[1].split(")")[0].split(", ")
    row = dict(zip(columns, values))
    assert row["kind"] == KIND_DELETE and row["message_id"] == 5


@pytest.mark.asyncio
async def test_an_edit_records_the_new_text_not_the_cached_one():
    """The cache holds the message as it was BEFORE the edit, so taking content
    from it would record an edit that changed nothing."""
    cog = _cog()
    payload = MagicMock()
    payload.guild_id, payload.channel_id, payload.message_id = 13, 11, 5
    payload.cached_message = _message(content="before")
    payload.data = {"content": "after"}
    await cog.on_raw_message_edit(payload)
    values = cog.db.conn.execute.call_args.args[1]
    columns = INSERT_SQL.split("(")[1].split(")")[0].split(", ")
    row = dict(zip(columns, values))
    assert (row["old_content"], row["content"]) == ("before", "after")
