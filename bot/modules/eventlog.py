"""
Channel event log: a verbatim record of what actually happened in a channel.

WHY THIS EXISTS, WHEN llm_log ALREADY EXISTS
llm_log answers "what was the model told, and what did it say back". It cannot
answer "and was that true": it stores the prompt the bot ASSEMBLED, so a
message the gap transcript never picked up is invisible in it by construction,
and a reaction it placed is recorded as a tool call rather than as a thing that
appeared in the room. Every interesting failure so far has needed both halves -
what the model saw, and what was actually there - and the second half had to be
reconstructed by hand from container logs and message IDs.

This is the second half. It records messages, edits, deletes and reactions in
the channels it is pointed at, as they happen, with IDs and timestamps, so an
exchange can be diffed against the room it happened in.

IT IS OFF UNTIL SOMEBODY TURNS IT ON, PER CHANNEL
`eventlog.channels` is empty by default and an empty list means NONE, not all -
the same convention as `llm.auto.channels`, and for a stronger reason here.
This records what people say, verbatim, and nothing prunes it: that is a
deliberate choice for a debugging log on a private server, and it is the wrong
default for anybody else's. Point it at the channel you are debugging.

A SEPARATE DATABASE FILE
`EVENTLOG_DB_PATH` (default /app/data/eventlog.db), not a table in the bot's
own database. Chat volume does not belong in the file that holds operational
state, this one can be copied off and queried without touching a live database,
and it can be deleted wholesale without a migration. SQLite ATTACH still joins
the two when comparing an exchange against the room:

    ATTACH '/app/data/eventlog.db' AS ev;
    SELECT * FROM ev.events WHERE channel_id = ? AND created_at BETWEEN ? AND ?;

NOTHING HERE MAY BREAK THE BOT
Every listener is best-effort and swallows its own exceptions, exactly like
llm_log: a debugging log that can take a message handler down with it costs
more than it is worth. A write failure marks the database unavailable and the
bot carries on.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Any, Dict, FrozenSet, List, Optional, Tuple, Union

import discord
from discord.ext import commands

from ..db import Database

logger = logging.getLogger("bot.eventlog")

DEFAULT_DB_PATH = "/app/data/eventlog.db"

# One row per thing that happened. Deliberately one wide table rather than a
# table per kind: every question worth asking is "what happened in this channel
# between these two times", and that is one ORDER BY over one table.
EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    guild_id INTEGER,
    channel_id INTEGER,
    channel_name TEXT,
    message_id INTEGER,
    author_id INTEGER,
    author_name TEXT,
    author_bot INTEGER,
    content TEXT,
    old_content TEXT,
    reference_id INTEGER,
    attachments TEXT,
    emoji TEXT,
    actor_id INTEGER,
    actor_name TEXT,
    message_created_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_channel ON events (channel_id, id);
CREATE INDEX IF NOT EXISTS idx_events_message ON events (message_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events (created_at);
"""
# Same ADD COLUMN mechanism the main schema uses: never drop, never rewrite.
EVENT_ADDED_COLUMNS: List[Tuple[str, str, str]] = []

# The kinds. There is no "everything else" kind on purpose - an event this does
# not understand is not recorded rather than recorded as a shrug.
KIND_MESSAGE = "message"
KIND_EDIT = "edit"
KIND_DELETE = "delete"
KIND_REACTION_ADD = "reaction_add"
KIND_REACTION_REMOVE = "reaction_remove"
EVENT_KINDS = frozenset({KIND_MESSAGE, KIND_EDIT, KIND_DELETE,
                         KIND_REACTION_ADD, KIND_REACTION_REMOVE})

# The literal that opts a guild into recording every channel. Spelled out
# rather than implied by an empty setting, which means the opposite here.
WATCH_ALL = "all"
CONFIG_CHANNELS = "eventlog.channels"

# Content is stored verbatim up to this, which is well past Discord's own
# message limit for anything but a nitro post. The cap exists so one pasted
# logfile cannot put a megabyte in a row, not to summarise.
CONTENT_CHAR_CAP = 4000
# Attachments are recorded as filenames and sizes, never fetched and never
# stored: this is a log of what happened, not a mirror of the channel's media.
ATTACHMENTS_MAX = 10


