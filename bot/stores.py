"""
Store classes: thin wrappers around SQL for each table.

Every read method checks db.available first and returns a safe default
(None, [], {}) if the database is down, logging a warning rather than
raising. Every write method raises if the database is unavailable, since
callers (usually a Discord command) need to know the write didn't happen
so they can tell the user, rather than pretending it succeeded.

That contract lives in exactly one place: the `_Store` base class below.
No store method may call `self.db.conn.execute` directly - every query goes
through `_read_one`, `_read_all` or `_write`, so a failure always both logs
and flips `db.available = False`, which is what tells the watchdog in
core.py to reconnect. (review F3)
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Optional

from .db import Database

logger = logging.getLogger("bot.stores")


class _Store:
    """Base for every store. All DB access goes through these three helpers so
    the read/write failure contract is implemented in exactly one place."""

    def __init__(self, db: Database):
        self.db = db

    async def _read_one(self, sql: str, params: tuple = (), default=None):
        """Reads a single row. On any failure: logs, marks the DB unavailable,
        returns `default`. Never raises."""
        if not self.db.available:
            return default
        try:
            async with self.db.conn.execute(sql, params) as cursor:
                return await cursor.fetchone()
        except Exception:
            logger.warning("Read failed: %s", sql, exc_info=True)
            self.db.available = False
            return default

    async def _read_all(self, sql: str, params: tuple = (), default=None) -> list:
        """Reads all rows. Same failure contract; `default` defaults to []."""
        if default is None:
            default = []
        if not self.db.available:
            return default
        try:
            async with self.db.conn.execute(sql, params) as cursor:
                return await cursor.fetchall()
        except Exception:
            logger.warning("Read failed: %s", sql, exc_info=True)
            self.db.available = False
            return default

    async def _write(self, sql: str, params: tuple, message: str):
        """Executes and commits. On failure: logs, marks the DB unavailable, and
        raises RuntimeError(message). Returns the cursor on success."""
        if not self.db.available:
            raise RuntimeError(message)
        try:
            cursor = await self.db.conn.execute(sql, params)
            await self.db.conn.commit()
            return cursor
        except Exception:
            logger.warning("Write failed: %s", sql, exc_info=True)
            self.db.available = False
            raise RuntimeError(message) from None


class ConfigStore(_Store):
    async def get(self, guild_id: int, key: str, default: Optional[str] = None) -> Optional[str]:
        row = await self._read_one(
            "SELECT value FROM config WHERE guild_id = ? AND key = ?", (guild_id, key)
        )
        return row[0] if row else default

    async def get_bool(self, guild_id: int, key: str, default: bool = False) -> bool:
        val = await self.get(guild_id, key, None)
        if val is None:
            return default
        return val.lower() in ("1", "true", "yes", "on")

    async def get_int(
        self,
        guild_id: int,
        key: str,
        default: int = 0,
        *,
        minimum: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> int:
        """Reads an integer config value. A value that doesn't parse, or that
        falls outside [minimum, maximum] when bounds are given, is rejected in
        favour of `default` - negative/absurd values were previously stored
        happily and silently disabled detection or broke Discord API calls.
        (review F14) Callers pass the bounds; see each module's config docs."""
        val = await self.get(guild_id, key, None)
        if val is None:
            return default
        try:
            parsed = int(val)
        except (TypeError, ValueError):
            logger.warning("Config %s in guild %s is not a whole number (%r)", key, guild_id, val)
            return default
        if (minimum is not None and parsed < minimum) or (maximum is not None and parsed > maximum):
            logger.warning(
                "Config %s in guild %s is out of range (%s not in [%s, %s]) - using default %s",
                key,
                guild_id,
                parsed,
                minimum,
                maximum,
                default,
            )
            return default
        return parsed

    async def set(self, guild_id: int, key: str, value: str) -> None:
        await self._write(
            "INSERT INTO config (guild_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
            (guild_id, key, value),
            "Database unavailable, config not saved",
        )

    async def delete(self, guild_id: int, key: str) -> None:
        """Removes a key entirely, so a later read falls back to its
        hardcoded default - used by /setup's per-step "Reset to defaults",
        for settings whose real default is "unset" rather than a literal
        stored value."""
        await self._write(
            "DELETE FROM config WHERE guild_id = ? AND key = ?",
            (guild_id, key),
            "Database unavailable, config not reset",
        )

    async def get_all(self, guild_id: int) -> dict:
        rows = await self._read_all(
            "SELECT key, value FROM config WHERE guild_id = ?", (guild_id,)
        )
        return {k: v for k, v in rows}


