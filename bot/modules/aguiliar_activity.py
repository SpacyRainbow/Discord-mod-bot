"""
aguiliar_activity - the cheap, code-side half of Aguilar's autonomous wake-up.

Everything in this file is pure logic: no discord objects, no database, no
network, no LLM. That is the point. The expensive half (one inference, in
aguiliar.py) must only ever run after this half has already said yes, and this
half has to be cheap enough to run on a 60s loop forever without anybody
noticing. It is also the half worth unit-testing, and it can be tested without
a gateway connection or a model.

WHAT IT DECIDES
Given a rolling window of recent message metadata per channel, it answers two
questions and nothing else:
  1. Which eligible channel, if any, is having a real multi-human conversation
     right now (score_channel / pick_channel).
  2. Is Aguilar allowed to consider participating at all (gate_reasons) -
     idle long enough, off cooldown, under the daily cap, outside quiet hours,
     and then, last of all, does the probability roll pass.

The ORDER matters and is deliberate: the random gate is applied LAST, after
every deterministic check has already passed. Randomness decides whether
Aguilar takes one of the reasonable opportunities available; it never promotes
an unreasonable opportunity into an acceptable one.

WHAT COUNTS AS A CONVERSATION
Not "message volume". One person posting thirty times is not a conversation and
must not read as one, so the score is built from distinct humans first and
message count second, bot/webhook/system messages are excluded from both, and a
channel where Aguilar itself spoke recently is penalised rather than rewarded.
A short burst by two people beats a monologue by one, every time.

STATE AND RESTARTS
The tracker's window is in memory and is SUPPOSED to be lost on restart - a
restart genuinely does mean "I have not seen any conversation yet", and an
empty window fails closed (no candidate, no wake-up). Cooldowns are the
opposite: losing those on restart would let a redeploy immediately re-arm a
bot that had just spoken. Those live in AutonomyState, which serialises to a
small JSON blob the cog persists in the config store, so a restart resumes the
cooldowns it was already serving.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# One entry per message kept in the rolling window. Deliberately not a
# discord.Message: holding real message objects for every channel would pin
# their whole attachment/embed graph in memory for as long as the window is
# open, and none of the scoring needs anything but these four fields.
@dataclass(frozen=True)
class Seen:
    author_id: int
    is_bot: bool
    at: float           # time.monotonic()
    content_len: int


# A window entry older than this is dropped regardless of configuration, so the
# per-channel deques cannot grow without bound on a busy server.
WINDOW_HARD_MAX_SECONDS = 3600.0
# Per-channel cap on retained entries. The scoring only ever needs counts and
# distinct authors over a few minutes; a channel doing 500 messages an hour must
# not cost 500 entries.
WINDOW_MAX_ENTRIES = 80
# Channels untouched for this long are dropped entirely. Same reasoning as
# bored.py's _sweep_activity (review F10): without it the dict holds one entry
# per channel that has ever seen a message.
CHANNEL_MAX_IDLE_SECONDS = 86400.0

# Scoring weights. Distinct humans dominate on purpose - see WHAT COUNTS AS A
# CONVERSATION above. These are constants rather than config because they are
# the shape of the heuristic, not a knob anybody should be turning per guild;
# the thresholds that decide eligibility ARE config.
SCORE_PER_HUMAN = 3.0
SCORE_PER_MESSAGE = 0.5
SCORE_RECENCY_BONUS = 2.0        # awarded when the newest message is very fresh
RECENCY_FRESH_SECONDS = 120.0
SCORE_BOT_HEAVY_PENALTY = 4.0    # applied when bots outnumber humans
SCORE_SELF_RECENT_PENALTY = 6.0  # Aguilar already spoke here recently
# A channel whose traffic is overwhelmingly one person is a monologue. Measured
# as "the busiest single human wrote more than this fraction of the messages".
MONOLOGUE_SHARE = 0.8
SCORE_MONOLOGUE_PENALTY = 5.0
# Very short messages are usually reactions to something rather than content;
# a window that is nothing but "lol"/"yeah" is not a conversation worth joining.
SHORT_MESSAGE_CHARS = 4


@dataclass
class ChannelStats:
    """What the scorer saw. Returned alongside the score so the log line can say
    WHY a channel was or was not picked - a bare score is unreviewable."""
    messages: int = 0
    humans: int = 0
    bot_messages: int = 0
    newest_age: float = float("inf")
    top_human_share: float = 0.0
    self_spoke_ago: float = float("inf")
    score: float = 0.0

    def as_log(self) -> str:
        return (f"msgs={self.messages} humans={self.humans} bots={self.bot_messages} "
                f"newest={self.newest_age:.0f}s share={self.top_human_share:.2f} "
                f"score={self.score:.1f}")


class ActivityTracker:
    """The rolling window. `record` is called from on_message for every message
    in the server and must stay trivially cheap - it is on the hot path of a
    listener that already runs on every message."""

    def __init__(self) -> None:
        self._seen: Dict[int, List[Seen]] = {}
        self._self_spoke: Dict[int, float] = {}

    def record(self, channel_id: int, author_id: int, is_bot: bool,
               content_len: int, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        entries = self._seen.setdefault(channel_id, [])
        entries.append(Seen(author_id, is_bot, now, content_len))
        if len(entries) > WINDOW_MAX_ENTRIES:
            # Trim from the front: the window is newest-biased by definition.
            del entries[:-WINDOW_MAX_ENTRIES]

    def record_self(self, channel_id: int, now: Optional[float] = None) -> None:
        """Aguilar spoke or reacted here. Tracked separately from `record` so it
        still counts even when the bot's own message never reaches on_message."""
        self._self_spoke[channel_id] = time.monotonic() if now is None else now

    def channels(self) -> Iterable[int]:
        return list(self._seen)

    def stats(self, channel_id: int, window_seconds: float,
              now: Optional[float] = None) -> ChannelStats:
        now = time.monotonic() if now is None else now
        cutoff = now - min(window_seconds, WINDOW_HARD_MAX_SECONDS)
        entries = [e for e in self._seen.get(channel_id, []) if e.at >= cutoff]
        stats = ChannelStats()
        if not entries:
            return stats
        humans = [e for e in entries if not e.is_bot]
        stats.messages = len(humans)
        stats.bot_messages = len(entries) - len(humans)
        stats.newest_age = now - max(e.at for e in entries)
        stats.self_spoke_ago = now - self._self_spoke.get(channel_id, float("-inf"))
        per_author: Dict[int, int] = {}
        for entry in humans:
            per_author[entry.author_id] = per_author.get(entry.author_id, 0) + 1
        stats.humans = len(per_author)
        if humans:
            stats.top_human_share = max(per_author.values()) / len(humans)
        stats.score = self._score(stats, humans)
        return stats

    @staticmethod
    def _score(stats: ChannelStats, humans: List[Seen]) -> float:
        if not stats.messages:
            return 0.0
        score = stats.humans * SCORE_PER_HUMAN + stats.messages * SCORE_PER_MESSAGE
        if stats.newest_age <= RECENCY_FRESH_SECONDS:
            score += SCORE_RECENCY_BONUS
        if stats.bot_messages > stats.messages:
            score -= SCORE_BOT_HEAVY_PENALTY
        if stats.humans <= 1 or stats.top_human_share > MONOLOGUE_SHARE:
            score -= SCORE_MONOLOGUE_PENALTY
        # An all-"lol" window: technically several humans, nothing to join.
        # The volume bonus is taken back rather than merely penalised - twenty
        # one-word messages are not twice the conversation of ten, so the count
        # must not be able to carry a contentless window over the threshold on
        # its own. (Found by test_an_all_one_word_window_is_not_a_conversation:
        # a flat penalty still scored 12 against a default minimum of 10.)
        if all(entry.content_len <= SHORT_MESSAGE_CHARS for entry in humans):
            score -= stats.messages * SCORE_PER_MESSAGE + SCORE_MONOLOGUE_PENALTY
        if stats.self_spoke_ago < RECENCY_FRESH_SECONDS:
            score -= SCORE_SELF_RECENT_PENALTY
        return score

    def sweep(self, now: Optional[float] = None) -> int:
        """Drops channels nothing has happened in for a day. Returns how many."""
        now = time.monotonic() if now is None else now
        dropped = 0
        for channel_id in list(self._seen):
            entries = self._seen[channel_id]
            if entries and now - entries[-1].at <= CHANNEL_MAX_IDLE_SECONDS:
                continue
            self._seen.pop(channel_id, None)
            self._self_spoke.pop(channel_id, None)
            dropped += 1
        return dropped


