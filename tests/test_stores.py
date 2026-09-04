import asyncio
import datetime

import pytest

from bot.stores import ChannelLockStore, Stores

GUILD = 123456


@pytest.mark.asyncio
async def test_config_get_returns_default_when_unset(db):
    stores = Stores(db)
    assert await stores.config.get(GUILD, "nope", "fallback") == "fallback"


@pytest.mark.asyncio
async def test_config_set_then_get(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "commandprefix", "?")
    assert await stores.config.get(GUILD, "commandprefix") == "?"


@pytest.mark.asyncio
async def test_config_get_degrades_gracefully_when_db_down(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "commandprefix", "?")
    db.available = False  # simulate a dropped connection
    assert await stores.config.get(GUILD, "commandprefix", "!") == "!"


@pytest.mark.asyncio
async def test_config_set_raises_when_db_down(db):
    stores = Stores(db)
    db.available = False
    with pytest.raises(RuntimeError):
        await stores.config.set(GUILD, "commandprefix", "?")


@pytest.mark.asyncio
async def test_config_delete_removes_key_so_get_falls_back_to_default(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "commandprefix", "?")
    await stores.config.delete(GUILD, "commandprefix")
    assert await stores.config.get(GUILD, "commandprefix", "!") == "!"


@pytest.mark.asyncio
async def test_config_delete_raises_when_db_down(db):
    stores = Stores(db)
    db.available = False
    with pytest.raises(RuntimeError):
        await stores.config.delete(GUILD, "commandprefix")


@pytest.mark.asyncio
async def test_case_add_and_lookup(db):
    stores = Stores(db)
    case_id = await stores.cases.add(GUILD, user_id=1, moderator_id=2, action="warn", reason="test")
    row = await stores.cases.get(GUILD, case_id)
    assert row[2] == "warn"
    assert row[3] == "test"


@pytest.mark.asyncio
async def test_case_for_user_returns_history(db):
    stores = Stores(db)
    await stores.cases.add(GUILD, user_id=1, moderator_id=2, action="warn", reason="first")
    await stores.cases.add(GUILD, user_id=1, moderator_id=2, action="warn", reason="second")
    rows = await stores.cases.for_user(GUILD, 1)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_filter_add_remove_and_list(db):
    stores = Stores(db)
    await stores.filters.add(GUILD, "badword")
    assert "badword" in await stores.filters.all(GUILD)
    await stores.filters.remove(GUILD, "badword")
    assert "badword" not in await stores.filters.all(GUILD)


@pytest.mark.asyncio
async def test_tag_set_and_get_is_case_insensitive(db):
    stores = Stores(db)
    await stores.tags.set(GUILD, "Welcome", "Hi there!", created_by=1)
    assert await stores.tags.get(GUILD, "welcome") == "Hi there!"


@pytest.mark.asyncio
async def test_bucket_add_and_random_returns_saved_item(db):
    stores = Stores(db)
    await stores.buckets.add(GUILD, "greetings", "hello")
    assert await stores.buckets.random(GUILD, "greetings") == "hello"


@pytest.mark.asyncio
async def test_bucket_random_returns_none_when_empty(db):
    stores = Stores(db)
    assert await stores.buckets.random(GUILD, "nonexistent") is None


@pytest.mark.asyncio
async def test_case_delete_removes_row(db):
    stores = Stores(db)
    case_id = await stores.cases.add(GUILD, user_id=1, moderator_id=2, action="warn", reason="test")
    await stores.cases.delete(GUILD, case_id)
    assert await stores.cases.get(GUILD, case_id) is None


@pytest.mark.asyncio
async def test_case_update_reason_changes_only_reason(db):
    stores = Stores(db)
    case_id = await stores.cases.add(GUILD, user_id=1, moderator_id=2, action="warn", reason="old")
    await stores.cases.update_reason(GUILD, case_id, "new")
    row = await stores.cases.get(GUILD, case_id)
    assert row[3] == "new"
    assert row[2] == "warn"  # action untouched