class CaseStore(_Store):
    """Moderation case history: kicks, bans, mutes, warns."""

    async def add(
        self, guild_id: int, user_id: int, moderator_id: int, action: str, reason: str
    ) -> Optional[int]:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor = await self._write(
            "INSERT INTO cases (guild_id, user_id, moderator_id, action, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, action, reason, now),
            "Database unavailable, case not recorded",
        )
        return cursor.lastrowid

    async def for_user(self, guild_id: int, user_id: int) -> list:
        return await self._read_all(
            "SELECT id, action, reason, moderator_id, created_at FROM cases "
            "WHERE guild_id = ? AND user_id = ? ORDER BY id DESC",
            (guild_id, user_id),
        )

    async def get(self, guild_id: int, case_id: int):
        return await self._read_one(
            "SELECT id, user_id, action, reason, moderator_id, created_at FROM cases "
            "WHERE guild_id = ? AND id = ?",
            (guild_id, case_id),
        )

    async def delete(self, guild_id: int, case_id: int) -> None:
        await self._write(
            "DELETE FROM cases WHERE guild_id = ? AND id = ?",
            (guild_id, case_id),
            "Database unavailable, case not deleted",
        )

    async def update_reason(self, guild_id: int, case_id: int, reason: str) -> None:
        await self._write(
            "UPDATE cases SET reason = ? WHERE guild_id = ? AND id = ?",
            (reason, guild_id, case_id),
            "Database unavailable, case not updated",
        )


class FilterStore(_Store):
    async def add(self, guild_id: int, word: str) -> None:
        await self._write(
            "INSERT OR IGNORE INTO banned_words (guild_id, word) VALUES (?, ?)",
            (guild_id, word.lower()),
            "Database unavailable, filter not saved",
        )

    async def remove(self, guild_id: int, word: str) -> None:
        await self._write(
            "DELETE FROM banned_words WHERE guild_id = ? AND word = ?",
            (guild_id, word.lower()),
            "Database unavailable, filter not removed",
        )

    async def all(self, guild_id: int) -> list:
        rows = await self._read_all(
            "SELECT word FROM banned_words WHERE guild_id = ?", (guild_id,)
        )
        return [r[0] for r in rows]


class RoleStore(_Store):
    async def add_self_assignable(self, guild_id: int, role_id: int) -> None:
        await self._write(
            "INSERT OR IGNORE INTO self_assignable_roles (guild_id, role_id) VALUES (?, ?)",
            (guild_id, role_id),
            "Database unavailable",
        )

    async def remove_self_assignable(self, guild_id: int, role_id: int) -> None:
        await self._write(
            "DELETE FROM self_assignable_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
            "Database unavailable",
        )

    async def list_self_assignable(self, guild_id: int) -> list:
        rows = await self._read_all(
            "SELECT role_id FROM self_assignable_roles WHERE guild_id = ?", (guild_id,)
        )
        return [r[0] for r in rows]

    async def add_reaction_role(self, guild_id: int, message_id: int, emoji: str, role_id: int) -> None:
        await self._write(
            "INSERT OR REPLACE INTO reaction_roles "
            "(guild_id, message_id, emoji, role_id) VALUES (?, ?, ?, ?)",
            (guild_id, message_id, emoji, role_id),
            "Database unavailable",
        )

    async def get_reaction_role(self, guild_id: int, message_id: int, emoji: str):
        row = await self._read_one(
            "SELECT role_id FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
            (guild_id, message_id, emoji),
        )
        return row[0] if row else None


