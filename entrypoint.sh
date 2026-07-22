#!/bin/bash
# Runs once per container start, before the bot itself. Pulls the latest
# commit from origin so a redeploy is just "restart the container" - see
# the README's "Updates" section and bot/modules/updater.py, which detects
# when a newer commit exists and can trigger this by exiting the process.
#
# Deliberately does not use `set -e`: a failed pull (no network, a
# fast-forward conflict) should not crash-loop the container - it should
# just start with whatever code is already on disk.
set -uo pipefail

cd /app || exit 1
git config --global --add safe.directory /app

if [ -d .git ]; then
    if ! git pull --ff-only; then
        echo "entrypoint.sh: git pull failed - starting with the code already on disk" >&2
    fi
else
    echo "entrypoint.sh: no .git directory - skipping update check" >&2
fi

exec python -m bot