def parse_watched(raw: Optional[str]) -> Union[str, FrozenSet[int]]:
    """The configured channel list: WATCH_ALL, or a set of channel IDs.

    An empty or unset value is an EMPTY SET, which records nothing. That is the
    opposite of `llm.channels`, where empty means everywhere, and it is
    deliberate: the cost of a wrong default here is a verbatim record of a
    conversation nobody meant to record.
    """
    text = str(raw or "").strip()
    if not text:
        return frozenset()
    if text.lower() == WATCH_ALL:
        return WATCH_ALL
    ids = set()
    for part in text.replace(" ", ",").split(","):
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return frozenset(ids)


def is_watched(channel_id: Optional[int], raw: Optional[str]) -> bool:
    watched = parse_watched(raw)
    if watched == WATCH_ALL:
        return True
    return channel_id is not None and channel_id in watched


def now_iso() -> str:
    """UTC, explicit. The container has no TZ set, and a log whose timestamps
    cannot be compared against llm_log's is not a log you can diff anything
    against."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def describe_attachments(message: Any) -> Optional[str]:
    """Filenames, sizes and content types as JSON, or None. Never the bytes and
    never the URL's signature - those expire, and a stale link in a log reads as
    evidence when it is not."""
    found = list(getattr(message, "attachments", None) or [])[:ATTACHMENTS_MAX]
    if not found:
        return None
    described = [{
        "filename": getattr(item, "filename", None),
        "size": getattr(item, "size", None),
        "content_type": getattr(item, "content_type", None),
    } for item in found]
    return json.dumps(described)


def _cap(text: Any) -> Optional[str]:
    if text is None:
        return None
    return str(text)[:CONTENT_CHAR_CAP]


def message_row(message: Any, kind: str = KIND_MESSAGE,
                old_content: Any = None) -> Dict[str, Any]:
    """One row from a discord.Message. Pure, so the shape is testable without a
    gateway: every listener below is a thin wrapper around one of these."""
    author = getattr(message, "author", None)
    channel = getattr(message, "channel", None)
    guild = getattr(message, "guild", None)
    reference = getattr(message, "reference", None)
    created = getattr(message, "created_at", None)
    return {
        "kind": kind,
        "guild_id": getattr(guild, "id", None),
        "channel_id": getattr(channel, "id", None),
        "channel_name": getattr(channel, "name", None),
        "message_id": getattr(message, "id", None),
        "author_id": getattr(author, "id", None),
        # The display name, matching what llm_log stores, so the two line up
        # without a lookup.
        "author_name": getattr(author, "display_name", None) or getattr(author, "name", None),
        "author_bot": int(bool(getattr(author, "bot", False))),
        "content": _cap(getattr(message, "content", None)),
        "old_content": _cap(old_content),
        "reference_id": getattr(reference, "message_id", None),
        "attachments": describe_attachments(message),
        "emoji": None,
        "actor_id": None,
        "actor_name": None,
        "message_created_at": created.isoformat() if created is not None else None,
        "created_at": now_iso(),
    }


def reaction_row(payload: Any, kind: str) -> Dict[str, Any]:
    """One row from a raw reaction payload.

    RAW on purpose: on_reaction_add only fires for messages in the cache, so a
    reaction on anything older than the process would simply not be recorded -
    and "the reaction is missing from the log" would read as "the reaction never
    happened", which is the exact failure this log exists to prevent.
    """
    member = getattr(payload, "member", None)
    return {
        "kind": kind,
        "guild_id": getattr(payload, "guild_id", None),
        "channel_id": getattr(payload, "channel_id", None),
        "channel_name": None,
        "message_id": getattr(payload, "message_id", None),
        "author_id": None,
        "author_name": None,
        "author_bot": None,
        "content": None,
        "old_content": None,
        "reference_id": None,
        "attachments": None,
        "emoji": str(getattr(payload, "emoji", "") or "") or None,
        "actor_id": getattr(payload, "user_id", None),
        "actor_name": (getattr(member, "display_name", None)
                       or getattr(member, "name", None)),
        "message_created_at": None,
        "created_at": now_iso(),
    }


_COLUMNS = ("kind", "guild_id", "channel_id", "channel_name", "message_id",
            "author_id", "author_name", "author_bot", "content", "old_content",
            "reference_id", "attachments", "emoji", "actor_id", "actor_name",
            "message_created_at", "created_at")
INSERT_SQL = (f"INSERT INTO events ({', '.join(_COLUMNS)}) "
              f"VALUES ({', '.join('?' * len(_COLUMNS))})")


def row_values(row: Dict[str, Any]) -> Tuple:
    """Dict -> the tuple INSERT_SQL expects, in column order. Keeps the ordering
    in exactly one place: a row built by hand in the wrong order would write
    somebody's name into the content column and nothing would complain."""
    return tuple(row.get(column) for column in _COLUMNS)


