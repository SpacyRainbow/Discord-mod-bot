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