@pytest.mark.asyncio
async def test_scheduled_task_add_and_due(db):
    stores = Stores(db)
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    await stores.scheduled.add(GUILD, "reminder", {"a": 1}, past)
    await stores.scheduled.add(GUILD, "reminder", {"b": 2}, future)

    due = await stores.scheduled.due(datetime.datetime.now(datetime.timezone.utc).isoformat())

    assert len(due) == 1
    assert due[0][3] == {"a": 1}


@pytest.mark.asyncio
async def test_scheduled_task_mark_done_excludes_it_from_due(db):
    stores = Stores(db)
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    task_id = await stores.scheduled.add(GUILD, "reminder", {}, past)

    await stores.scheduled.mark_done(task_id)

    due = await stores.scheduled.due(datetime.datetime.now(datetime.timezone.utc).isoformat())
    assert due == []


def test_channel_lock_encode_decode_round_trip():
    assert ChannelLockStore.decode(ChannelLockStore.encode(True)) is True
    assert ChannelLockStore.decode(ChannelLockStore.encode(False)) is False
    assert ChannelLockStore.decode(ChannelLockStore.encode(None)) is None


@pytest.mark.asyncio
async def test_channel_lock_set_get_clear(db):
    stores = Stores(db)
    await stores.channel_locks.set(GUILD, 55, True)
    assert await stores.channel_locks.get(GUILD, 55) == "true"
    await stores.channel_locks.clear(GUILD, 55)
    assert await stores.channel_locks.get(GUILD, 55) is None


@pytest.mark.asyncio
async def test_starboard_set_and_get(db):
    stores = Stores(db)
    await stores.starboard.set(GUILD, 111, 222)
    assert await stores.starboard.get(GUILD, 111) == 222
    assert await stores.starboard.get(GUILD, 999) is None


@pytest.mark.asyncio
async def test_giveaway_toggle_entry_enters_then_leaves(db):
    stores = Stores(db)
    end_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    giveaway_id = await stores.giveaways.create(GUILD, 1, 2, "Prize", 1, 9, end_at)

    entered = await stores.giveaways.toggle_entry(giveaway_id, 42)
    assert entered is True
    assert await stores.giveaways.entries(giveaway_id) == [42]

    entered_again = await stores.giveaways.toggle_entry(giveaway_id, 42)
    assert entered_again is False
    assert await stores.giveaways.entries(giveaway_id) == []


@pytest.mark.asyncio
async def test_poll_set_vote_switch_and_retract(db):
    stores = Stores(db)
    poll_id = await stores.polls.create(GUILD, 1, 2, "Q?", ["A", "B"], None)

    result = await stores.polls.set_vote(poll_id, 42, 0)
    assert result == "voted"
    assert await stores.polls.vote_counts(poll_id, 2) == [1, 0]

    result = await stores.polls.set_vote(poll_id, 42, 1)
    assert result == "voted"
    assert await stores.polls.vote_counts(poll_id, 2) == [0, 1]

    result = await stores.polls.set_vote(poll_id, 42, 1)
    assert result == "retracted"
    assert await stores.polls.vote_counts(poll_id, 2) == [0, 0]


@pytest.mark.asyncio
async def test_ticket_create_get_and_close(db):
    stores = Stores(db)
    await stores.tickets.create(GUILD, 100, 42)
    assert await stores.tickets.get_by_channel(GUILD, 100) is not None
    assert await stores.tickets.get_open_for_user(GUILD, 42) is not None

    await stores.tickets.close(GUILD, 100)

    assert await stores.tickets.get_by_channel(GUILD, 100) is None
    assert await stores.tickets.get_open_for_user(GUILD, 42) is None


# --- review F3: the _Store failure contract -------------------------------
#
# A "broken" DB here means the connection object is still present but every
# query against it raises - the case the old code never noticed, because it
# left db.available True and the watchdog was never told to reconnect.


async def _break_connection(db):
    """Closes the real connection but leaves db.available True, so every
    subsequent query raises the way a dropped connection would."""
    await db.conn.close()
    db.available = True