class EventLog(commands.Cog):
    """Records what happens in the watched channels. Reads nothing back - the
    file is meant to be queried directly, next to llm_log."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = Database(os.getenv("EVENTLOG_DB_PATH", DEFAULT_DB_PATH),
                           schema=EVENT_SCHEMA, added_columns=EVENT_ADDED_COLUMNS)

    async def cog_load(self) -> None:
        try:
            await self.db.connect()
        except Exception:
            # Never fatal. The bot loses a debugging log, not a feature anybody
            # is waiting on.
            logger.warning("eventlog: could not open its database", exc_info=True)

    async def cog_unload(self) -> None:
        await self.db.close()

    async def _watched(self, guild_id: Optional[int], channel_id: Optional[int]) -> bool:
        if guild_id is None or not self.db.available:
            return False
        raw = await self.bot.stores.config.get(guild_id, CONFIG_CHANNELS, None)
        return is_watched(channel_id, raw)

    async def _record(self, row: Dict[str, Any]) -> None:
        if not self.db.available or self.db.conn is None:
            return
        try:
            await self.db.conn.execute(INSERT_SQL, row_values(row))
            await self.db.conn.commit()
        except Exception:
            # Mark it unavailable rather than retrying into a dead handle on
            # every message for the rest of the process's life.
            self.db.available = False
            logger.warning("eventlog: write failed, log disabled until restart",
                           exc_info=True)

    # --- listeners ----------------------------------------------------------
    # Each one is a guard, a pure row builder, and a write. They swallow
    # everything: a listener that raises is logged by discord.py and the next
    # one still runs, but a stack trace per message is its own outage.

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if message.guild is None:
                return
            if not await self._watched(message.guild.id, message.channel.id):
                return
            await self._record(message_row(message))
        except Exception:
            logger.debug("eventlog: on_message failed", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        try:
            if not await self._watched(payload.guild_id, payload.channel_id):
                return
            cached = getattr(payload, "cached_message", None)
            data = getattr(payload, "data", None) or {}
            if cached is not None:
                row = message_row(cached, KIND_EDIT, old_content=cached.content)
                # The cache holds the message as it was BEFORE the edit, so the
                # new text has to come from the payload or the row would record
                # an edit that changed nothing.
                row["content"] = _cap(data.get("content", cached.content))
            else:
                row = {
                    **message_row(_Missing(), KIND_EDIT),
                    "guild_id": payload.guild_id,
                    "channel_id": payload.channel_id,
                    "message_id": payload.message_id,
                    "content": _cap(data.get("content")),
                }
            await self._record(row)
        except Exception:
            logger.debug("eventlog: on_raw_message_edit failed", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        try:
            if not await self._watched(payload.guild_id, payload.channel_id):
                return
            cached = getattr(payload, "cached_message", None)
            if cached is not None:
                row = message_row(cached, KIND_DELETE)
            else:
                # An uncached delete is only ever an ID. Recorded anyway: "this
                # message stopped existing at this time" is the fact worth
                # having, and a gap in the log would be indistinguishable from
                # the message never being sent.
                row = {
                    **message_row(_Missing(), KIND_DELETE),
                    "guild_id": payload.guild_id,
                    "channel_id": payload.channel_id,
                    "message_id": payload.message_id,
                }
            await self._record(row)
        except Exception:
            logger.debug("eventlog: on_raw_message_delete failed", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        try:
            if not await self._watched(payload.guild_id, payload.channel_id):
                return
            await self._record(reaction_row(payload, KIND_REACTION_ADD))
        except Exception:
            logger.debug("eventlog: on_raw_reaction_add failed", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        try:
            if not await self._watched(payload.guild_id, payload.channel_id):
                return
            await self._record(reaction_row(payload, KIND_REACTION_REMOVE))
        except Exception:
            logger.debug("eventlog: on_raw_reaction_remove failed", exc_info=True)


class _Missing:
    """Stands in for a message the cache does not have, so message_row's getattr
    walk produces a full row of Nones instead of the caller assembling one by
    hand and getting a column wrong."""


async def setup(bot: commands.Bot):
    await bot.add_cog(EventLog(bot))
