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
import json
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

    async def delete(self, guild_id: int, key: str) -> None:
        """Removes a key entirely, so a later read falls back to its
        hardcoded default - used by /setup's per-step "Reset to defaults",
        for settings whose real default is "unset" rather than a literal
        stored value."""
        if not self.db.available:
            raise RuntimeError("Database unavailable, config not reset")
        await self.db.conn.execute(
            "DELETE FROM config WHERE guild_id = ? AND key = ?", (guild_id, key)
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

    async def delete(self, guild_id: int, case_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, case not deleted")
        await self.db.conn.execute(
            "DELETE FROM cases WHERE guild_id = ? AND id = ?", (guild_id, case_id)
        )
        await self.db.conn.commit()

    async def update_reason(self, guild_id: int, case_id: int, reason: str) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, case not updated")
        await self.db.conn.execute(
            "UPDATE cases SET reason = ? WHERE guild_id = ? AND id = ?", (reason, guild_id, case_id)
        )
        await self.db.conn.commit()


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


class ScheduledTaskStore:
    """Generic "run this at time T" queue - tempban auto-unbans, giveaway
    endings, poll auto-closes, and /remind all share this one table/loop
    instead of each needing their own tasks.loop watchdog."""

    def __init__(self, db: Database):
        self.db = db

    async def add(self, guild_id: int, kind: str, payload: dict, run_at: datetime.datetime) -> Optional[int]:
        if not self.db.available:
            raise RuntimeError("Database unavailable, task not scheduled")
        cursor = await self.db.conn.execute(
            "INSERT INTO scheduled_tasks (guild_id, kind, payload, run_at) VALUES (?, ?, ?, ?)",
            (guild_id, kind, json.dumps(payload), run_at.isoformat()),
        )
        await self.db.conn.commit()
        return cursor.lastrowid

    async def due(self, now_iso: str) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute(
            "SELECT id, guild_id, kind, payload FROM scheduled_tasks WHERE done = 0 AND run_at <= ?",
            (now_iso,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [(row[0], row[1], row[2], json.loads(row[3])) for row in rows]

    async def mark_done(self, task_id: int) -> None:
        if not self.db.available:
            return
        await self.db.conn.execute("UPDATE scheduled_tasks SET done = 1 WHERE id = ?", (task_id,))
        await self.db.conn.commit()


class ChannelLockStore:
    """Remembers a channel's @everyone send_messages overwrite from just
    before /lockdown flipped it off, so /lockdown again can restore it -
    stored as text since PermissionOverwrite values are tri-state
    (True/False/None-inherit), not a plain bool."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def encode(value: Optional[bool]) -> str:
        return "none" if value is None else ("true" if value else "false")

    @staticmethod
    def decode(value: str) -> Optional[bool]:
        return None if value == "none" else value == "true"

    async def set(self, guild_id: int, channel_id: int, previous: Optional[bool]) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, lock not recorded")
        await self.db.conn.execute(
            "INSERT INTO channel_locks (guild_id, channel_id, previous_send_messages) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, channel_id) DO UPDATE SET "
            "previous_send_messages = excluded.previous_send_messages",
            (guild_id, channel_id, self.encode(previous)),
        )
        await self.db.conn.commit()

    async def get(self, guild_id: int, channel_id: int) -> Optional[str]:
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT previous_send_messages FROM channel_locks WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def clear(self, guild_id: int, channel_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, lock not cleared")
        await self.db.conn.execute(
            "DELETE FROM channel_locks WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id)
        )
        await self.db.conn.commit()


class StarboardStore:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, guild_id: int, message_id: int) -> Optional[int]:
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT starboard_message_id FROM starboard_posts WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set(self, guild_id: int, message_id: int, starboard_message_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, starboard post not recorded")
        await self.db.conn.execute(
            "INSERT INTO starboard_posts (guild_id, message_id, starboard_message_id) VALUES (?, ?, ?)",
            (guild_id, message_id, starboard_message_id),
        )
        await self.db.conn.commit()


class GiveawayStore:
    def __init__(self, db: Database):
        self.db = db

    async def create(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        prize: str,
        winner_count: int,
        host_id: int,
        end_at: datetime.datetime,
    ) -> Optional[int]:
        if not self.db.available:
            raise RuntimeError("Database unavailable, giveaway not created")
        cursor = await self.db.conn.execute(
            "INSERT INTO giveaways (guild_id, channel_id, message_id, prize, winner_count, host_id, end_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, message_id, prize, winner_count, host_id, end_at.isoformat()),
        )
        await self.db.conn.commit()
        return cursor.lastrowid

    async def get(self, giveaway_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT id, guild_id, channel_id, message_id, prize, winner_count, host_id, end_at, ended "
            "FROM giveaways WHERE id = ?",
            (giveaway_id,),
        ) as cursor:
            return await cursor.fetchone()

    async def get_by_message(self, guild_id: int, message_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT id, guild_id, channel_id, message_id, prize, winner_count, host_id, end_at, ended "
            "FROM giveaways WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        ) as cursor:
            return await cursor.fetchone()

    async def toggle_entry(self, giveaway_id: int, user_id: int) -> bool:
        """Enters if not already in, leaves if already in. Returns True if
        the user is now entered, False if they just left."""
        if not self.db.available:
            raise RuntimeError("Database unavailable, entry not recorded")
        async with self.db.conn.execute(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (giveaway_id, user_id)
        ) as cursor:
            already_in = await cursor.fetchone() is not None
        if already_in:
            await self.db.conn.execute(
                "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
                (giveaway_id, user_id),
            )
            await self.db.conn.commit()
            return False
        await self.db.conn.execute(
            "INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)", (giveaway_id, user_id)
        )
        await self.db.conn.commit()
        return True

    async def entries(self, giveaway_id: int) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def mark_ended(self, giveaway_id: int) -> None:
        if not self.db.available:
            return
        await self.db.conn.execute("UPDATE giveaways SET ended = 1 WHERE id = ?", (giveaway_id,))
        await self.db.conn.commit()


class PollStore:
    def __init__(self, db: Database):
        self.db = db

    async def create(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        question: str,
        options: list,
        end_at: Optional[datetime.datetime],
    ) -> Optional[int]:
        if not self.db.available:
            raise RuntimeError("Database unavailable, poll not created")
        cursor = await self.db.conn.execute(
            "INSERT INTO polls (guild_id, channel_id, message_id, question, options, end_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                channel_id,
                message_id,
                question,
                json.dumps(options),
                end_at.isoformat() if end_at else None,
            ),
        )
        await self.db.conn.commit()
        return cursor.lastrowid

    async def get(self, poll_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT id, guild_id, channel_id, message_id, question, options, end_at, closed "
            "FROM polls WHERE id = ?",
            (poll_id,),
        ) as cursor:
            return await cursor.fetchone()

    async def set_message_id(self, poll_id: int, message_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, poll not updated")
        await self.db.conn.execute(
            "UPDATE polls SET message_id = ? WHERE id = ?", (message_id, poll_id)
        )
        await self.db.conn.commit()

    async def get_by_message(self, guild_id: int, message_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT id, guild_id, channel_id, message_id, question, options, end_at, closed "
            "FROM polls WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        ) as cursor:
            return await cursor.fetchone()

    async def open_polls(self) -> list:
        if not self.db.available:
            return []
        async with self.db.conn.execute(
            "SELECT id, guild_id, channel_id, message_id, question, options, end_at, closed "
            "FROM polls WHERE closed = 0"
        ) as cursor:
            return await cursor.fetchall()

    async def set_vote(self, poll_id: int, user_id: int, option_index: int) -> str:
        """Casts a vote, switches an existing vote, or (clicking the same
        option again) retracts it. Returns "voted" or "retracted"."""
        if not self.db.available:
            raise RuntimeError("Database unavailable, vote not recorded")
        async with self.db.conn.execute(
            "SELECT option_index FROM poll_votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None and row[0] == option_index:
            await self.db.conn.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id)
            )
            await self.db.conn.commit()
            return "retracted"
        await self.db.conn.execute(
            "INSERT INTO poll_votes (poll_id, user_id, option_index) VALUES (?, ?, ?) "
            "ON CONFLICT(poll_id, user_id) DO UPDATE SET option_index = excluded.option_index",
            (poll_id, user_id, option_index),
        )
        await self.db.conn.commit()
        return "voted"

    async def vote_counts(self, poll_id: int, option_count: int) -> list:
        counts = [0] * option_count
        if not self.db.available:
            return counts
        async with self.db.conn.execute(
            "SELECT option_index, COUNT(*) FROM poll_votes WHERE poll_id = ? GROUP BY option_index",
            (poll_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        for index, count in rows:
            if 0 <= index < option_count:
                counts[index] = count
        return counts

    async def close(self, poll_id: int) -> None:
        if not self.db.available:
            return
        await self.db.conn.execute("UPDATE polls SET closed = 1 WHERE id = ?", (poll_id,))
        await self.db.conn.commit()


class TicketStore:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, guild_id: int, channel_id: int, opener_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, ticket not recorded")
        await self.db.conn.execute(
            "INSERT INTO tickets (guild_id, channel_id, opener_id, opened_at) VALUES (?, ?, ?, ?)",
            (guild_id, channel_id, opener_id, datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )
        await self.db.conn.commit()

    async def get_by_channel(self, guild_id: int, channel_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT guild_id, channel_id, opener_id, opened_at, status FROM tickets "
            "WHERE guild_id = ? AND channel_id = ? AND status = 'open'",
            (guild_id, channel_id),
        ) as cursor:
            return await cursor.fetchone()

    async def get_open_for_user(self, guild_id: int, opener_id: int):
        if not self.db.available:
            return None
        async with self.db.conn.execute(
            "SELECT guild_id, channel_id, opener_id, opened_at, status FROM tickets "
            "WHERE guild_id = ? AND opener_id = ? AND status = 'open'",
            (guild_id, opener_id),
        ) as cursor:
            return await cursor.fetchone()

    async def close(self, guild_id: int, channel_id: int) -> None:
        if not self.db.available:
            raise RuntimeError("Database unavailable, ticket not closed")
        await self.db.conn.execute(
            "UPDATE tickets SET status = 'closed' WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        await self.db.conn.commit()


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
        self.scheduled = ScheduledTaskStore(db)
        self.channel_locks = ChannelLockStore(db)
        self.starboard = StarboardStore(db)
        self.giveaways = GiveawayStore(db)
        self.polls = PollStore(db)
        self.tickets = TicketStore(db)
