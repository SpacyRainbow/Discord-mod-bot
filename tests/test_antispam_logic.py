from bot.modules.antispam import _UserHistory, is_duplicate_spam, is_flooding


def test_is_flooding_under_limit():
    history = _UserHistory()
    now = 100.0
    for t in (98.0, 99.0):
        history.timestamps.append(t)
    history.timestamps.append(now)
    assert is_flooding(history, now, window_seconds=6, max_messages=5) is False


def test_is_flooding_over_limit():
    history = _UserHistory()
    now = 100.0
    for t in (95.0, 96.0, 97.0, 98.0, 99.0, 100.0):
        history.timestamps.append(t)
    assert is_flooding(history, now, window_seconds=6, max_messages=5) is True


def test_is_flooding_expires_old_timestamps():
    history = _UserHistory()
    # these are all outside the 6-second window relative to now=100
    for t in (10.0, 11.0, 12.0, 13.0, 14.0, 15.0):
        history.timestamps.append(t)
    history.timestamps.append(100.0)
    assert is_flooding(history, 100.0, window_seconds=6, max_messages=5) is False


def test_is_duplicate_spam_triggers_after_threshold():
    history = _UserHistory()
    assert is_duplicate_spam(history, "hello", max_duplicates=3) is False
    assert is_duplicate_spam(history, "hello", max_duplicates=3) is False
    assert is_duplicate_spam(history, "hello", max_duplicates=3) is True


def test_is_duplicate_spam_resets_on_different_content():
    history = _UserHistory()
    is_duplicate_spam(history, "hello", max_duplicates=3)
    is_duplicate_spam(history, "hello", max_duplicates=3)
    assert is_duplicate_spam(history, "different message", max_duplicates=3) is False
