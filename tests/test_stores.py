import pytest

from bot.stores import Stores

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