@dataclass
class AutonomyConfig:
    """Every threshold, resolved from guild config once per check. Defaults are
    deliberately timid: this feature is off unless it is switched on, and its
    channel allowlist is empty-means-NONE rather than empty-means-everywhere.
    That is the opposite of llm.channels, and it is intentional - being pinged
    in a channel is consent, wandering into one uninvited is not."""
    enabled: bool = False
    channels: Tuple[int, ...] = ()
    # "all" - every text channel the bot can actually read and send in, resolved
    # per tick rather than stored, so a new channel is covered without anybody
    # editing config. Deliberately a separate flag rather than a magic empty
    # tuple: empty still means NONE, and the two must not be confusable.
    all_channels: bool = False
    # Always wins, in both modes. An allowlist of "everything" is only usable if
    # there is a way to carve pieces out of it.
    exclude: Tuple[int, ...] = ()
    idle_seconds: float = 2700.0          # 45 min since Aguilar last spoke
    window_seconds: float = 600.0         # 10 min of conversation considered
    min_messages: int = 8
    min_humans: int = 3
    min_score: float = 10.0
    chance_percent: int = 25
    cooldown_seconds: float = 5400.0      # 90 min, any channel
    channel_cooldown_seconds: float = 10800.0   # 3 h, same channel
    eval_cooldown_seconds: float = 1200.0       # 20 min after a NO_ACTION
    max_per_day: int = 6
    quiet_start: int = -1                 # local hour, -1 disables
    quiet_end: int = -1
    allow_reply: bool = True

    def allows(self, channel_id: int) -> bool:
        """Whether this channel is eligible at all. The denylist is checked
        first and applies to both modes: a channel named in llm.auto.exclude is
        out even if somebody also named it in llm.auto.channels, because the
        safe reading of a contradiction is the restrictive one."""
        if channel_id in self.exclude:
            return False
        return self.all_channels or channel_id in self.channels


