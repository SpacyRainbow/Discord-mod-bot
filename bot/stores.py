"""
Store classes: thin wrappers around SQL for each table.

Every read method checks db.available first and returns a safe default
(None, [], {}) if the database is down, logging a warning rather than
raising. Every write method raises if the database is unavailable, since
callers (usually a Discord command) need to know the write didn't happen
so they can tell the user, rather than pretending it succeeded.
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from .db import Database

logger = logging.getLogger("bot.stores")


class ConfigStore:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, guild_id: int, key: str, default: Optional[str] = None) -> Optional[str]:
        if not self.db.available:
            return default
        try:
            async with self.db.conn.execute(
                "SELECT value FROM config WHERE guild_id = ? AND key = ?", (guild_id, key)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default
        except Exception:
            logger.warning("Config read failed for key=%s", key, exc_info=True)
            self.db.available = False
            return default

    async def get_bool(self, guild_id: int, key: str, default: bool = False) -> bool:
        val = await self.get(guild_id, key, None)
        if val is None:
            return default
        return val.lower() in ("1", "true", "yes", "on")

    async def get_int(self, guild_id: int, key: str, default: int = 0) -> int:
        val = await self.get(guild_id, key, None)
        try:
            return int(val) if val is not None else default
        except ValueError:
            return default

    async def set(self, guild_id: int, key: str, value: str) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, config not saved")
        await self.db.conn.execute(
            "INSERT INTO config (guild_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
            (guild_id, key, value),
        )
        await self.db.conn.commit()

    async def get_all(self, guild_id: int) -> dict:
        if not self.db.available:
            return {}
        async with self.db.conn.execute(
            "SELECT key, value FROM config WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return {k: v for k, v in rows}


class CaseStore:
    """Moderation case history: kicks, bans, mutes, warns."""

    def __init__(self, db: Database):
        self.db = db

    async def add(
        self, guild_id: int, user_id: int, moderator_id: int, action: str, reason: str
    ) -> Optional[int]:
        if not self.db.available:
            raise RuntimeError("Database unavailable, case not recorded")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor = await self.db.conn.execute(
            "INSERT INTO cases (guild_id, user_id, moderator_id, action, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, action, reason, now),
        )
        await self.db.conn.commit()
        return cursor.lastrowid

    async def for_user(self, guild_id: int, user_id: int) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute(
            "SELECT id, action, reason, moderator_id, created_at FROM cases "
            "WHERE guild_id = ? AND user_id = ? ORDER BY id DESC",
            (guild_id, user_id),
        ) as cursor:
            return await cursor.fetchall()

    async def get(self, guild_id: int, case_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT id, user_id, action, reason, moderator_id, created_at FROM cases "
            "WHERE guild_id = ? AND id = ?",
            (guild_id, case_id),
        ) as cursor:
            return await cursor.fetchone()


class FilterStore:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, guild_id: int, word: str) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, filter not saved")
        await self.db.conn.execute(
            "INSERT OR IGNORE INTO banned_words (guild_id, word) VALUES (?, ?)",
            (guild_id, word.lower()),
        )
        await self.db.conn.commit()

    async def remove(self, guild_id: int, word: str) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, filter not removed")
        await self.db.conn.execute(
            "DELETE FROM banned_words WHERE guild_id = ? AND word = ?", (guild_id, word.lower())
        )
        await self.db.conn.commit()

    async def all(self, guild_id: int) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute(
            "SELECT word FROM banned_words WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


class RoleStore:
    def __init__(self, db: Database):
        self.db = db

    async def add_self_assignable(self, guild_id: int, role_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable")
        await self.db.conn.execute(
            "INSERT OR IGNORE INTO self_assignable_roles (guild_id, role_id) VALUES (?, ?)",
            (guild_id, role_id),
        )
        await self.db.conn.commit()

    async def remove_self_assignable(self, guild_id: int, role_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable")
        await self.db.conn.execute(
            "DELETE FROM self_assignable_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
        )
        await self.db.conn.commit()

    async def list_self_assignable(self, guild_id: int) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute(
            "SELECT role_id FROM self_assignable_roles WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def add_reaction_role(self, guild_id: int, message_id: int, emoji: str, role_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable")
        await self.db.conn.execute(
            "INSERT OR REPLACE INTO reaction_roles "
            "(guild_id, message_id, emoji, role_id) VALUES (?, ?, ?, ?)",
            (guild_id, message_id, emoji, role_id),
        )
        await self.db.conn.commit()

    async def get_reaction_role(self, guild_id: int, message_id: int, emoji: str):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT role_id FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
            (guild_id, message_id, emoji),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


class QuoteStore:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, guild_id: int, author: str, content: str, added_by: int) -> Optional[int]:
        if not self.db.available:
            raise RuntimeError("Database unavailable, quote not saved")
        cursor = await self.db.conn.execute(
            "INSERT INTO quotes (guild_id, author, content, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, author, content, added_by, datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )
        await self.db.conn.commit()
        return cursor.lastrowid

    async def random(self, guild_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT id, author, content FROM quotes WHERE guild_id = ? ORDER BY RANDOM() LIMIT 1", (guild_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def get(self, guild_id: int, quote_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT id, author, content FROM quotes WHERE guild_id = ? AND id = ?", (guild_id, quote_id)
        ) as cursor:
            return await cursor.fetchone()


class TagStore:
    def __init__(self, db: Database):
        self.db = db

    async def set(self, guild_id: int, name: str, content: str, created_by: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, tag not saved")
        await self.db.conn.execute(
            "INSERT INTO tags (guild_id, name, content, created_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, name) DO UPDATE SET content = excluded.content",
            (guild_id, name.lower(), content, created_by),
        )
        await self.db.conn.commit()

    async def get(self, guild_id: int, name: str) -> Optional[str]:
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT content FROM tags WHERE guild_id = ? AND name = ?", (guild_id, name.lower())
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def delete(self, guild_id: int, name: str) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable")
        await self.db.conn.execute(
            "DELETE FROM tags WHERE guild_id = ? AND name = ?", (guild_id, name.lower())
        )
        await self.db.conn.commit()

    async def list_names(self, guild_id: int) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute("SELECT name FROM tags WHERE guild_id = ?", (guild_id,)) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


class BucketStore:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, guild_id: int, bucket_name: str, item: str) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, item not saved")
        await self.db.conn.execute(
            "INSERT INTO buckets (guild_id, bucket_name, item) VALUES (?, ?, ?)",
            (guild_id, bucket_name.lower(), item),
        )
        await self.db.conn.commit()

    async def random(self, guild_id: int, bucket_name: str):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT item FROM buckets WHERE guild_id = ? AND bucket_name = ? ORDER BY RANDOM() LIMIT 1",
            (guild_id, bucket_name.lower()),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def list_buckets(self, guild_id: int) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute(
            "SELECT DISTINCT bucket_name FROM buckets WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


class WittyStore:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, guild_id: int, response: str) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, response not saved")
        await self.db.conn.execute(
            "INSERT INTO witty_responses (guild_id, response) VALUES (?, ?)", (guild_id, response)
        )
        await self.db.conn.commit()

    async def random(self, guild_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT response FROM witty_responses WHERE guild_id = ? ORDER BY RANDOM() LIMIT 1", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


class MuteStore:
    """Scheduled auto-lift times for role-based mutes longer than Discord's
    28-day native timeout cap. Indefinite mutes never get a row here -
    they're lifted only by a manual /unmute."""

    def __init__(self, db: Database):
        self.db = db

    async def schedule(self, guild_id: int, user_id: int, expires_at: str) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, mute expiration not scheduled")
        await self.db.conn.execute(
            "INSERT INTO mute_expirations (guild_id, user_id, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET expires_at = excluded.expires_at",
            (guild_id, user_id, expires_at),
        )
        await self.db.conn.commit()

    async def clear(self, guild_id: int, user_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, mute expiration not cleared")
        await self.db.conn.execute(
            "DELETE FROM mute_expirations WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        await self.db.conn.commit()

    async def due(self, now_iso: str) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute(
            "SELECT guild_id, user_id FROM mute_expirations WHERE expires_at <= ?", (now_iso,)
        ) as cursor:
            return await cursor.fetchall()


class Stores:
    """Bag of all store instances, attached to the bot as bot.stores."""

    def __init__(self, db: Database):
        self.db = db
        self.config = ConfigStore(db)
        self.cases = CaseStore(db)
        self.filters = FilterStore(db)
        self.roles = RoleStore(db)
        self.quotes = QuoteStore(db)
        self.tags = TagStore(db)
        self.buckets = BucketStore(db)
        self.witty = WittyStore(db)
        self.mutes = MuteStore(db)