class QuoteStore(_Store):
    async def add(self, guild_id: int, author: str, content: str, added_by: int) -> Optional[int]:
        cursor = await self._write(
            "INSERT INTO quotes (guild_id, author, content, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, author, content, added_by, datetime.datetime.now(datetime.timezone.utc).isoformat()),
            "Database unavailable, quote not saved",
        )
        return cursor.lastrowid

    async def random(self, guild_id: int):
        return await self._read_one(
            "SELECT id, author, content FROM quotes WHERE guild_id = ? ORDER BY RANDOM() LIMIT 1",
            (guild_id,),
        )

    async def get(self, guild_id: int, quote_id: int):
        return await self._read_one(
            "SELECT id, author, content FROM quotes WHERE guild_id = ? AND id = ?",
            (guild_id, quote_id),
        )


class TagStore(_Store):
    async def set(self, guild_id: int, name: str, content: str, created_by: int) -> None:
        await self._write(
            "INSERT INTO tags (guild_id, name, content, created_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, name) DO UPDATE SET content = excluded.content",
            (guild_id, name.lower(), content, created_by),
            "Database unavailable, tag not saved",
        )

    async def get(self, guild_id: int, name: str) -> Optional[str]:
        row = await self._read_one(
            "SELECT content FROM tags WHERE guild_id = ? AND name = ?", (guild_id, name.lower())
        )
        return row[0] if row else None

    async def delete(self, guild_id: int, name: str) -> None:
        await self._write(
            "DELETE FROM tags WHERE guild_id = ? AND name = ?",
            (guild_id, name.lower()),
            "Database unavailable",
        )

    async def list_names(self, guild_id: int) -> list:
        rows = await self._read_all("SELECT name FROM tags WHERE guild_id = ?", (guild_id,))
        return [r[0] for r in rows]


class BucketStore(_Store):
    async def add(self, guild_id: int, bucket_name: str, item: str) -> None:
        await self._write(
            "INSERT INTO buckets (guild_id, bucket_name, item) VALUES (?, ?, ?)",
            (guild_id, bucket_name.lower(), item),
            "Database unavailable, item not saved",
        )

    async def random(self, guild_id: int, bucket_name: str):
        row = await self._read_one(
            "SELECT item FROM buckets WHERE guild_id = ? AND bucket_name = ? ORDER BY RANDOM() LIMIT 1",
            (guild_id, bucket_name.lower()),
        )
        return row[0] if row else None

    async def list_buckets(self, guild_id: int) -> list:
        rows = await self._read_all(
            "SELECT DISTINCT bucket_name FROM buckets WHERE guild_id = ?", (guild_id,)
        )
        return [r[0] for r in rows]


class WittyStore(_Store):
    async def add(self, guild_id: int, response: str) -> None:
        await self._write(
            "INSERT INTO witty_responses (guild_id, response) VALUES (?, ?)",
            (guild_id, response),
            "Database unavailable, response not saved",
        )

    async def random(self, guild_id: int):
        row = await self._read_one(
            "SELECT response FROM witty_responses WHERE guild_id = ? ORDER BY RANDOM() LIMIT 1",
            (guild_id,),
        )
        return row[0] if row else None


class MuteStore(_Store):
    """Scheduled auto-lift times for role-based mutes longer than Discord's
    28-day native timeout cap. Indefinite mutes never get a row here -
    they're lifted only by a manual /unmute."""

    async def schedule(self, guild_id: int, user_id: int, expires_at: str) -> None:
        await self._write(
            "INSERT INTO mute_expirations (guild_id, user_id, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET expires_at = excluded.expires_at",
            (guild_id, user_id, expires_at),
            "Database unavailable, mute expiration not scheduled",
        )

    async def clear(self, guild_id: int, user_id: int) -> None:
        await self._write(
            "DELETE FROM mute_expirations WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
            "Database unavailable, mute expiration not cleared",
        )

    async def due(self, now_iso: str) -> list:
        return await self._read_all(
            "SELECT guild_id, user_id FROM mute_expirations WHERE expires_at <= ?", (now_iso,)
        )


