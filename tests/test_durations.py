import datetime

import pytest

from bot.durations import parse_duration


def test_parse_duration_minutes():
    assert parse_duration("10m").total_seconds() == 600


def test_parse_duration_hours():
    assert parse_duration("2h").total_seconds() == 7200


def test_parse_duration_days():
    assert parse_duration("1d").total_seconds() == 86400


def test_parse_duration_rejects_empty_string():
    with pytest.raises(ValueError):
        parse_duration("")


def test_parse_duration_rejects_unknown_unit():
    with pytest.raises(ValueError):
        parse_duration("10x")


def test_parse_duration_rejects_non_numeric_amount():
    with pytest.raises(ValueError):
        parse_duration("xm")


def test_parse_duration_rejects_zero():
    with pytest.raises(ValueError):
        parse_duration("0m")


def test_parse_duration_rejects_negative():
    with pytest.raises(ValueError):
        parse_duration("-5m")


def test_parse_duration_rejects_overflow():
    with pytest.raises(ValueError):
        parse_duration("999999999999d")


def test_parse_duration_respects_custom_max():
    with pytest.raises(ValueError):
        parse_duration("31d", max_duration=datetime.timedelta(days=30))


def test_parse_duration_accepts_at_custom_max():
    assert parse_duration("30d", max_duration=datetime.timedelta(days=30)).days == 30
