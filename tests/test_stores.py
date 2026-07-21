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