class ScheduledTaskStore(_Store):
    """Generic "run this at time T" queue - tempban auto-unbans, giveaway
    endings, poll auto-closes, and /remind all share this one table/loop
    instead of each needing their own tasks.loop watchdog."""

    async def add(self, guild_id: int, kind: str, payload: dict, run_at: datetime.datetime) -> Optional[int]:
        cursor = await self._write(
            "INSERT INTO scheduled_tasks (guild_id, kind, payload, run_at) VALUES (?, ?, ?, ?)",
            (guild_id, kind, json.dumps(payload), run_at.isoformat()),
            "Database unavailable, task not scheduled",
        )
        return cursor.lastrowid

    async def due(self, now_iso: str) -> list:
        rows = await self._read_all(
            "SELECT id, guild_id, kind, payload FROM scheduled_tasks WHERE done = 0 AND run_at <= ?",
            (now_iso,),
        )
        tasks = []
        for row in rows:
            # One corrupt payload must not sink the whole batch - the caller is
            # the scheduler loop, and a raised JSONDecodeError would stop it
            # permanently. Skip and log the bad row instead. (review F3)
            try:
                payload = json.loads(row[3])
            except (ValueError, TypeError):
                logger.warning("Skipping scheduled task %s with malformed payload", row[0], exc_info=True)
                continue
            tasks.append((row[0], row[1], row[2], payload))
        return tasks

    async def mark_done(self, task_id: int) -> None:
        # Called from the scheduler loop body, so it returns quietly rather than
        # raising when the DB is down - the task simply gets retried later.
        if not self.db.available:
            return
        await self._write(
            "UPDATE scheduled_tasks SET done = 1 WHERE id = ?",
            (task_id,),
            "Database unavailable, task not marked done",
        )


