"""
updater - detects when origin has commits this checkout doesn't have yet,
and (optionally) restarts the process to pick them up.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[2]
CHECK_INTERVAL_MINUTES = 30
AUTO_APPLY_KEY = "updates.auto_apply"


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


class Updater(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.status = UpdateStatus(checked=False)
        self.check_loop.start()

    def cog_unload(self):
        self.check_loop.cancel()

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_loop(self):
        # tasks.loop only auto-restarts on network errors; anything else would stop
        # this loop permanently, so nothing may escape the body. (review F4)
        try:
            self.status = await check_for_update()
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