@pytest.mark.asyncio
async def test_failed_row_read_returns_default_and_flags_db_unavailable(db):
    stores = Stores(db)
    await _break_connection(db)

    assert await stores.config.get(GUILD, "commandprefix", "!") == "!"
    assert db.available is False


@pytest.mark.asyncio
async def test_failed_list_read_returns_empty_and_flags_db_unavailable(db):
    stores = Stores(db)
    await _break_connection(db)

    assert await stores.filters.all(GUILD) == []
    assert db.available is False


@pytest.mark.asyncio
async def test_failed_get_all_returns_empty_dict_and_flags_db_unavailable(db):
    stores = Stores(db)
    await _break_connection(db)

    assert await stores.config.get_all(GUILD) == {}
    assert db.available is False


@pytest.mark.asyncio
async def test_failed_write_raises_runtime_error_and_flags_db_unavailable(db):
    stores = Stores(db)
    await _break_connection(db)

    with pytest.raises(RuntimeError, match="config not saved"):
        await stores.config.set(GUILD, "commandprefix", "?")
    assert db.available is False


@pytest.mark.asyncio
async def test_write_preserves_its_specific_failure_message(db):
    stores = Stores(db)
    db.available = False

    with pytest.raises(RuntimeError, match="Database unavailable, case not recorded"):
        await stores.cases.add(GUILD, 1, 2, "ban", "reason")


@pytest.mark.asyncio
async def test_loop_writes_return_silently_when_db_is_down(db):
    """mark_done / mark_ended / close are called from tasks.loop bodies, where
    raising would be harmful - they must stay quiet."""
    stores = Stores(db)
    db.available = False

    assert await stores.scheduled.mark_done(1) is None
    assert await stores.giveaways.mark_ended(1) is None
    assert await stores.polls.close(1) is None


@pytest.mark.asyncio
async def test_scheduled_due_skips_rows_with_malformed_payload(db):
    stores = Stores(db)
    now = datetime.datetime.now(datetime.timezone.utc)
    past = now - datetime.timedelta(minutes=5)

    good_id = await stores.scheduled.add(GUILD, "remind", {"text": "hi"}, past)
    # Write a corrupt payload straight past the store, as a bad migration or a
    # truncated write would leave behind.
    await db.conn.execute(
        "INSERT INTO scheduled_tasks (guild_id, kind, payload, run_at) VALUES (?, ?, ?, ?)",
        (GUILD, "remind", "{not json", past.isoformat()),
    )
    await db.conn.commit()

    due = await stores.scheduled.due(now.isoformat())

    # The good row still comes back; the corrupt one is skipped, not fatal.
    assert [t[0] for t in due] == [good_id]
    assert due[0][3] == {"text": "hi"}


@pytest.mark.asyncio
async def test_reads_return_defaults_without_raising_when_db_is_down(db):
    stores = Stores(db)
    db.available = False

    assert await stores.cases.for_user(GUILD, 1) == []
    assert await stores.cases.get(GUILD, 1) is None
    assert await stores.giveaways.get(1) is None
    assert await stores.giveaways.entries(1) == []
    assert await stores.polls.open_polls() == []
    assert await stores.polls.vote_counts(1, 3) == [0, 0, 0]
    assert await stores.tickets.get_by_channel(GUILD, 1) is None


# --- review F9: check-then-write races -----------------------------------

@pytest.mark.asyncio
async def test_concurrent_toggle_entry_does_not_raise_and_leaves_consistent_state(db):
    """Two fast Enter clicks used to both SELECT "not entered" and both INSERT,
    raising IntegrityError on the (giveaway_id, user_id) PK, which no caller
    catches. Delete-first makes exactly one branch win."""
    stores = Stores(db)
    now = datetime.datetime.now(datetime.timezone.utc)
    gid = await stores.giveaways.create(GUILD, 1, 2, "prize", 1, 3, now)

    results = await asyncio.gather(
        stores.giveaways.toggle_entry(gid, 99),
        stores.giveaways.toggle_entry(gid, 99),
    )

    # The contract is: no IntegrityError, never a duplicate row, and the value
    # each caller was told matches reality. Both clicks reporting "you're in"
    # (both DELETEs miss, then both INSERT OR IGNORE) is a legitimate outcome
    # and a truthful one - unlike the old code, which raised.
    entries = await stores.giveaways.entries(gid)
    assert len(entries) == len(set(entries))
    assert entries == ([99] if results[-1] else [])