class ChannelLockStore(_Store):
    """Remembers a channel's @everyone send_messages overwrite from just
    before /lockdown flipped it off, so /lockdown again can restore it -
    stored as text since PermissionOverwrite values are tri-state
    (True/False/None-inherit), not a plain bool."""

    @staticmethod
    def encode(value: Optional[bool]) -> str:
        return "none" if value is None else ("true" if value else "false")

    @staticmethod
    def decode(value: str) -> Optional[bool]:
        return None if value == "none" else value == "true"

    async def set(self, guild_id: int, channel_id: int, previous: Optional[bool]) -> None:
        await self._write(
            "INSERT INTO channel_locks (guild_id, channel_id, previous_send_messages) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, channel_id) DO UPDATE SET "
            "previous_send_messages = excluded.previous_send_messages",
            (guild_id, channel_id, self.encode(previous)),
            "Database unavailable, lock not recorded",
        )

    async def get(self, guild_id: int, channel_id: int) -> Optional[str]:
        row = await self._read_one(
            "SELECT previous_send_messages FROM channel_locks WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        return row[0] if row else None

    async def clear(self, guild_id: int, channel_id: int) -> None:
        await self._write(
            "DELETE FROM channel_locks WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
            "Database unavailable, lock not cleared",
        )


class StarboardStore(_Store):
    async def get(self, guild_id: int, message_id: int) -> Optional[int]:
        row = await self._read_one(
            "SELECT starboard_message_id FROM starboard_posts WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        )
        return row[0] if row else None

    async def set(self, guild_id: int, message_id: int, starboard_message_id: int) -> None:
        await self._write(
            # OR REPLACE: _handle_reaction decides to post by checking get() is None
            # first, so two reactions crossing the threshold together can both reach
            # here. A losing race overwrites rather than raising IntegrityError on
            # the (guild_id, message_id) PK. (review F9)
            "INSERT OR REPLACE INTO starboard_posts "
            "(guild_id, message_id, starboard_message_id) VALUES (?, ?, ?)",
            (guild_id, message_id, starboard_message_id),
            "Database unavailable, starboard post not recorded",
        )


class GiveawayStore(_Store):
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
        cursor = await self._write(
            "INSERT INTO giveaways (guild_id, channel_id, message_id, prize, winner_count, host_id, end_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, message_id, prize, winner_count, host_id, end_at.isoformat()),
            "Database unavailable, giveaway not created",
        )
        return cursor.lastrowid

    async def get(self, giveaway_id: int):
        return await self._read_one(
            "SELECT id, guild_id, channel_id, message_id, prize, winner_count, host_id, end_at, ended "
            "FROM giveaways WHERE id = ?",
            (giveaway_id,),
        )

    async def get_by_message(self, guild_id: int, message_id: int):
        return await self._read_one(
            "SELECT id, guild_id, channel_id, message_id, prize, winner_count, host_id, end_at, ended "
            "FROM giveaways WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        )

    async def toggle_entry(self, giveaway_id: int, user_id: int) -> bool:
        """Enters if not already in, leaves if already in. Returns True if
        the user is now entered, False if they just left."""
        # Delete-first, decided from rowcount, instead of SELECT-then-branch: the
        # shared connection gives no atomicity across a read and a write, so two
        # fast Enter clicks could both see "not entered" and both INSERT, raising an
        # IntegrityError on the (giveaway_id, user_id) PK that no caller catches.
        # Exactly one of these two branches can win now. (review F9)
        message = "Database unavailable, entry not recorded"
        cursor = await self._write(
            "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
            message,
        )
        if cursor.rowcount:
            return False  # they were in; they're now out
        await self._write(
            "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
            (giveaway_id, user_id),
            message,
        )
        return True

    async def entries(self, giveaway_id: int) -> list:
        rows = await self._read_all(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
        )
        return [r[0] for r in rows]

    async def mark_ended(self, giveaway_id: int) -> None:
        # Called from the scheduler loop body - stays quiet rather than raising.
        if not self.db.available:
            return
        await self._write(
            "UPDATE giveaways SET ended = 1 WHERE id = ?",
            (giveaway_id,),
            "Database unavailable, giveaway not marked ended",
        )


class PollStore(_Store):
    async def create(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        question: str,
        options: list,
        end_at: Optional[datetime.datetime],
    ) -> Optional[int]:
        cursor = await self._write(
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
            "Database unavailable, poll not created",
        )
        return cursor.lastrowid

    async def get(self, poll_id: int):
        return await self._read_one(
            "SELECT id, guild_id, channel_id, message_id, question, options, end_at, closed "
            "FROM polls WHERE id = ?",
            (poll_id,),
        )

    async def set_message_id(self, poll_id: int, message_id: int) -> None:
        await self._write(
            "UPDATE polls SET message_id = ? WHERE id = ?",
            (message_id, poll_id),
            "Database unavailable, poll not updated",
        )

    async def get_by_message(self, guild_id: int, message_id: int):
        return await self._read_one(
            "SELECT id, guild_id, channel_id, message_id, question, options, end_at, closed "
            "FROM polls WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        )

    async def open_polls(self) -> list:
        return await self._read_all(
            "SELECT id, guild_id, channel_id, message_id, question, options, end_at, closed "
            "FROM polls WHERE closed = 0"
        )

    async def set_vote(self, poll_id: int, user_id: int, option_index: int) -> str:
        """Casts a vote, switches an existing vote, or (clicking the same
        option again) retracts it. Returns "voted" or "retracted"."""
        # Same delete-first shape as GiveawayStore.toggle_entry, for the same
        # reason: the SELECT-then-branch this replaced had no atomicity across the
        # read and the write, so two fast clicks could interleave and mis-branch
        # (double-retract, or retract a vote the other click just switched). The
        # conditional DELETE decides the branch atomically. (review F9)
        message = "Database unavailable, vote not recorded"
        cursor = await self._write(
            "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ? AND option_index = ?",
            (poll_id, user_id, option_index),
            message,
        )
        if cursor.rowcount:
            return "retracted"
        await self._write(
            "INSERT INTO poll_votes (poll_id, user_id, option_index) VALUES (?, ?, ?) "
            "ON CONFLICT(poll_id, user_id) DO UPDATE SET option_index = excluded.option_index",
            (poll_id, user_id, option_index),
            message,
        )
        return "voted"

    async def vote_counts(self, poll_id: int, option_count: int) -> list:
        counts = [0] * option_count
        rows = await self._read_all(
            "SELECT option_index, COUNT(*) FROM poll_votes WHERE poll_id = ? GROUP BY option_index",
            (poll_id,),
        )
        for index, count in rows:
            if 0 <= index < option_count:
                counts[index] = count
        return counts

    async def close(self, poll_id: int) -> None:
        # Called from the scheduler loop body - stays quiet rather than raising.
        if not self.db.available:
            return
        await self._write(
            "UPDATE polls SET closed = 1 WHERE id = ?",
            (poll_id,),
            "Database unavailable, poll not closed",
        )


class TicketStore(_Store):
    async def create(self, guild_id: int, channel_id: int, opener_id: int) -> None:
        await self._write(
            "INSERT INTO tickets (guild_id, channel_id, opener_id, opened_at) VALUES (?, ?, ?, ?)",
            (guild_id, channel_id, opener_id, datetime.datetime.now(datetime.timezone.utc).isoformat()),
            "Database unavailable, ticket not recorded",
        )

    async def get_by_channel(self, guild_id: int, channel_id: int):
        return await self._read_one(
            "SELECT guild_id, channel_id, opener_id, opened_at, status FROM tickets "
            "WHERE guild_id = ? AND channel_id = ? AND status = 'open'",
            (guild_id, channel_id),
        )

    async def get_open_for_user(self, guild_id: int, opener_id: int):
        return await self._read_one(
            "SELECT guild_id, channel_id, opener_id, opened_at, status FROM tickets "
            "WHERE guild_id = ? AND opener_id = ? AND status = 'open'",
            (guild_id, opener_id),
        )

    async def close(self, guild_id: int, channel_id: int) -> None:
        await self._write(
            "UPDATE tickets SET status = 'closed' WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
            "Database unavailable, ticket not closed",
        )


class LLMLogStore(_Store):
    """One row per LLM exchange, successful or not.

    This is the only record of what Aguiliar actually said - the cog used to log
    nothing but exceptions, so a bad answer left no trace. Two callers depend on
    it: /llmlog (reading it back) and the cog's short-term channel memory, which
    replays the last few rows as prior turns. That second use is why the index
    is on (channel_id, created_at) and why `status` exists: a failed exchange
    must be visible to a human but must never be replayed to the model as if it
    were a real reply.
    """

    async def add(
        self,
        guild_id: int,
        channel_id: int,
        channel_name: Optional[str],
        user_id: int,
        user_name: Optional[str],
        prompt: Optional[str],
        reply: Optional[str],
        tool_calls: Optional[list],
        rounds: int,
        duration_ms: Optional[int],
        model: Optional[str],
        status: str,
        error: Optional[str],
    ) -> None:
        await self._write(
            "INSERT INTO llm_log (guild_id, channel_id, channel_name, user_id, user_name, "
            "prompt, reply, tool_calls, rounds, duration_ms, model, status, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                channel_id,
                channel_name,
                user_id,
                user_name,
                prompt,
                reply,
                json.dumps(tool_calls or []),
                rounds,
                duration_ms,
                model,
                status,
                error,
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
            "Database unavailable, LLM exchange not logged",
        )

    async def recent_for_channel(self, channel_id: int, limit: int, since_iso: str) -> list:
        """Newest-first rows for the memory feature. Only successful exchanges
        with a real reply - a timeout row would otherwise be replayed to the
        model as something it said."""
        return await self._read_all(
            "SELECT user_name, prompt, reply, created_at FROM llm_log "
            "WHERE channel_id = ? AND created_at >= ? AND status = 'ok' "
            "AND reply IS NOT NULL AND reply != '' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (channel_id, since_iso, limit),
        )

    async def recent_for_guild(self, guild_id: int, limit: int) -> list:
        """Newest-first, successes and failures alike - this one is /llmlog."""
        return await self._read_all(
            "SELECT created_at, channel_name, user_name, prompt, reply, tool_calls, "
            "rounds, duration_ms, status, error FROM llm_log "
            "WHERE guild_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (guild_id, limit),
        )

    async def prune(self, older_than_iso: str) -> int:
        cursor = await self._write(
            "DELETE FROM llm_log WHERE created_at < ?",
            (older_than_iso,),
            "Database unavailable, LLM log not pruned",
        )
        return cursor.rowcount if cursor is not None else 0


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
        self.llm_log = LLMLogStore(db)