@dataclass
class AutonomyState:
    """Cooldown bookkeeping that must survive a restart.

    Times here are WALL CLOCK (time.time()), unlike the tracker's monotonic
    window: a value that has to be written to a database and read back after a
    process restart cannot be monotonic, because monotonic zero moves."""
    last_action_at: float = 0.0
    last_channel_action: Dict[int, float] = field(default_factory=dict)
    last_eval: Dict[int, float] = field(default_factory=dict)
    reacted_messages: List[int] = field(default_factory=list)
    recent_targets: List[int] = field(default_factory=list)
    day: str = ""
    day_count: int = 0

    # Bounded so the persisted blob cannot grow forever.
    REACTED_MAX = 50
    TARGETS_MAX = 10

    def to_dict(self) -> dict:
        return {
            "last_action_at": self.last_action_at,
            "last_channel_action": {str(k): v for k, v in self.last_channel_action.items()},
            "last_eval": {str(k): v for k, v in self.last_eval.items()},
            "reacted_messages": self.reacted_messages[-self.REACTED_MAX:],
            "recent_targets": self.recent_targets[-self.TARGETS_MAX:],
            "day": self.day,
            "day_count": self.day_count,
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "AutonomyState":
        """Fails soft on anything malformed. A corrupt state blob must degrade to
        "no cooldowns recorded" rather than stop the loop - but note that this
        is the one direction that fails OPEN, so it is kept to plain coercion
        with no way to raise."""
        state = cls()
        if not isinstance(raw, dict):
            return state
        try:
            state.last_action_at = float(raw.get("last_action_at") or 0.0)
            state.day = str(raw.get("day") or "")
            state.day_count = int(raw.get("day_count") or 0)
            state.last_channel_action = {
                int(k): float(v) for k, v in (raw.get("last_channel_action") or {}).items()
            }
            state.last_eval = {
                int(k): float(v) for k, v in (raw.get("last_eval") or {}).items()
            }
            state.reacted_messages = [int(x) for x in (raw.get("reacted_messages") or [])]
            state.recent_targets = [int(x) for x in (raw.get("recent_targets") or [])]
        except (TypeError, ValueError, AttributeError):
            return cls()
        return state

    def already_reacted(self, message_id: int) -> bool:
        return message_id in self.reacted_messages

    def note_reaction(self, message_id: int) -> None:
        self.reacted_messages.append(message_id)
        del self.reacted_messages[:-self.REACTED_MAX]

    def note_target(self, user_id: int) -> None:
        self.recent_targets.append(user_id)
        del self.recent_targets[:-self.TARGETS_MAX]

    def targeted_recently(self, user_id: int, last: int = 3) -> bool:
        """Whether this person was the target of one of the last few autonomous
        actions. Stops Aguilar from repeatedly picking on the same member, which
        reads as fixation rather than banter."""
        return user_id in self.recent_targets[-last:]

    def note_action(self, channel_id: int, now: float, day: str) -> None:
        self.last_action_at = now
        self.last_channel_action[channel_id] = now
        if day != self.day:
            self.day, self.day_count = day, 0
        self.day_count += 1
        self._trim()

    def note_eval(self, channel_id: int, now: float) -> None:
        self.last_eval[channel_id] = now
        self._trim()

    def _trim(self) -> None:
        for mapping in (self.last_channel_action, self.last_eval):
            if len(mapping) <= 40:
                continue
            for key in sorted(mapping, key=mapping.get)[:len(mapping) - 40]:
                mapping.pop(key, None)


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Inclusive of `start`, exclusive of `end`, and wraps over midnight, which
    is the only case anybody actually configures (23 -> 8)."""
    if start < 0 or end < 0 or start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def gate_reasons(config: AutonomyConfig, state: AutonomyState, *,
                 idle_seconds: float, channel_id: int, stats: ChannelStats,
                 now: float, local_hour: int, day: str) -> List[str]:
    """Every deterministic reason this wake-up must NOT happen, in one list.

    Returns reasons rather than a bool so the log line can say which gate
    stopped it - "skipped" with no cause is not observable. An empty list means
    every check passed and only the probability roll is left. The roll is NOT
    performed here: see roll_passes, and the note about ordering at the top of
    this file."""
    reasons: List[str] = []
    if not config.enabled:
        reasons.append("disabled")
    if not config.channels and not config.all_channels:
        reasons.append("no-allowlist")
    elif not config.allows(channel_id):
        reasons.append("channel-not-allowed")
    if idle_seconds < config.idle_seconds:
        reasons.append(f"bot-not-idle({idle_seconds:.0f}s)")
    if in_quiet_hours(local_hour, config.quiet_start, config.quiet_end):
        reasons.append("quiet-hours")
    if now - state.last_action_at < config.cooldown_seconds:
        reasons.append("global-cooldown")
    if now - state.last_channel_action.get(channel_id, 0.0) < config.channel_cooldown_seconds:
        reasons.append("channel-cooldown")
    if now - state.last_eval.get(channel_id, 0.0) < config.eval_cooldown_seconds:
        reasons.append("eval-cooldown")
    if state.day == day and state.day_count >= config.max_per_day:
        reasons.append("daily-cap")
    if stats.messages < config.min_messages:
        reasons.append(f"too-few-messages({stats.messages})")
    if stats.humans < config.min_humans:
        reasons.append(f"too-few-humans({stats.humans})")
    if stats.score < config.min_score:
        reasons.append(f"low-score({stats.score:.1f})")
    return reasons


def roll_passes(chance_percent: int, rng: Optional[random.Random] = None) -> bool:
    """The LAST gate, and the only non-deterministic one."""
    if chance_percent <= 0:
        return False
    if chance_percent >= 100:
        return True
    return (rng or random).randrange(100) < chance_percent


# Skip reasons that are about the BOT or the CLOCK, not about one channel.
# Seeing one of these means no other channel would fare better either, so the
# tick can stop instead of scoring the rest.
GUILD_WIDE_SKIPS = frozenset({"disabled", "no-allowlist", "quiet-hours",
                              "global-cooldown", "daily-cap"})


def rank_channels(candidates: List[Tuple[int, ChannelStats]]) -> List[Tuple[int, ChannelStats]]:
    """Every viable candidate, best first. Same ordering as pick_channel; the
    caller walks it so that a channel blocked by its OWN cooldown does not hide
    the next one down."""
    viable = [(cid, st) for cid, st in candidates if st.score > 0]
    return sorted(viable, key=lambda item: (item[1].score, -item[1].newest_age), reverse=True)


def pick_channel(candidates: List[Tuple[int, ChannelStats]]) -> Optional[Tuple[int, ChannelStats]]:
    """Highest score wins; ties break toward the more recent conversation. A
    non-positive score is never a candidate no matter how empty the field is."""
    viable = [(cid, st) for cid, st in candidates if st.score > 0]
    if not viable:
        return None
    return max(viable, key=lambda item: (item[1].score, -item[1].newest_age))
