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
    # (channel_id, message_id) pairs rather than Message objects - see F10.
    refs = [(1, 10), (2, 20), (1, 11)]
    grouped = group_by_channel(refs)

    assert set(grouped.keys()) == {1, 2}
    assert grouped[1] == [10, 11]
    assert grouped[2] == [20]


def test_group_by_channel_empty_list_returns_empty_dict():
    assert group_by_channel([]) == {}


# --- review F10: _history/_locks were keyed by every user who ever posted ---


def _cog():
    from unittest.mock import MagicMock

    from bot.modules.antispam import AntiSpam

    return AntiSpam(MagicMock())


def test_sweep_drops_a_user_whose_history_is_stale():
    cog = _cog()
    now = 10_000.0
    cog._history[(1, 2)].timestamps.append(now - 7200)
    cog._locks[(1, 2)]  # materialise the lock too
    assert cog._sweep(now) == 1
    assert (1, 2) not in cog._history
    assert (1, 2) not in cog._locks


def test_sweep_keeps_a_user_who_posted_recently():
    cog = _cog()
    now = 10_000.0
    cog._history[(1, 2)].timestamps.append(now - 5)
    assert cog._sweep(now) == 0
    assert (1, 2) in cog._history


async def test_sweep_never_evicts_a_lock_that_is_currently_held():
    """Evicting a held lock hands the holder and the next caller two different
    Lock objects for the same key, silently undoing phase 2's F9 race fix."""
    cog = _cog()
    now = 10_000.0
    cog._history[(1, 2)].timestamps.append(now - 7200)  # stale enough to evict
    lock = cog._locks[(1, 2)]
    async with lock:
        assert cog._sweep(now) == 0
        assert (1, 2) in cog._history
        assert cog._locks[(1, 2)] is lock


async def test_sweep_loop_body_survives_a_raising_sweep():
    from unittest.mock import MagicMock

    cog = _cog()
    cog._sweep = MagicMock(side_effect=RuntimeError("boom"))
    await cog.sweep_history.coro(cog)  # must not raise (review F4)
