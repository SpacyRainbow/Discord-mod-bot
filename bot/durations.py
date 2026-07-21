"""
Shared m/h/d duration parsing for commands that need "run N minutes/hours/
days from now" (tempban via /ban, giveaways, polls, /remind). moderation.py
keeps its own separate copy for /mute, since that one also has to handle
the permanent/indefinite case ("perm"/"forever"/no duration at all), which
none of these do - duplicating the small parsing core once was simpler and
lower-risk than threading an extra "is this allowed to be permanent" flag
through the one already-tested mute implementation.
"""

from __future__ import annotations

import datetime

DURATION_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
DEFAULT_MAX_DURATION = datetime.timedelta(days=365)


def parse_duration(
    duration: str, max_duration: datetime.timedelta = DEFAULT_MAX_DURATION
) -> datetime.timedelta:
    """Parses simple durations like '10m', '2h', '1d'. Raises ValueError on
    anything else - a bad unit, a non-numeric amount, a zero/negative
    amount, or a duration past max_duration - so the caller can report a
    clean message instead of crashing."""
    if not duration:
        raise ValueError("Give a duration like 10m, 2h, or 1d.")
    unit = duration[-1].lower()
    if unit not in DURATION_UNITS:
        raise ValueError(f"Unknown duration unit '{unit}'. Use m/h/d, e.g. 10m, 2h, 1d.")
    amount_text = duration[:-1]
    try:
        amount = int(amount_text)
    except ValueError:
        raise ValueError(f"'{amount_text}' isn't a whole number. Use m/h/d, e.g. 10m, 2h, 1d.") from None
    if amount <= 0:
        raise ValueError("Duration must be a positive number, e.g. 10m, 2h, 1d.")
    try:
        delta = datetime.timedelta(**{DURATION_UNITS[unit]: amount})
    except OverflowError:
        raise ValueError("That duration is too long.") from None
    if delta > max_duration:
        raise ValueError(f"That duration is too long (max {max_duration.days} days).")
    return delta
