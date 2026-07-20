from bot.modules.antispam import (
    _UserHistory,
    group_by_channel,
    is_duplicate_spam,
    is_flooding,
    sync_message_window,
)


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


def test_user_history_reset_clears_everything():
    history = _UserHistory()
    history.timestamps.append(1.0)
    history.messages.append("fake-message")
    history.last_content = "hello"
    history.duplicate_count = 2

    history.reset()

    assert list(history.timestamps) == []
    assert list(history.messages) == []
    assert history.last_content == ""
    assert history.duplicate_count == 0


def test_sync_message_window_trims_to_match_timestamps():
    history = _UserHistory()
    for t in (1.0, 2.0, 3.0):
        history.timestamps.append(t)
    for m in ("a", "b", "c", "d", "e"):  # more messages than timestamps
        history.messages.append(m)

    sync_message_window(history)

    assert list(history.messages) == ["c", "d", "e"]


def test_sync_message_window_no_op_when_already_in_sync():
    history = _UserHistory()
    history.timestamps.append(1.0)
    history.messages.append("only-message")

    sync_message_window(history)

    assert list(history.messages) == ["only-message"]


def test_group_by_channel_groups_messages_by_channel_id():
    class FakeMessage:
        def __init__(self, channel_id):
            self.channel = type("Channel", (), {"id": channel_id})()

    messages = [FakeMessage(1), FakeMessage(2), FakeMessage(1)]
    grouped = group_by_channel(messages)

    assert set(grouped.keys()) == {1, 2}
    assert len(grouped[1]) == 2
    assert len(grouped[2]) == 1


def test_group_by_channel_empty_list_returns_empty_dict():
    assert group_by_channel([]) == {}