@pytest.mark.asyncio
async def test_toggle_entry_round_trips_and_never_duplicates(db):
    stores = Stores(db)
    now = datetime.datetime.now(datetime.timezone.utc)
    gid = await stores.giveaways.create(GUILD, 1, 2, "prize", 1, 3, now)

    assert await stores.giveaways.toggle_entry(gid, 99) is True
    assert await stores.giveaways.entries(gid) == [99]
    assert await stores.giveaways.toggle_entry(gid, 99) is False
    assert await stores.giveaways.entries(gid) == []
    assert await stores.giveaways.toggle_entry(gid, 99) is True
    assert await stores.giveaways.entries(gid) == [99]


@pytest.mark.asyncio
async def test_toggle_entry_still_raises_when_db_down(db):
    stores = Stores(db)
    db.available = False
    with pytest.raises(RuntimeError):
        await stores.giveaways.toggle_entry(1, 99)


@pytest.mark.asyncio
async def test_concurrent_starboard_set_does_not_raise(db):
    """Two reactions crossing the threshold together both reach set(); the
    losing write must overwrite rather than raise on the PK."""
    stores = Stores(db)
    await asyncio.gather(
        stores.starboard.set(GUILD, 555, 1001),
        stores.starboard.set(GUILD, 555, 1002),
    )
    assert await stores.starboard.get(GUILD, 555) in (1001, 1002)


@pytest.mark.asyncio
async def test_starboard_set_overwrites_instead_of_raising(db):
    stores = Stores(db)
    await stores.starboard.set(GUILD, 555, 1001)
    await stores.starboard.set(GUILD, 555, 1002)  # used to raise IntegrityError
    assert await stores.starboard.get(GUILD, 555) == 1002


@pytest.mark.asyncio
async def test_set_vote_round_trips_vote_switch_and_retract(db):
    """The delete-first rewrite must preserve all three behaviours."""
    stores = Stores(db)
    now = datetime.datetime.now(datetime.timezone.utc)
    pid = await stores.polls.create(GUILD, 1, 2, "Q?", ["a", "b"], now)

    assert await stores.polls.set_vote(pid, 99, 0) == "voted"
    assert await stores.polls.vote_counts(pid, 2) == [1, 0]
    # Switching to another option is a vote, not a retract.
    assert await stores.polls.set_vote(pid, 99, 1) == "voted"
    assert await stores.polls.vote_counts(pid, 2) == [0, 1]
    # Clicking the same option again retracts.
    assert await stores.polls.set_vote(pid, 99, 1) == "retracted"
    assert await stores.polls.vote_counts(pid, 2) == [0, 0]


@pytest.mark.asyncio
async def test_concurrent_set_vote_does_not_raise(db):
    stores = Stores(db)
    now = datetime.datetime.now(datetime.timezone.utc)
    pid = await stores.polls.create(GUILD, 1, 2, "Q?", ["a", "b"], now)

    await asyncio.gather(
        stores.polls.set_vote(pid, 99, 0),
        stores.polls.set_vote(pid, 99, 1),
    )
    # One user, at most one vote row, whichever option won.
    assert sum(await stores.polls.vote_counts(pid, 2)) <= 1


@pytest.mark.asyncio
async def test_set_vote_still_raises_when_db_down(db):
    stores = Stores(db)
    db.available = False
    with pytest.raises(RuntimeError):
        await stores.polls.set_vote(1, 99, 0)


# --- review F14: get_int bounds ---


async def test_get_int_returns_value_inside_bounds(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "spam.window_seconds", "30")
    assert await stores.config.get_int(GUILD, "spam.window_seconds", 6, minimum=1, maximum=3600) == 30


