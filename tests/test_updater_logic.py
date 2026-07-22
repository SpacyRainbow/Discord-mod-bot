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
