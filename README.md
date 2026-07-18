# Discord Mod/Utility Bot

A self-hosted, single-server Discord bot combining behavior-based anti-spam,
content-based automod, moderation with case history, role management, and a
set of "personality" features (quotes, tags, buckets, witty replies, a bored
detector, and a markov-chain generator). Built to replace
[Sweetie Bot](https://github.com/ErikMcClure/sweetiebot) (archived, Go)
after it went offline, keeping its one-module-per-feature architecture and
runtime-configurable settings, rebuilt in Python.

## Architecture

Each feature is an independent `discord.py` Cog living in its own file under
`bot/modules/`. The bot core (`bot/core.py`) just loads whichever modules are
listed in `MODULES` - dropping a feature is commenting out one line, no
other code changes.

```
bot/
├── __main__.py       entry point: loads .env, sets up logging, runs the bot
├── core.py            bot class, module loader, DB watchdog, error handler
├── db.py               SQLite connection + schema
├── stores.py           one small data-access class per table
└── modules/
    ├── status.py        ping/uptime/about
    ├── automod.py        word/regex filters, invite blocking, caps spam
    ├── antispam.py        message flooding, duplicate spam, mass mentions
    ├── moderation.py      kick/ban/mute/warn + case history
    ├── logging_module.py  edit/delete + join/leave logging, mod-log
    ├── roles.py           self-assign, reaction roles, auto-role on join
    ├── quote.py           save/recall quotes
    ├── tag.py             custom canned commands
    ├── bucket.py          named random-pick lists
    ├── witty.py           random replies when the bot is @mentioned
    ├── bored.py           nudges a channel after it goes quiet
    ├── markov.py          generates text from recent channel history
    ├── scheduler.py       STUB - Phase 6, see file for design notes
    ├── counters.py        STUB - Phase 6, see file for design notes
    ├── leveling.py         STUB - Phase 6, see file for design notes
    └── minecraft.py        STUB - Phase 6, needs your Crafty API details
```

**Config system.** Every tunable value (spam thresholds, log channel, autorole,
etc.) is a `guild_id + key -> value` row in the `config` table, read through
`bot.stores.config`. Nothing is hardcoded, so tuning a threshold is a database
write, not a code change and redeploy.

**Graceful degradation.** Every store read checks `db.available` first and
returns a safe default (with a logged warning) instead of raising. Writes
still raise, since a failed write (a mod action not being recorded) is
something the calling command needs to know about. A background watchdog
task (`core.py::_db_watchdog`) retries the connection every 30 seconds if
it's down - the bot keeps running and taking live moderation action even
with the database unreachable, it just can't persist config/case history
until it reconnects.

## Feature status

| Phase | Modules | Status |
|---|---|---|
| 1 | Bot skeleton, Docker pipeline | ✅ Done |
| 2 | antispam, automod | ✅ Done |
| 3 | logging_module, moderation | ✅ Done |
| 4 | roles | ✅ Done |
| 5 | quote, tag, bucket, witty, bored, markov | ✅ Done |
| 6 | scheduler, counters, leveling, minecraft | 🚧 Stubbed - see each file's docstring |

29 tests currently pass (`pytest -q`), covering the pure detection logic
(automod/antispam/markov) and the store layer against a real temp SQLite file.

## Local setup

```bash
git clone <your-repo-url>
cd discord-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: paste your bot token from https://discord.com/developers/applications

python -m bot
```

Run the tests any time with:

```bash
pip install pytest pytest-asyncio flake8
pytest -q
flake8 --max-line-length=110 bot/
```

## Discord Developer Portal setup

1. Create an application at https://discord.com/developers/applications
2. Bot tab -> Reset Token -> paste into `.env` as `DISCORD_TOKEN`
3. Under Privileged Gateway Intents, enable **Server Members Intent** and
   **Message Content Intent** (both required - the bot's `intents` in
   `core.py` request them, and Discord will silently refuse to connect
   without them enabled here).
4. OAuth2 -> URL Generator -> scopes: `bot`. Permissions: at minimum
   `Kick Members`, `Ban Members`, `Moderate Members`, `Manage Roles`,
   `Manage Messages`, `Read Message History`. Use the generated URL to
   invite it to your server.

## Deploying to TrueNAS SCALE

1. On the NAS, create a dataset for the bot's data (e.g.
   `/mnt/<pool>/apps/discord-bot/data`), owned by UID/GID 1000 to match the
   container's `botuser`:
   ```bash
   chown -R 1000:1000 /mnt/<pool>/apps/discord-bot/data
   ```
2. Push this repo to GitHub, then either `git clone` it onto the NAS or pull
   it in through your existing Custom App workflow (same pattern you're
   already using for Crafty).
3. Copy `.env.example` to `.env` in the project root and fill in the real
   token. **Don't commit `.env`** - it's already in `.gitignore`.
4. Point `docker-compose.yml`'s volume mount at your dataset path instead of
   the local `./data` default, then bring it up as a Custom App / via
   `docker compose up -d --build`.
5. Check logs (`docker logs discord-mod-bot`) - the bot logs its module
   load results and DB connection status on every startup, which is the
   fastest way to confirm everything came up clean.

## Config reference

Every key below is read/written through `bot.stores.config`, scoped per
guild. Defaults apply if a key was never set.

| Key | Default | Meaning |
|---|---|---|
| `commandprefix` | `!` | Command prefix for this server |
| `spam.max_messages` | 5 | Messages allowed within the rolling window |
| `spam.window_seconds` | 6 | Rolling window size |
| `spam.max_duplicates` | 3 | Identical consecutive messages before action |
| `spam.max_mentions` | 5 | Mentions in one message before it's flagged |
| `spam.timeout_seconds` | 300 | Timeout duration applied on a spam violation |
| `automod.block_invites` | true | Delete messages containing invite links |
| `automod.caps_threshold` | 70 | % caps that triggers a flag (0 disables) |
| `automod.caps_minlen` | 10 | Minimum message length before caps checking applies |
| `logging.channel` | unset | Channel ID all logs post to |
| `logging.edits` / `logging.deletes` / `logging.joins` | true | Toggle each log category |
| `roles.autorole` | unset | Role ID granted automatically on join |
| `bored.channel` | unset | Channel to watch for the bored-nudge |
| `bored.idle_seconds` | 1800 | Silence duration before it fires |
| `bored.message` | generic nudge | Text posted when triggered |

Most of these are set via their module's dedicated command (`!setlogchannel`,
`!setautorole`, `!setboredchannel`, `!filter add`) rather than a raw
`!setconfig` - a generic `!setconfig`/`!getconfig` pair (matching Sweetie
Bot's pattern exactly) is a natural next addition to `bot/modules/status.py`
if you want a single escape hatch for keys that don't have a dedicated
command yet.

## Roadmap / next steps

- Phase 6 modules (`scheduler.py`, `counters.py`, `leveling.py`,
  `minecraft.py`) are stubbed with design notes in each file's docstring -
  each needs one design decision made before it's worth writing real code.
- `minecraft.py` specifically needs Crafty Controller API details (or a
  decision to use the simpler `mcstatus`-only route) before it can query
  your actual SkyFactory 3 server.
- A generic `!setconfig <key> <value>` / `!getconfig <key>` pair would round
  out the Sweetie Bot config parity and is a good next commit.