async def test_get_int_rejects_value_below_minimum(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "spam.window_seconds", "-1")
    assert await stores.config.get_int(GUILD, "spam.window_seconds", 6, minimum=1, maximum=3600) == 6


async def test_get_int_rejects_value_above_maximum(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "spam.timeout_seconds", "999999999")
    got = await stores.config.get_int(GUILD, "spam.timeout_seconds", 300, minimum=1, maximum=2419200)
    assert got == 300


async def test_get_int_without_bounds_still_accepts_anything_parseable(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "logging.channel", "-5")
    assert await stores.config.get_int(GUILD, "logging.channel", 0) == -5


async def test_get_int_falls_back_to_default_on_unparseable_value(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "starboard.threshold", "banana")
    assert await stores.config.get_int(GUILD, "starboard.threshold", 3, minimum=1, maximum=100) == 3


async def test_get_int_boundaries_are_inclusive(db):
    stores = Stores(db)
    await stores.config.set(GUILD, "automod.caps_threshold", "0")
    assert await stores.config.get_int(GUILD, "automod.caps_threshold", 70, minimum=0, maximum=100) == 0
    await stores.config.set(GUILD, "automod.caps_threshold", "100")
    assert await stores.config.get_int(GUILD, "automod.caps_threshold", 70, minimum=0, maximum=100) == 100


# --- the LLM exchange log -----------------------------------------------------


async def _log(stores, **overrides):
    row = dict(
        guild_id=GUILD,
        channel_id=555,
        channel_name="general",
        user_id=77,
        user_name="bob",
        prompt="hello",
        reply="hi",
        tool_calls=[],
        rounds=1,
        duration_ms=1234,
        model="qwen38-27b-stock",
        status="ok",
        error=None,
    )
    row.update(overrides)
    await stores.llm_log.add(**row)


async def test_llm_log_round_trips_an_exchange(db):
    stores = Stores(db)
    await _log(stores, tool_calls=[{"name": "read_recent_messages", "arguments": "{}"}])
    rows = await stores.llm_log.recent_for_guild(GUILD, 5)
    assert len(rows) == 1
    assert rows[0][3] == "hello" and rows[0][4] == "hi"
    assert "read_recent_messages" in rows[0][5]


async def test_llm_log_records_a_failure_with_no_reply(db):
    """A timeout has to leave a trace - that is the whole point of the table."""
    stores = Stores(db)
    await _log(stores, reply=None, status="timeout", error="no response within 600s")
    rows = await stores.llm_log.recent_for_guild(GUILD, 5)
    assert rows[0][8] == "timeout"
    assert rows[0][9] == "no response within 600s"
    assert rows[0][4] is None


async def test_memory_read_excludes_failed_exchanges(db):
    """A failed row must never be replayed to the model as something it said."""
    stores = Stores(db)
    await _log(stores, reply=None, status="timeout", error="boom")
    await _log(stores, prompt="good", reply="answer")
    rows = await stores.llm_log.recent_for_channel(555, 10, "2000-01-01T00:00:00")
    assert len(rows) == 1
    assert rows[0][1] == "good"


async def test_memory_read_is_scoped_to_one_channel(db):
    stores = Stores(db)
    await _log(stores, channel_id=555, prompt="here")
    await _log(stores, channel_id=666, prompt="elsewhere")
    rows = await stores.llm_log.recent_for_channel(555, 10, "2000-01-01T00:00:00")
    assert [r[1] for r in rows] == ["here"]


async def test_memory_read_honours_the_staleness_cutoff(db):
    stores = Stores(db)
    await _log(stores)
    assert await stores.llm_log.recent_for_channel(555, 10, "2999-01-01T00:00:00") == []


async def test_llm_log_prune_removes_only_old_rows(db):
    stores = Stores(db)
    await _log(stores)
    assert await stores.llm_log.prune("2000-01-01T00:00:00") == 0
    assert await stores.llm_log.prune("2999-01-01T00:00:00") == 1
    assert await stores.llm_log.recent_for_guild(GUILD, 5) == []
