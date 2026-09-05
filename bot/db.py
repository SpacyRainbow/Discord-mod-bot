"""
Database connection layer.

Design goal (borrowed from Sweetie Bot): the bot should never crash just
because the DB connection dropped. Every read falls back to a safe default
and logs a warning instead of raising. Writes DO raise, since a failed write
(e.g. a mod action not being recorded) is something the caller needs to know
about and surface to the user, not silently swallow.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger("bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    guild_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS banned_words (
    guild_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    PRIMARY KEY (guild_id, word)
);

CREATE TABLE IF NOT EXISTS reaction_roles (
    guild_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, message_id, emoji)
);

CREATE TABLE IF NOT EXISTS self_assignable_roles (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    author TEXT,
    content TEXT NOT NULL,
    added_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_by INTEGER,
    PRIMARY KEY (guild_id, name)
);

CREATE TABLE IF NOT EXISTS buckets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    bucket_name TEXT NOT NULL,
    item TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS witty_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    response TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mute_expirations (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    run_at TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS channel_locks (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    previous_send_messages TEXT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS starboard_posts (
    guild_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    starboard_message_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, message_id)
);

CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    prize TEXT NOT NULL,
    winner_count INTEGER NOT NULL,
    host_id INTEGER NOT NULL,
    end_at TEXT NOT NULL,
    ended INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    question TEXT NOT NULL,
    options TEXT NOT NULL,
    end_at TEXT,
    closed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    option_index INTEGER NOT NULL,
    PRIMARY KEY (poll_id, user_id)
);

CREATE TABLE IF NOT EXISTS llm_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    channel_name TEXT,
    user_id INTEGER NOT NULL,
    user_name TEXT,
    prompt TEXT,
    reply TEXT,
    tool_calls TEXT,
    rounds INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_log_channel_time
    ON llm_log (channel_id, created_at);

-- Rolling per-channel topic summary. One row per channel; `covers_to` is the
-- created_at of the newest exchange already summarised, so the refresh loop can
-- tell how much is new without re-reading everything.
CREATE TABLE IF NOT EXISTS channel_digest (
    channel_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    digest TEXT NOT NULL,
    covers_to TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    opener_id INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    PRIMARY KEY (guild_id, channel_id)
);
"""


class Database:
    """Owns the single aiosqlite connection and tracks availability."""

    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None
        self.available = False

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        self.available = True
        logger.info("Connected to database at %s", self.path)

    async def close(self) -> None:
        # A connection that is already broken often raises on close() too; that
        # must not stop us nulling the handle, or ping() can never take its
        # reconnect branch again. (review F2)
        if self.conn is not None:
            with contextlib.suppress(Exception):
                await self.conn.close()
        self.conn = None
        self.available = False

    async def ping(self) -> bool:
        """Used by the watchdog task in core.py to test/recover a dead connection."""
        try:
            if self.conn is None:
                await self.connect()
                return True
            # `async with` so the cursor is closed - a bare execute() leaked one
            # cursor per ping. (review F2)
            async with self.conn.execute("SELECT 1"):
                pass
            self.available = True
            return True
        except Exception:
            logger.warning("Database ping failed - dropping the connection", exc_info=True)
            # Drop the dead handle so the next watchdog tick reconnects rather
            # than retrying the same broken connection forever. (review F2)
            await self.close()
            return False
