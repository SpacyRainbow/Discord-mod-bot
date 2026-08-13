from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.modules import updater
from bot.modules.updater import UpdateStatus, Updater, check_for_update, describe_status
from bot.stores import Stores

GUILD = 111


def test_describe_status_unchecked():
    assert "Unable to check" in describe_status(UpdateStatus(checked=False))


def test_describe_status_up_to_date():
    assert describe_status(UpdateStatus(checked=True, available=False)) == "Up to date"


def test_describe_status_available_with_behind_and_summary():
    status = UpdateStatus(checked=True, available=True, behind=3, latest_summary="fix the thing")
    text = describe_status(status)
    assert "3 commit(s) behind" in text
    assert "fix the thing" in text


def test_describe_status_available_without_extra_detail_falls_back():
    status = UpdateStatus(checked=True, available=True, behind=None, latest_summary=None)
    assert describe_status(status) == "Update available"


@pytest.mark.asyncio
async def test_check_for_update_returns_unchecked_when_not_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "REPO_DIR", tmp_path)

    status = await check_for_update()

    assert status.checked is False
    assert status.available is False


@pytest.mark.asyncio
async def test_check_for_update_returns_unchecked_when_fetch_fails(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(updater, "REPO_DIR", tmp_path)

    async def fake_run_git(*args):
        if args[0] == "fetch":
            return 1, ""
        raise AssertionError("should not run further git commands after a failed fetch")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    status = await check_for_update()

    assert status.checked is False


@pytest.mark.asyncio
async def test_check_for_update_reports_up_to_date_when_shas_match(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(updater, "REPO_DIR", tmp_path)

    responses = {
        ("fetch", "--quiet"): (0, ""),
        ("rev-parse", "--abbrev-ref", "HEAD"): (0, "main"),
        ("rev-parse", "HEAD"): (0, "abc123"),
        ("rev-parse", "origin/main"): (0, "abc123"),
    }

    async def fake_run_git(*args):
        return responses[args]

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    status = await check_for_update()

    assert status.checked is True
    assert status.available is False


@pytest.mark.asyncio
async def test_check_for_update_reports_available_with_behind_count_and_summary(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(updater, "REPO_DIR", tmp_path)

    responses = {
        ("fetch", "--quiet"): (0, ""),
        ("rev-parse", "--abbrev-ref", "HEAD"): (0, "main"),
        ("rev-parse", "HEAD"): (0, "abc123"),
        ("rev-parse", "origin/main"): (0, "def456"),
        ("rev-list", "--count", "HEAD..origin/main"): (0, "2"),
        ("log", "-1", "--format=%s", "origin/main"): (0, "bump version"),
    }

    async def fake_run_git(*args):
        return responses[args]

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    status = await check_for_update()

    assert status.checked is True
    assert status.available is True
    assert status.behind == 2
    assert status.latest_summary == "bump version"


@pytest.mark.asyncio
async def test_run_git_against_the_real_repo_returns_a_commit_sha():
    """Lightweight integration check that the subprocess wiring itself
    works, against this repo's own real .git - no network call (no
    fetch), just confirming `_run_git` can shell out and parse output."""
    code, sha = await updater._run_git("rev-parse", "HEAD")
    assert code == 0
    assert len(sha) == 40


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    return bot


def _make_cog(bot):
    cog = Updater(bot)
    cog.check_loop.cancel()
    return cog


@pytest.mark.asyncio
async def test_check_loop_does_not_auto_apply_when_disabled(db):
    bot = _make_bot(db)
    bot.guilds = [MagicMock(id=GUILD)]
    cog = _make_cog(bot)
    cog.apply_update = AsyncMock()
    available = AsyncMock(return_value=UpdateStatus(checked=True, available=True, behind=1))

    with patch("bot.modules.updater.check_for_update", available):
        await cog.check_loop.coro(cog)

    cog.apply_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_loop_auto_applies_when_enabled_and_update_available(db):
    bot = _make_bot(db)
    bot.guilds = [MagicMock(id=GUILD)]
    await bot.stores.config.set(GUILD, "updates.auto_apply", "true")
    cog = _make_cog(bot)
    cog.apply_update = AsyncMock()
    available = AsyncMock(return_value=UpdateStatus(checked=True, available=True, behind=1))

    with patch("bot.modules.updater.check_for_update", available):
        await cog.check_loop.coro(cog)

    cog.apply_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_loop_does_not_apply_when_no_update_available(db):
    bot = _make_bot(db)
    bot.guilds = [MagicMock(id=GUILD)]
    await bot.stores.config.set(GUILD, "updates.auto_apply", "true")
    cog = _make_cog(bot)
    cog.apply_update = AsyncMock()
    up_to_date = AsyncMock(return_value=UpdateStatus(checked=True, available=False))

    with patch("bot.modules.updater.check_for_update", up_to_date):
        await cog.check_loop.coro(cog)

    cog.apply_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_update_closes_bot_then_exits_process(db):
    bot = _make_bot(db)
    bot.close = AsyncMock()
    cog = _make_cog(bot)

    with patch("bot.modules.updater.os._exit") as fake_exit:
        await cog.apply_update()

    bot.close.assert_awaited_once()
    fake_exit.assert_called_once_with(0)


# --- review F4: the loop body must never let an exception escape -----------


@pytest.mark.asyncio
async def test_check_loop_survives_a_failing_update_check(db):
    bot = _make_bot(db)
    bot.guilds = [MagicMock(id=GUILD)]
    cog = _make_cog(bot)
    cog.apply_update = AsyncMock()
    boom = AsyncMock(side_effect=RuntimeError("network exploded"))

    with patch("bot.modules.updater.check_for_update", boom):
        await cog.check_loop.coro(cog)  # must return normally, not propagate

    cog.apply_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_loop_survives_a_failing_config_read(db):
    bot = _make_bot(db)
    bot.guilds = [MagicMock(id=GUILD)]
    cog = _make_cog(bot)
    cog.apply_update = AsyncMock()
    bot.stores.config.get_bool = AsyncMock(side_effect=RuntimeError("db exploded"))
    available = AsyncMock(return_value=UpdateStatus(checked=True, available=True, behind=1))

    with patch("bot.modules.updater.check_for_update", available):
        await cog.check_loop.coro(cog)  # must return normally

    cog.apply_update.assert_not_awaited()


# --- current_status: the live check behind /about --------------------------


@pytest.mark.asyncio
async def test_current_status_fetches_when_there_is_no_cached_check(db):
    cog = _make_cog(_make_bot(db))
    fresh = AsyncMock(return_value=UpdateStatus(checked=True, available=True, behind=1))

    with patch("bot.modules.updater.check_for_update", fresh):
        status = await cog.current_status()

    assert status.available is True
    fresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_status_reuses_a_recent_check(db):
    # A room full of people running /about must not mean a git fetch each.
    cog = _make_cog(_make_bot(db))
    fresh = AsyncMock(return_value=UpdateStatus(checked=True, available=False))

    with patch("bot.modules.updater.check_for_update", fresh):
        await cog.current_status()
        await cog.current_status()

    assert fresh.await_count == 1


@pytest.mark.asyncio
async def test_current_status_re_fetches_once_the_ttl_has_passed(db):
    cog = _make_cog(_make_bot(db))
    fresh = AsyncMock(return_value=UpdateStatus(checked=True, available=False))

    with patch("bot.modules.updater.check_for_update", fresh):
        await cog.current_status()
        cog._checked_at -= updater.STATUS_TTL_SECONDS + 1
        await cog.current_status()

    assert fresh.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_current_status_calls_share_one_fetch(db):
    # The lock is only half of it - the loser must also re-check the TTL
    # inside the lock, or it fetches again the moment the winner releases.
    import asyncio

    cog = _make_cog(_make_bot(db))
    started = 0

    async def slow_check():
        nonlocal started
        started += 1
        await asyncio.sleep(0.01)
        return UpdateStatus(checked=True, available=False)

    with patch("bot.modules.updater.check_for_update", slow_check):
        await asyncio.gather(*(cog.current_status() for _ in range(5)))

    assert started == 1


@pytest.mark.asyncio
async def test_the_background_loop_also_refreshes_the_cache(db):
    # So /about right after an auto-check doesn't pay for a second fetch.
    bot = _make_bot(db)
    bot.guilds = []
    cog = _make_cog(bot)
    fresh = AsyncMock(return_value=UpdateStatus(checked=True, available=False))

    with patch("bot.modules.updater.check_for_update", fresh):
        await cog.check_loop.coro(cog)
        await cog.current_status()

    assert fresh.await_count == 1


# --- head_commit_date ------------------------------------------------------


@pytest.mark.asyncio
async def test_head_commit_date_reads_the_real_repo(monkeypatch):
    monkeypatch.setattr(updater, "_head_date", None)
    monkeypatch.setattr(updater, "_head_date_looked_up", False)

    date = await updater.head_commit_date()

    assert date is not None
    assert len(date) == 10 and date[4] == "-" and date[7] == "-"


@pytest.mark.asyncio
async def test_head_commit_date_is_none_outside_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "REPO_DIR", tmp_path)
    monkeypatch.setattr(updater, "_head_date", None)
    monkeypatch.setattr(updater, "_head_date_looked_up", False)

    assert await updater.head_commit_date() is None


@pytest.mark.asyncio
async def test_head_commit_date_only_shells_out_once(monkeypatch):
    # HEAD can't move under a running process - entrypoint.sh pulls before
    # the interpreter starts - so this is looked up once, not per /about.
    monkeypatch.setattr(updater, "_head_date", None)
    monkeypatch.setattr(updater, "_head_date_looked_up", False)
    run_git = AsyncMock(return_value=(0, "2026-08-13"))
    monkeypatch.setattr(updater, "_run_git", run_git)

    assert await updater.head_commit_date() == "2026-08-13"
    assert await updater.head_commit_date() == "2026-08-13"
    run_git.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_status_falls_back_to_the_last_known_status_on_timeout(db):
    # /setup edits a component interaction without deferring, so a slow fetch
    # has to degrade rather than blow Discord's 3s window.
    import asyncio

    cog = _make_cog(_make_bot(db))
    cog.status = UpdateStatus(checked=True, available=False)

    async def never_finishes():
        await asyncio.sleep(10)
        raise AssertionError("should have been cancelled")

    with patch("bot.modules.updater.check_for_update", never_finishes):
        status = await cog.current_status(timeout=0.01)

    assert status is cog.status


@pytest.mark.asyncio
async def test_a_timed_out_check_leaves_the_lock_usable(db):
    # The cancelled call must unwind cleanly, or every later check deadlocks.
    import asyncio

    cog = _make_cog(_make_bot(db))
    async def slow():
        await asyncio.sleep(10)
        return UpdateStatus(checked=True, available=False)

    with patch("bot.modules.updater.check_for_update", slow):
        await cog.current_status(timeout=0.01)

    fresh = AsyncMock(return_value=UpdateStatus(checked=True, available=True, behind=3))
    with patch("bot.modules.updater.check_for_update", fresh):
        status = await asyncio.wait_for(cog.current_status(), timeout=2)

    assert status.behind == 3
