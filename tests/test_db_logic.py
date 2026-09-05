"""Database connection-recovery tests (review F2)."""

import pytest

from bot.db import Database


class _BrokenConnection:
    """Stands in for an aiosqlite connection whose socket/file died under us."""

    def __init__(self, raise_on_close=False):
        self.raise_on_close = raise_on_close
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("connection is dead")

    async def close(self):
        self.closed = True
        if self.raise_on_close:
            raise RuntimeError("close failed too")


@pytest.mark.asyncio
async def test_ping_drops_a_dead_connection_so_the_next_tick_reconnects():
    database = Database("/nonexistent/never-used.db")
    database.conn = _BrokenConnection()
    database.available = True

    assert await database.ping() is False
    # conn must be cleared, or ping() retries the same dead handle forever.
    assert database.conn is None
    assert database.available is False


@pytest.mark.asyncio
async def test_ping_clears_conn_even_when_close_itself_raises():
    database = Database("/nonexistent/never-used.db")
    database.conn = _BrokenConnection(raise_on_close=True)
    database.available = True

    assert await database.ping() is False
    assert database.conn is None
    assert database.available is False


@pytest.mark.asyncio
async def test_ping_reconnects_after_the_connection_was_dropped(tmp_path):
    database = Database(str(tmp_path / "recover.db"))
    await database.connect()
    assert database.available is True

    # Simulate the failure/recovery cycle the watchdog drives. The real
    # connection is closed first so aiosqlite's worker thread is reaped -
    # orphaning it would hang the interpreter at exit.
    await database.conn.close()
    database.conn = _BrokenConnection()
    database.available = True
    assert await database.ping() is False
    assert database.conn is None

    assert await database.ping() is True
    assert database.conn is not None
    assert database.available is True
    await database.close()


@pytest.mark.asyncio
async def test_ping_succeeds_on_a_healthy_connection(db):
    assert await db.ping() is True
    assert db.available is True


@pytest.mark.asyncio
async def test_close_is_idempotent_and_nulls_the_handle(tmp_path):
    database = Database(str(tmp_path / "close.db"))
    await database.connect()
    await database.close()
    assert database.conn is None
    assert database.available is False
    await database.close()  # must not raise
    assert database.conn is None


# --- additive migrations ------------------------------------------------------
#
# `CREATE TABLE IF NOT EXISTS` does NOTHING to a table that already exists, so a
# column added to SCHEMA after a database shipped is simply absent on every
# deployed copy. This is the mechanism that closes that gap, and it has to be
# safe to run on every single connect.


@pytest.mark.asyncio
async def test_a_column_added_after_the_fact_is_applied_to_an_existing_table(tmp_path):
    import aiosqlite

    from bot.db import ADDED_COLUMNS, Database

    path = str(tmp_path / "old.db")
    # A database from before the columns existed, with a row already in it.
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE llm_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        await conn.execute("INSERT INTO llm_log (guild_id, channel_id, created_at) "
                           "VALUES (1, 2, 'then')")
        await conn.commit()

    database = Database(path)
    await database.connect()
    try:
        cursor = await database.conn.execute("PRAGMA table_info(llm_log)")
        columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        for table, column, _decl in ADDED_COLUMNS:
            if table == "llm_log":
                assert column in columns
        # Additive means additive: the existing row is still there.
        cursor = await database.conn.execute("SELECT COUNT(*) FROM llm_log")
        assert (await cursor.fetchone())[0] == 1
        await cursor.close()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_running_the_migration_twice_is_harmless(tmp_path):
    """It runs on every connect, so "already applied" is the normal case."""
    from bot.db import Database

    path = str(tmp_path / "twice.db")
    for _ in range(2):
        database = Database(path)
        await database.connect()
        await database._add_missing_columns()
        await database.close()

    database = Database(path)
    await database.connect()
    try:
        cursor = await database.conn.execute("PRAGMA table_info(llm_log)")
        names = [row[1] for row in await cursor.fetchall()]
        await cursor.close()
        assert names.count("prompt_tokens") == 1
    finally:
        await database.close()
