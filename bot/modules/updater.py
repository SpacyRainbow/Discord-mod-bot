"""
updater - detects when origin has commits this checkout doesn't have yet,
and (optionally) restarts the process to pick them up.

Two callers, different freshness needs: the background loop runs every
CHECK_INTERVAL_MINUTES and exists to drive auto-apply unattended, while
/about calls current_status() and gets a live check (behind a short TTL).
Serving /about from the loop's cached value made it report "Up to date"
for up to half an hour after a push, which is the opposite of what someone
who just asked is looking for.

Applying an update deliberately does not mean "pull inside the running
process" - a running Python program can't safely replace its own already-
imported modules out from under itself. Instead, `entrypoint.sh` runs
`git pull` once, before `python -m bot` starts (see the Dockerfile) -
"applying an update" here just means exiting so the container's restart
policy relaunches it, which re-runs that entrypoint against whatever is
now on origin.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[2]
CHECK_INTERVAL_MINUTES = 30
AUTO_APPLY_KEY = "updates.auto_apply"

# How long a status may be reused to answer an interactive command. The
# 30-minute loop exists to drive auto-apply unattended; someone typing /about
# wants to know about a commit pushed a minute ago, not one from the last
# sweep. Short enough to be "now", long enough that a room full of people
# running /about doesn't mean a git fetch each.
STATUS_TTL_SECONDS = 60

# Budget for a check made inside an interaction that hasn't been deferred and
# so must be answered within Discord's 3s window. Leaves room for the rest of
# the embed build and the HTTP round trip back.
INTERACTIVE_FETCH_TIMEOUT_SECONDS = 2.0


@dataclass
class UpdateStatus:
    checked: bool
    available: bool = False
    behind: Optional[int] = None
    latest_summary: Optional[str] = None


def describe_status(status: UpdateStatus) -> str:
    """Pure: the /about and /setup display line for a given status."""
    if not status.checked:
        return "Unable to check (not a git checkout, or the last fetch failed)"
    if not status.available:
        return "Up to date"
    behind_text = f"{status.behind} commit(s) behind" if status.behind is not None else "Update available"
    if status.latest_summary:
        return f"{behind_text} - latest: {status.latest_summary}"
    return behind_text


async def _run_git(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(REPO_DIR),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace").strip()


async def check_for_update() -> UpdateStatus:
    """Fetches from origin and compares HEAD to the upstream branch.
    Inert (checked=False) rather than raising if this isn't a git
    checkout, there's no origin remote tracking a real branch, or the
    fetch itself fails (no network, DNS, etc) - this runs unattended on a
    timer, so a transient failure should be silent, not crash the loop."""
    if not (REPO_DIR / ".git").exists():
        return UpdateStatus(checked=False)

    code, _ = await _run_git("fetch", "--quiet")
    if code != 0:
        return UpdateStatus(checked=False)

    code, branch = await _run_git("rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or not branch or branch == "HEAD":
        return UpdateStatus(checked=False)

    code, local_sha = await _run_git("rev-parse", "HEAD")
    if code != 0:
        return UpdateStatus(checked=False)

    code, remote_sha = await _run_git("rev-parse", f"origin/{branch}")
    if code != 0:
        return UpdateStatus(checked=False)

    if local_sha == remote_sha:
        return UpdateStatus(checked=True, available=False)

    code, count_text = await _run_git("rev-list", "--count", f"HEAD..origin/{branch}")
    behind = int(count_text) if code == 0 and count_text.isdigit() else None

    code, subject = await _run_git("log", "-1", "--format=%s", f"origin/{branch}")
    latest_summary = subject if code == 0 and subject else None

    return UpdateStatus(checked=True, available=True, behind=behind, latest_summary=latest_summary)


_head_date: Optional[str] = None
_head_date_looked_up = False


async def head_commit_date() -> Optional[str]:
    """The date of the commit this process is running, or None if that can't
    be determined. Memoized: `git pull` happens in entrypoint.sh before the
    interpreter starts, so HEAD cannot move underneath a running process."""
    global _head_date, _head_date_looked_up
    if _head_date_looked_up:
        return _head_date
    _head_date_looked_up = True
    if (REPO_DIR / ".git").exists():
        code, out = await _run_git("log", "-1", "--format=%cs")
        if code == 0 and out:
            _head_date = out
    return _head_date


class Updater(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.status = UpdateStatus(checked=False)
        self._checked_at: Optional[float] = None
        self._check_lock = asyncio.Lock()
        self.check_loop.start()

    def cog_unload(self):
        self.check_loop.cancel()

    async def _refresh(self) -> UpdateStatus:
        self.status = await check_for_update()
        self._checked_at = time.monotonic()
        return self.status

    async def _locked_status(self) -> UpdateStatus:
        """Held under a lock so two people asking at once share one fetch
        rather than racing two, and re-checks the TTL inside it so the loser
        of that race returns the winner's result instead of fetching again."""
        async with self._check_lock:
            if self._checked_at is not None and time.monotonic() - self._checked_at < STATUS_TTL_SECONDS:
                return self.status
            return await self._refresh()

    async def current_status(self, timeout: Optional[float] = None) -> UpdateStatus:
        """Fresh status for an interactive command.

        `timeout` is for callers that must answer Discord inside a deadline
        they can't extend - /setup's wizard edits a component interaction
        without deferring, and a slow `git fetch` there would break the
        navigation entirely. Falling back to the last known status is a
        strictly better failure than a dead button. Callers that have
        deferred (/about) pass nothing and wait for the real answer."""
        try:
            return await asyncio.wait_for(self._locked_status(), timeout)
        except TimeoutError:
            logger.warning("Update check exceeded %ss - serving the last known status", timeout)
            return self.status

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_loop(self):
        # tasks.loop only auto-restarts on network errors; anything else would stop
        # this loop permanently, so nothing may escape the body. (review F4)
        try:
            async with self._check_lock:
                await self._refresh()
            if not self.status.available:
                return
            for guild in self.bot.guilds:
                if await self.bot.stores.config.get_bool(guild.id, AUTO_APPLY_KEY, False):
                    logger.info("Auto-update enabled (guild %s) - restarting to apply it", guild.id)
                    await self.apply_update()
                    return
        except Exception:
            logger.exception("updater check_loop iteration failed")

    @check_loop.before_loop
    async def before_check_loop(self):
        await self.bot.wait_until_ready()

    async def apply_update(self) -> None:
        """Exits the process - the container's restart policy relaunches
        it, and entrypoint.sh's `git pull` picks up whatever's new."""
        logger.info("Applying update - exiting so the restart policy relaunches with the new code")
        await self.bot.close()
        # os._exit skips interpreter cleanup, so the SQLite connection has to be
        # closed by hand or the next start has a -journal/-wal to recover.
        # db.close() is idempotent and never raises out (review F2/F19).
        with contextlib.suppress(Exception):
            await self.bot.db.close()
        # Deliberately os._exit and NOT sys.exit: apply_update is called from
        # inside a command callback and an interaction button, where discord.py
        # catches SystemExit along with everything else - the process would log
        # an error and keep running instead of restarting. (review F19)
        os._exit(0)


async def setup(bot: commands.Bot):
    await bot.add_cog(Updater(bot))
