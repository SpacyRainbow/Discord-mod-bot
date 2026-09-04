# Discord Mod/Utility Bot

This is a Discord bot. You run this bot yourself, on your own computer or
server. This process has the name self-hosting. The bot works with one
Discord server at a time.

The bot has these functions:

- Anti-spam control, based on member behavior.
- Automod, based on message content.
- Moderation commands, with a case history: kick, ban (with an optional
  temp-ban duration), unban, mute, warn, purge, slowmode, and lockdown.
- Raid protection: a minimum account-age gate, join-burst detection, and a
  manual raid-mode toggle.
- Anti-nuke: detects one member mass-deleting channels/roles or
  mass-banning members, and strips their dangerous permissions.
- Role management.
- A starboard, giveaways, polls, a ticket system, and welcome/leave
  messages.
- Reminders and other scheduled, one-off tasks.
- Music playback from YouTube and other sources.
- Personality functions: quotes, tags, buckets, witty replies, a bored
  detector, and a text generator.
- Minecraft server status and control, through Crafty Controller 4.
- A link embed fixer: posts a working link when someone shares an X,
  TikTok, or Instagram link that Discord will not embed.

This project replaces [Sweetie Bot](https://github.com/ErikMcClure/sweetiebot).
Sweetie Bot was a bot written in the Go language. The developer archived the
Sweetie Bot project. This project uses the same design as Sweetie Bot: one
module for each function, and settings that you can change while the bot
runs. This project uses the Python language.

Every command works in two ways. You can type the command with a `!`
prefix. You can also use the command as a Discord `/slash` command. The bot
uses a discord.py function named hybrid commands for this. This function
did not remove any command. The slash commands are an addition only.

## Architecture

Each function is a separate module. A module is one Python file. All
modules are in the folder `bot/modules/`. The file `bot/core.py` loads the
modules in the list named `MODULES`. To remove a function, remove or
comment out its line in this list. No other code change is necessary.

```
bot/
├── __main__.py       entry point: loads .env, sets up logging, starts the bot
├── core.py            bot class; loads modules; checks the database
│                       connection; handles errors; syncs slash commands
├── db.py               database connection and schema
├── stores.py           one data-access class for each database table
├── durations.py         shared "10m"/"2h"/"1d" duration parsing
└── modules/
    ├── status.py        ping, uptime, about, help, setconfig, getconfig
    ├── automod.py        word filter, invite block, capital-letter check
    ├── antispam.py        message flooding, repeated messages, mass mentions
    ├── moderation.py      kick, ban/tempban, unban, mute, warn, purge,
    │                       slowmode, lockdown, case history, Mute role
    ├── logging_module.py  message edit/delete logs, join/leave logs, mod log
    ├── raid.py            join-age gate, join-burst detection, /raidmode
    ├── antinuke.py        mass channel/role-delete or ban-burst detection
    ├── roles.py           self-assign roles, reaction roles, auto-role
    ├── quote.py           save and show quotes
    ├── tag.py             custom text commands
    ├── bucket.py          named random-pick lists
    ├── witty.py           random reply when a member mentions the bot
    ├── bored.py           sends a message after a channel is quiet
    ├── markov.py          makes text from recent channel messages
    ├── music.py           YouTube playback; SponsorBlock auto-skip
    ├── setup.py           setup wizard for server settings
    ├── minecraft.py       /mcstatus: status + button/modal controls (Crafty Controller 4)
    ├── scheduler.py       generic "run this later" engine; /remind
    ├── starboard.py       star-reaction board
    ├── giveaway.py        /giveaway start/end/reroll
    ├── poll.py            /poll with button voting
    ├── tickets.py         /ticketpanel support-ticket channels
    ├── greetings.py       welcome/leave messages
    ├── embedfix.py        rewrites links Discord will not embed; see below
    ├── updater.py          detects/applies updates from GitHub; see "Updates"
    ├── counters.py        not complete; see the file for design notes
    └── leveling.py         not complete; see the file for design notes
```

`entrypoint.sh`, at the repo root, runs `git pull` before starting the bot
on every container start - this is what makes the update mechanism in
"Updates" below work.

**Settings system.** The bot stores all settings in the database. Examples
of settings: spam thresholds, the log channel, and the auto-role. Each
setting is one row in the `config` table. Each row has a server ID, a key,
and a value. The code in `bot/stores.py` reads and writes these rows
through the object `bot.stores.config`. No setting is fixed in the code. To
change a setting, write a new value to the database. This does not need a
code change or a restart with new code.

**The bot continues to run if the database fails.** Before a read
operation, the code checks the connection through the variable
`db.available`. If the connection has failed, the read operation returns a
safe default value and writes a warning to the log, instead of failing. A
write operation is different: if the connection has failed, a write
operation fails and reports an error. This is necessary, because a
moderator must know if the bot did not record an action. A background task
checks the database connection every 30 seconds. This task is the function
`_db_watchdog` in `core.py`. If the connection has failed, this task tries
to reconnect. While the connection has failed, the bot continues to run and
can still take live moderation action. But the bot cannot save settings or
case history until the connection is available again.

## Feature status

| Phase | Modules | Status |
|---|---|---|
| 1 | Bot skeleton, Docker pipeline | Done |
| 2 | antispam, automod | Done |
| 3 | logging_module, moderation | Done |
| 4 | roles | Done |
| 5 | quote, tag, bucket, witty, bored, markov | Done |
| 6 | counters, leveling | Not complete - see the notes in each file (scheduler is now done - see Phase 15) |
| 7 | Hybrid `/slash` commands, music (yt-dlp and SponsorBlock) | Done |
| 8 | Antispam bulk-delete fix, role-based mute, `/setup` wizard, `/help` | Done |
| 9 | Minecraft status via Crafty Controller 4 | Done |
| 10 | `/mcstatus` button/modal control (start/stop/restart, console, whitelist), guild-restricted | Done |
| 11 | Purge, tempban, unban, slowmode, lockdown, case edit/delete | Done |
| 12 | Raid protection (account-age gate, join-burst detection, `/raidmode`) | Done |
| 13 | Anti-nuke (mass channel/role-delete or ban-burst detection) | Done |
| 14 | Starboard, giveaways, polls, tickets, welcome/leave messages | Done |
| 15 | Scheduler engine and `/remind` (the rest of Phase 6 - `counters`, `leveling` - is still not complete) | Done |
| 16 | Music volume, loop, and a dedicated `/shuffle` command | Done |
| 17 | Update detection and one-click/auto-apply updates from GitHub | Done |
| 18 | Link embed fixer (X/Twitter, TikTok, Instagram, Reddit, Bluesky, Pixiv, Twitch) | Done |

Automated tests exist for this bot, and all of them pass. Run the tests
with the command `pytest -q`. The tests check three parts of the bot: the
detection logic in `automod.py`, `antispam.py`, `markov.py`, and `music.py`
(this logic does not depend on Discord); the moderation, status,
minecraft, raid, anti-nuke, starboard, giveaway, poll, ticket, greetings,
and scheduler commands (these tests use mock Discord objects, and a
temporary SQLite database file where a database is needed); and the data
storage layer in `stores.py` (these tests also use a temporary SQLite
database file).

## Commands

Each command in the table below works in two ways: as a `!prefix` command,
and as a `/slash` command. Type `/help` in Discord at any time. This
command shows the same list, and a link back to this file. A command
marked **mod** needs one of these permissions: Manage Guild, Manage Roles,
Kick Members, Ban Members, or Moderate Members.

| Command | Module | What it does |
|---|---|---|
| `ping`, `uptime`, `about` | status | Shows the health and the status of the bot |
| `help` | status | Shows a list of every command, with a link back to this file |
| `setconfig <key> <value>` **mod** | status | Sets the value for any setting key. See "Settings reference" |
| `getconfig <key>` **mod** | status | Shows the current value for a setting key |
| `setup` **mod** | setup | Starts the setup wizard. You can run this command again at any time. See "Setup wizard" |
| `filter add/remove/list` **mod** | automod | Adds, removes, or shows words in the banned-word list |
| `kick <member> [reason]` **mod** | moderation | Removes a member from the server; adds a case history record |
| `ban <member> [duration] [reason]` **mod** | moderation | Bans a member; give a duration (e.g. `7d`) for a temp-ban, or omit it for permanent |
| `unban <user> [reason]` **mod** | moderation | Lifts a ban; adds a case history record |
| `mute <member> [duration] [reason]` **mod** | moderation | Mutes a member. See "Moderation" for the duration rules |
| `unmute <member>` **mod** | moderation | Removes a mute: the timeout, the Mute role, and any scheduled end time |
| `warn <member> [reason]` **mod** | moderation | Sends a warning; sends a direct message; adds a case history record |
| `cases <member>` **mod** | moderation | Shows the case history for a member |
| `case <id>` **mod** | moderation | Shows one case by its ID |
| `editcase <id> <reason>` **mod** | moderation | Changes a case's recorded reason |
| `deletecase <id>` **mod** | moderation | Deletes a case from the history |
| `purge <amount> [member]` **mod** | moderation | Deletes the last 1-100 messages in the channel, optionally only one member's |
| `slowmode <seconds> [channel]` **mod** | moderation | Sets a channel's slowmode delay, 0-21600 seconds |
| `lockdown [channel]` **mod** | moderation | Toggles blocking @everyone from sending messages in a channel |
| `raidmode <on\|off>` **mod** | raid | Toggles a temporary verification-level lockdown |
| `setlogchannel <channel>` **mod** | logging_module | Sets the channel for edit, delete, join, leave, and mod-log messages |
| `setautorole <role>` **mod** | roles | Sets the role that the bot gives automatically on join |
| `allowrole`/`disallowrole <role>` **mod** | roles | Adds or removes a role from the self-assign list |
| `iam`/`iamnot <role>` | roles | Gives or removes a self-assign role for yourself |
| `reactionrole <message_id> <emoji> <role>` **mod** | roles | Connects an emoji reaction on a message to a role grant |
| `addquote <author> <text>`, `quote [id]` | quote | Saves a quote; shows a saved quote |
| `tag`, `tagset`, `tagdelete`, `tags` | tag | Manages custom text responses |
| `bucketadd`, `bucket`, `buckets` | bucket | Manages named lists; picks a random item from a list |
| `wittyadd <response>` **mod** | witty | Adds a possible reply for when a member mentions the bot |
| `setboredchannel <channel>` **mod** | bored | Sets the channel for the nudge message after quiet time |
| `markov` | markov | Makes new text from recent messages in the channel |
| `play`, `skip`, `pause`, `resume`, `rewind`, `forward`, `stop`, `queue`, `nowplaying`, `leave` | music | Controls music playback. See "Music" |
| `volume <0-200>` | music | Sets playback volume as a percent |
| `loop <off\|track\|queue>` | music | Repeats the current track, the whole queue, or neither |
| `shuffle` | music | Shuffles the current queue |
| `mcstatus [server]` | minecraft | Shows Minecraft server status; mods get Start/Stop/Restart/Console/Whitelist buttons. See "Minecraft (Crafty4)" |
| `remind <duration> <text>` | scheduler | DMs you a reminder after a delay, e.g. `10m`, `2h`, `1d` |
| `setstarboard <channel> [threshold]` **mod** | starboard | Sets the starboard channel and the star count needed to post, default 3 |
| `giveaway start/end/reroll` **mod** | giveaway | Starts, ends early, or rerolls the winner of a giveaway. See "Giveaways" |
| `poll <question> <option1> <option2> ... [duration]` | poll | Starts a button-voted poll with 2-5 options. See "Polls" |
| `pollclose <message_id>` **mod** | poll | Closes a poll early |
| `ticketpanel [message]` **mod** | tickets | Posts a button members click to open a support ticket. See "Tickets" |

The anti-spam function, and part of the automod function, have no commands
of their own. Anti-spam checks for message flooding. Automod checks for
capital letters and invite links. These checks are always active. Change
their settings with the `setconfig` command or the `setup` wizard - see
"Settings reference".

## Local setup

```bash
git clone https://github.com/SpacyRainbow/Discord-mod-bot.git
cd Discord-mod-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Open the file .env. Add your bot token. Add your server ID as GUILD_ID.
# See "Discord Developer Portal setup" below.

python -m bot
```

Music playback needs the program `ffmpeg`. The Python package alone is not
enough. Install `ffmpeg` with your operating system package manager, for
example `apt install ffmpeg` on Debian and Ubuntu, or `dnf install ffmpeg`
on Fedora. The Docker image already includes `ffmpeg`. You only need this
step if you run the bot outside Docker.

Run the tests at any time with these commands:

```bash
pip install pytest pytest-asyncio flake8
pytest -q
flake8 --max-line-length=110 bot/
```

## Discord Developer Portal setup

1. Go to https://discord.com/developers/applications. Create a new
   application.
2. Open the Bot tab. Select "Reset Token". Copy the token. Paste the token
   into the file `.env` as `DISCORD_TOKEN`.
3. Find the section "Privileged Gateway Intents". Enable "Server Members
   Intent". Enable "Message Content Intent". Both intents are necessary.
   The file `core.py` requests both intents in the code. If you do not
   enable both intents here, Discord refuses the connection, without an
   error message.
4. Open the OAuth2 tab. Open the URL Generator. Select these two scopes:
   `bot` and `applications.commands`. The scope `applications.commands` is
   necessary for `/slash` commands - without it, the bot cannot show any.
   Select these permissions, as a minimum: Kick Members, Ban Members,
   Moderate Members, Manage Roles, Manage Messages, Read Message History,
   Connect, and Speak (Connect and Speak are for music). Use the generated
   URL to add the bot to your server.
5. Enable Developer Mode in Discord (User Settings, under Advanced).
   Right-click your server icon. Select "Copy Server ID". Paste this ID as
   `GUILD_ID` in `.env`. This step makes `/slash` commands appear
   immediately, but only on this one server. Without this step, `/slash`
   commands still work, but a global update can take up to one hour after
   each restart.

## Deploying to TrueNAS SCALE

1. On the NAS, create a dataset for the bot's data, for example
   `/mnt/<pool>/apps/discord-bot/data`. Set the owner to UID 1000 and GID
   1000. This matches the user `botuser` in the container:
   ```bash
   chown -R 1000:1000 /mnt/<pool>/apps/discord-bot/data
   ```
2. Push this repository to GitHub. Then, on the NAS, either use `git clone`
   to copy the repository, or use your existing Custom App process (the
   same process you use for Crafty).
3. Copy `.env.example` to a new file named `.env`, in the project root. Add
   the real token. Do not commit `.env` to Git - `.gitignore` already
   excludes it.
4. In `docker-compose.yml`, change the volume mount to your dataset path
   instead of the default `./data`. Then start the bot as a Custom App, or
   with `docker compose up -d --build`.
5. Check the logs with `docker logs discord-mod-bot`. At each startup, the
   bot writes its module load results and its database connection status to
   the log. This is the fastest way to confirm a clean startup.

The image bakes in a real git checkout (see the `COPY . .` in `Dockerfile`),
so from this point on, restarting the container (`docker restart
discord-mod-bot`, or a Custom App restart) is all it takes to pick up a new
version pushed to GitHub - see "Updates" below for how the bot detects and
can trigger this itself.

## Settings reference

The code reads and writes each key below through the object
`bot.stores.config`, in the database table named `config`. Each key applies
to one server only. If a key has no stored value, the bot uses the default.

| Key | Default | Meaning |
|---|---|---|
| `commandprefix` | `!` | The command prefix for this server |
| `spam.max_messages` | 5 | The number of messages allowed in the time window |
| `spam.window_seconds` | 6 | The length of the time window, in seconds |
| `spam.max_duplicates` | 3 | The number of identical messages in a row before action |
| `spam.max_mentions` | 5 | The number of mentions in one message before it is flagged |
| `spam.timeout_seconds` | 300 | The length of the timeout for a spam violation, in seconds |
| `automod.block_invites` | true | If true, deletes messages that contain invite links |
| `automod.caps_threshold` | 70 | The percent of capital letters that triggers a flag (0 turns this off) |
| `automod.caps_minlen` | 10 | The minimum message length for the capital-letters check |
| `logging.channel` | unset | The ID of the channel for all log messages |
| `logging.edits` / `logging.deletes` / `logging.joins` | true | Turns on or off each type of log message |
| `roles.autorole` | unset | The ID of the role given automatically on join |
| `moderation.mute_role` | unset | The ID of the Mute role (see "Moderation") |
| `bored.channel` | unset | The channel checked for the bored-nudge function |
| `bored.idle_seconds` | 1800 | The length of silence, in seconds, before the nudge |
| `bored.message` | generic nudge | The message the bot sends for the nudge |
| `music.sponsorblock_enabled` | true | If true, uses SponsorBlock data to skip non-music segments |
| `raid.min_account_age_hours` | 0 (off) | Kicks a new member if their account is younger than this |
| `raid.join_threshold` | 0 (off) | Joins within `raid.join_window_seconds` that count as a raid burst |
| `raid.join_window_seconds` | 30 | The rolling window size for join-burst detection |
| `raid.auto_lockdown` | false | If true, a detected join burst raises the verification level automatically |
| `antinuke.enabled` | false | Turns on anti-nuke detection. Off by default - see "Anti-nuke" |
| `antinuke.action_threshold` | 3 | Destructive actions (channel/role delete, ban) within the window before punishing |
| `antinuke.window_seconds` | 30 | The rolling window size for anti-nuke detection |
| `starboard.channel` | unset | The channel starred messages are posted to |
| `starboard.threshold` | 3 | The number of stars needed before a message is posted |
| `tickets.category_id` | unset | The category new ticket channels are created under |
| `welcome.channel_id` | unset | The channel welcome messages are posted to |
| `welcome.message` | generic welcome | The welcome message template - see "Welcome and leave messages" |
| `leave.channel_id` | unset | The channel leave messages are posted to |
| `leave.message` | generic leave message | The leave message template |
| `updates.auto_apply` | false | If true, the bot restarts itself automatically when it detects a newer commit on GitHub - see "Updates" |
| `embedfix.enabled` | true | If true, replies with a working link when someone posts one Discord will not embed - see "Link embed fixer" |
| `embedfix.suppress_original` | true | If true, also hides the original message's empty embed. Needs Manage Messages |
| `embedfix.remove_seconds` | 120 | How long the poster has to undo a fix, in seconds. The ❌ is removed when the window closes; `0` means no ❌ at all. Moderators have no time limit |
| `embedfix.platform.<name>` | true | One key per supported site: `twitter`, `tiktok`, `instagram`, `reddit`, `bluesky`, `pixiv`, `twitch` |
| `llm.enabled` | false | Whether an @mention of the bot gets an LLM reply. Off means pings are ignored, and the witty-line responder keeps the mention instead |
| `llm.persona` | (built-in) | The bot's voice and personality. This is the *editable half* of the system prompt only — the safety preamble above it lives in code and cannot be changed from Discord. Edit it with `/setpersona`, which opens a form: the default text is about 3,500 characters and a Discord message stops at 2,000 |
| `llm.maxtokens` | 200 | Longest reply, in tokens (32–600). The single biggest lever on how long a reply takes |
| `llm.cooldown` | 60 | Seconds each user must wait between pings (0–3600; `0` disables the cooldown) |
| `llm.channels` | (all) | Comma-separated channel IDs the bot will answer in. Empty means every channel |
| `llm.timezone` | America/New_York | The IANA timezone the bot is told it is in. Set deliberately: the container has no `TZ`, so anything that trusted the system clock would report UTC as local time |
| `llm.memoryturns` | 2 | How many previous exchanges in the same channel the bot is reminded of (0–5). `0` turns short-term memory off |
| `llm.memoryminutes` | 30 | How old a remembered exchange may be before it is ignored (1–1440) |
| `llm.logdays` | 30 | How long exchanges stay in the log that `/llmlog` reads (1–365) |

Some settings have their own command: `setlogchannel`, `setautorole`,
`setboredchannel`, `setstarboard`, and `filter add`. You can change every
other setting with `setconfig <key> <value>` and `getconfig <key>` (both
mod-only). The `/setup` wizard shows and changes all settings, in a
guided series of steps, and is the easiest way to configure a server. See
the next section.

## Setup wizard (`/setup`)

The command `/setup` starts a wizard. This command needs the Manage Guild
permission. You can run this command again at any time.

The wizard saves each change to the database immediately, not only at the
end. If you stop the wizard before the last step, your changes stay saved.
The wizard also stops automatically after 5 minutes with no activity, and
your changes still stay saved.

Only the member who started the wizard can use its buttons and menus. If a
different member tries, the bot replies that the wizard is not theirs.

The wizard uses one message. The bot edits this same message at each step.
Every step has a Back button and a Next button. The last step has a Finish
button instead of Next. The Finish button locks the message: after this,
the buttons and menus no longer work.

| Step | Title | Content | Setting keys |
|---|---|---|---|
| 1/16 | General | A button, "Edit prefix", opens a form with one field: the command prefix | `commandprefix` |
| 2/16 | Logging | A menu selects the log channel. Three on/off buttons: "Log edits", "Log deletes", "Log joins/leaves" | `logging.channel`, `logging.edits`, `logging.deletes`, `logging.joins` |
| 3/16 | Moderation | No button or menu. The bot creates the Mute role if it does not exist, or confirms the role if it does, and shows its name. Run `/setup` again to re-apply the role's channel permissions everywhere, for example after adding a new channel | `moderation.mute_role` |
| 4/16 | Anti-spam | A button, "Edit thresholds", opens a form with 5 fields: max messages per window, window length, max duplicate messages, max mentions, and timeout duration. All five must be whole numbers | `spam.max_messages`, `spam.window_seconds`, `spam.max_duplicates`, `spam.max_mentions`, `spam.timeout_seconds` |
| 5/16 | Automod | An on/off button, "Block invites". A separate button, "Edit caps thresholds", opens a form with 2 fields: the caps percent threshold, and the minimum message length for the caps check. This step does not include the banned-word list - that list is a set of words, not one value, and is managed instead with `filter add`, `filter remove`, and `filter` (list) | `automod.block_invites`, `automod.caps_threshold`, `automod.caps_minlen` |
| 6/16 | Raid protection | An on/off button, "Auto-lockdown on burst". A button, "Edit raid thresholds", opens a form with 3 fields: minimum account age in hours, join burst size, and the burst window in seconds. Both checks are 0 (off) by default | `raid.min_account_age_hours`, `raid.join_threshold`, `raid.join_window_seconds`, `raid.auto_lockdown` |
| 7/16 | Anti-nuke | An on/off button, "Anti-nuke enabled" (off by default). A button, "Edit anti-nuke thresholds", opens a form with 2 fields: the number of destructive actions before punishing, and the window in seconds | `antinuke.enabled`, `antinuke.action_threshold`, `antinuke.window_seconds` |
| 8/16 | Roles | A menu selects the auto-role for new members | `roles.autorole` |
| 9/16 | Bored detector | A menu selects the nudge channel. A button, "Edit bored settings", opens a form with 2 fields: idle seconds before the nudge, and the nudge message | `bored.channel`, `bored.idle_seconds`, `bored.message` |
| 10/16 | Starboard | A menu selects the starboard channel. A button, "Edit star threshold", opens a form with one field: the number of stars needed to post | `starboard.channel`, `starboard.threshold` |
| 11/16 | Tickets | A menu selects the category new ticket channels are created under | `tickets.category_id` |
| 12/16 | Welcome/leave messages | Two menus select the welcome channel and the leave channel. A button, "Edit welcome/leave text", opens a form with 2 fields: the welcome message template and the leave message template | `welcome.channel_id`, `welcome.message`, `leave.channel_id`, `leave.message` |
| 13/16 | Music | An on/off button, "SponsorBlock auto-skip" | `music.sponsorblock_enabled` |
| 14/16 | Link embed fixer | Two on/off buttons, "Fix links" and "Hide original". A menu selects which sites to fix - unselected sites are off. A button, "Edit undo window", opens a form with one field: how many seconds the poster has to undo a fix. See "Link embed fixer" | `embedfix.enabled`, `embedfix.suppress_original`, `embedfix.remove_seconds`, `embedfix.platform.<name>` |
| 15/16 | Updates | An on/off button, "Auto-update", plus a live status line showing whether an update is currently detected. See "Updates" | `updates.auto_apply` |
| 16/16 | Summary | A read-only summary of every setting above, its current value or default, and one line for Spotify that shows only "Configured" or "Not configured" - this step never shows or accepts the real Spotify credentials | None - display only |

**Clear on/off status, and a default always shown.** Every setting that
can genuinely be turned on or off - block invites, both raid checks,
anti-lockdown, anti-nuke, autorole, the bored/starboard/welcome/leave
channels, SponsorBlock - shows as a plain-language 🟢 Enabled / 🔴 Disabled
line, not a raw `true`/`false`. Each step's embed color follows the same
rule: green if everything in that step is on, red if everything is off,
and blurple (the bot's neutral color) if the step has no on/off setting
at all, or a mix of both. Every numeric field also always shows what it
would reset to - `45 (default: 30)` if you've changed it, `30 (default)`
if you haven't - both in the step's embed and in the modal's field
labels, so you never have to leave the wizard to check what "normal" is.

**Reset to defaults.** Every step except Moderation and the Summary has
its own "Reset to defaults" button, restoring only that step's settings -
a channel setting resets to unset (not a placeholder channel), everything
else resets to its literal default value.

**Why Spotify is never an editable field here.** `SPOTIFY_CLIENT_ID` and
`SPOTIFY_CLIENT_SECRET` are secret values that apply to the whole bot, on
every server, not to one server. A Discord command is not a safe place for
these values, for three reasons: Discord keeps a history of each command,
so a secret typed into a command would stay in that history; a command
run on one server would change the value for every server; and the bot
would need to store the secret in its own database, an extra risk. For
these reasons, set the Spotify credentials one time, in `.env` - see "Local
setup" above. After this, every server shows only the word "Configured".

## Anti-spam

The anti-spam function checks member behavior for three problems: message
flooding, repeated messages, and mass mentions. See "Settings reference"
for the setting keys.

This function had a bug. When the bot detected a violation, the bot
deleted only the messages sent after the threshold was crossed. The bot
left the first messages of the same burst untouched. This bug is now
fixed.

The Sweetie Bot spam module has similar logic, in the file
`spammodule/SpamModule.go`. When Sweetie Bot detects a violation, Sweetie
Bot bulk-deletes the whole recent burst from that member. This bot now
uses the same method.

The bulk-delete step works one channel at a time. This is necessary
because the Discord bulk-delete API also works one channel at a time.

The bot uses one lock for each member. This lock stops a fast burst of
messages from causing the bot to process the same violation two times.

## Moderation

The commands `kick`, `ban`, and `warn` are simple commands. Each command
adds a record to the case history.

The `mute` command uses one of two methods. The method depends on the
duration.

**Method 1: Discord timeout.** The bot uses this method for a duration of
28 days or less. Discord shows this status as "Timed Out" in its own
interface. This method needs no setup.

**Method 2: Mute role.** The bot uses this method in two cases: a duration
of more than 28 days, or an indefinite mute. A mute is indefinite if you
give no duration. A mute is also indefinite if you type `perm`,
`permanent`, or `indefinite`. Indefinite is the default action when you
give no duration.

The bot creates the Mute role automatically, the first time the bot needs
this role. You can also create this role with `/setup`. The Mute role
blocks these actions in every channel, including channels created
afterward: sending messages, adding reactions, and starting threads.

A Discord native timeout has a hard limit of 28 days. Discord does not
allow a longer timeout. This is the reason for the Mute role method.

An indefinite mute ends only when a moderator uses the `unmute` command. A
mute longer than 28 days ends on its own. A background task checks for
this every 60 seconds.

The `unmute` command always clears every method at the same time: the
timeout, the Mute role, and any scheduled end time. A moderator does not
need to know which method was active.

**Temp-ban.** Give `ban` a duration, for example `/ban @user 7d spamming`,
for a temporary ban. The bot schedules an automatic unban through the same
scheduler engine `/remind` uses (see "Scheduler and reminders"). Omit the
duration for a normal, permanent ban. If the duration is not understood,
the ban still happens - the bot just reports that it is permanent, rather
than silently discarding the ban. `unban` lifts a ban early, or lifts one
that was never temporary, and works with a user ID or a `@mention` even
for someone who already left - it does not need a current member. Known
limitation: re-banning someone who already has a pending temp-ban
schedules a second, independent auto-unban rather than replacing the
first - rare in practice, but worth knowing about.

**Purge.** `/purge <amount> [member]` deletes the last 1-100 messages in
the channel. Give a member to delete only that member's messages within
that same range, instead of every message. This uses Discord's bulk-delete
API, the same one anti-spam's automatic cleanup uses - it deletes messages
older than 14 days one at a time instead, automatically.

**Slowmode and lockdown.** `/slowmode <seconds> [channel]` sets a
channel's slowmode delay directly, 0 to turn it off. `/lockdown [channel]`
is a toggle: the first use blocks @everyone from sending messages in that
channel, remembering whatever the permission was set to before; using it
again restores that exact previous value, rather than always turning
sending back on (a channel that was already restricted to certain roles
goes back to being restricted to those roles, not thrown open to
everyone).

**Editing case history.** `/editcase <id> <reason>` changes a case's
recorded reason - useful for correcting a typo, or adding detail after the
fact. `/deletecase <id>` removes a case from the history entirely.

## Raid protection

Two automatic checks, both off by default (this bot's usual "fork-friendly,
does nothing until configured" default), plus one manual command:

- **Minimum account age** (`raid.min_account_age_hours`): if set above 0,
  a new member whose account is younger than this many hours is kicked
  automatically, with a note in the mod-log channel.
- **Join-burst detection** (`raid.join_threshold` /
  `raid.join_window_seconds`): if this many members join within the
  window, the bot posts an alert to the mod-log channel. If
  `raid.auto_lockdown` is also true, the bot raises the server's
  verification level to the maximum automatically, on top of the alert.
- **`/raidmode <on|off>`**: raises or restores the verification level by
  hand, at any time, independent of the automatic checks above. `off`
  restores whatever level was active before `on` was used.

## Anti-nuke

Watches for one member deleting channels, deleting roles, or banning
members in a fast burst - the signature of a compromised
moderator/administrator account doing deliberate damage, not an
individual mistake. **Off by default** (`antinuke.enabled`) - this is the
one function in this bot that automatically strips a real staff member's
permissions, so it needs a deliberate opt-in rather than acting out of the
box.

When one non-owner member exceeds `antinuke.action_threshold` such
actions within `antinuke.window_seconds`, the bot removes every role from
that member that grants Ban Members, Kick Members, Manage Channels,
Manage Roles, or Manage Guild, and posts an alert to the mod-log channel.
The server owner is never a target. Attribution comes from Discord's own
audit log, so this needs the bot's View Audit Log permission - without
it, an event simply is not counted, rather than guessed at.

This function is deliberately narrow: only channel deletes, role deletes,
and bans are watched. Deleting messages, including through this bot's own
`/purge`, is never counted, so a moderator doing ordinary cleanup is never
mistaken for an attack.

## Starboard

`/setstarboard <channel> [threshold]` sets the channel and the star count
needed (default 3). Any message that collects enough
:star: reactions gets reposted there, with a jump link back to the
original. A message's own author starring their own message never counts
towards the threshold. Reactions inside the starboard channel itself are
ignored. Once a message is posted to the starboard, it stays there even if
the star count later drops - only the displayed count changes.

## Giveaways

`/giveaway start <duration> <winners> <prize>` posts a giveaway with an
Enter button - clicking it again leaves the giveaway, rather than needing
a separate command to back out. The giveaway ends automatically when the
duration passes, picking winners at random from everyone entered, and
announces them in the channel. `/giveaway end <message_id>` ends one
early; `/giveaway reroll <message_id>` picks a new winner for one that
already ended. The Enter button keeps working even across a bot restart.

## Polls

`/poll <question> <option1> <option2> [option3] [option4] [option5]
[duration]` posts a poll with one button per option (2 to 5 of them).
Each member gets one vote: clicking a different option switches it,
clicking your current one retracts it. Give a duration (e.g. `1h`) for
the poll to close itself automatically; without one, it stays open until
a moderator uses `/pollclose <message_id>`. Poll buttons also keep working
across a bot restart.

## Tickets

`/ticketpanel [message]` posts a button members can click to open a
private support ticket. Clicking it creates a new text channel visible
only to that member and anyone with Manage Guild, with its own Close
button inside. A member can only have one ticket open at a time - opening
a second one just points back at the existing channel. Set
`tickets.category_id` to have new ticket channels created under a specific
category; without it, they're created at the top level.

## Welcome and leave messages

No dedicated commands - these use the same `setconfig`/`getconfig`
escape hatch as any other setting (see "Settings reference"), since it's
just a channel and a text template each. Set `welcome.channel_id` and
`leave.channel_id` to turn these on; set `welcome.message` and
`leave.message` to customize the text. Every template accepts these
placeholders: `{member}` (a mention), `{member_name}` (the plain name),
`{server}` (the server name), and `{member_count}`.

## Link embed fixer

Discord does not embed links from several popular sites. An X/Twitter or
TikTok link posts as bare text, so everyone has to leave the chat and open
the platform to see the content.

When a member posts one of these links, the bot replies with the same link
on a proxy host that does serve embed metadata, and hides the original
message's empty embed. The video or post then plays inline.

| Site | Rewritten to |
|---|---|
| `x.com`, `twitter.com` | `fxtwitter.com` |
| `tiktok.com` (including `vm.` and `vt.` short links) | `tiktokfix.com` |
| `instagram.com` | `kkinstagram.com` |
| `reddit.com` | `rxddit.com` |
| `bsky.app` | `fxbsky.app` |
| `pixiv.net` | `phixiv.net` |
| `twitch.tv` clips | `fxtwitch.seria.moe` |

The rewrite is pure text work on the URL. The bot fetches nothing, and no
new dependency is needed. The path and query are kept, minus that site's
known tracking parameters. A link that is already on a proxy host is left
alone, so the bot never re-fixes a fixed link.

**Opting one link out.** Wrap it in angle brackets - `<https://x.com/...>`.
This is Discord's own "do not embed this" syntax, so the bot honors it.
Links inside code blocks and inline code are ignored too. At most 3 links
per message are rewritten, so one message cannot become a wall of replies.

**Undoing a fix.** The bot puts a ❌ on its own reply. Click it and the
reply is deleted and the original message's embed is restored. The member
who posted the link can do this for `embedfix.remove_seconds` (120 by
default) after the fix, and **the ❌ is taken back off once that window
closes**, so it is only showing while it actually does something. Set the
window to `0` and no ❌ is added at all.

Anyone with the Manage Messages permission can undo at any time, with no
time limit - once the ❌ is gone they add one back by hand and it still
works. Anyone else who clicks just has their reaction removed. This check
reads the messages themselves rather than in-memory state, so undo still
works after a bot restart. The removal is booked with the scheduler for
the same reason, which means it can lag the deadline by up to 30 seconds
(the scheduler's sweep interval); the undo itself is already refused in
that gap.

**Permissions.** Hiding the original message's embed needs Manage
Messages. Without it the bot still posts the fixed link - only the empty
embed stays visible.

Turn the whole feature off with `embedfix.enabled`, or turn off one site
with `embedfix.platform.<name>`. Step 14 of `/setup` has all of it.

## Scheduler and reminders

`scheduler.py` is a generic "run this at a specific time" engine, shared
by every feature in this bot that needs to do something later: temp-ban
auto-unbans, giveaway endings, poll auto-closes, and the `/remind`
command below. It survives a bot restart - anything scheduled while the
bot was down still runs, on the next check after it starts back up.

`/remind <duration> <text>` DMs you a reminder after the given delay
(e.g. `10m`, `2h`, `1d`) - falls back to a mention in the channel you sent
it from if your DMs are closed. Works in a DM with the bot too, not only
in a server.

## Music

Commands: `play`, `skip`, `pause`, `resume`, `rewind`, `forward`, `stop`,
`queue`, `nowplaying`, `leave`, `volume`, `loop`, `shuffle`.

The bot plays audio from a search term or a link, found through the
program `yt-dlp`. Each server has one queue and one voice connection. When
the bot joins a voice channel, it joins deafened - the bot does not need to
hear the channel, only to send audio to it.

**Sources.** A plain search term plays from YouTube. A link from YouTube,
SoundCloud, Bandcamp, or a direct audio file plays directly from that
source. The program `yt-dlp` supports many sites. `yt-dlp` selects the
correct method from the link automatically. This function needs no extra
code for these sources.

A Spotify link is different. A Spotify stream has DRM protection. The
program `yt-dlp` has no method to get audio from a Spotify stream. For this
reason, no bot can play audio directly from a Spotify link. Instead, the
bot uses this method for a Spotify link:

1. The bot finds the title and the artist through the Spotify Web API.
2. The bot searches for this text on YouTube.
3. The bot plays the result from YouTube.

This method needs `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in the
file `.env`. See the comment in that file for a 5-minute setup. Without
these values, a Spotify link gives a clear reply: "not configured". The bot
does not crash. If the bot finds no result at all for a search, the `/play`
command reports this. The command does not fail without a message.

**Playlists.** A direct link to a YouTube playlist, or a Spotify playlist,
adds every track to the queue. There is a maximum of 200 tracks.

A YouTube video link can also be part of a playlist, for example
`watch?v=X&list=Y`. This kind of link is not clear on its own: it could
mean one song, or the whole playlist. For this kind of link, the `/play`
command shows two buttons: "Just this song" and "Whole playlist". The
command does not guess.

The bot resolves playlist entries in a lazy way. First, the bot gets the
title and the link for each track. This step is fast, even for a long
playlist. The bot resolves the real audio stream for a track only right
before that track plays. If a track fails, for example a deleted video or a
regional block, the bot skips the track and sends a short message. The rest
of the playlist continues to play.

**Ads and SponsorBlock.** This bot plays audio with no advertisements. The
program `yt-dlp` gets the raw audio stream directly. This raw stream does
not go through the source website's normal ad system. The bot does not
need to remove ads, because the raw stream never had ads.

Separately, the bot uses [SponsorBlock](https://sponsor.ajay.app/) data.
SponsorBlock data marks segments that are not music, for example sponsor
messages, self-promotion, and intros and outros. The bot skips these
segments automatically. Turn off this function for one server with the
setting `music.sponsorblock_enabled`.

SponsorBlock coverage depends on user submissions. Coverage is not complete
for every video. This function only applies to YouTube. SponsorBlock does
not support other sites.

**Buttons and a clean channel.** The "Now playing" message has these
buttons: -10s, Pause/Resume, +10s, Skip, Shuffle, End Session, Queue, and a
Ping-on-empty toggle.

This message is always the newest message from the bot. When a new track
starts, the bot deletes the old "Now playing" message and sends a new one.
Old "Now playing" messages do not collect in the channel.

The Shuffle button shows the new order of the queue. The Shuffle button
does not show only a plain confirmation.

Button responses and command confirmations, for example "Skipped" and
"Paused", are private messages, or the bot deletes them after a few
seconds. This keeps the channel clean.

When the queue becomes empty, the bot mentions whoever added the last
track. Turn off this mention with the Ping-on-empty toggle.

A session ends in one of these cases: the `stop` command, the `leave`
command, an idle timeout, or every member leaving the channel. After a
session ends, the last "Now playing" message stays in the channel as a
record. The bot removes the buttons from this message.

Control commands and buttons only work for a member in the bot's voice
channel. The bot leaves the voice channel on its own in two cases: after
about 2 minutes with no activity, or immediately once every member has
left.

**Volume, loop, and shuffle.** `/volume <0-200>` sets playback volume as a
percent, applied live to whatever is currently playing and remembered for
whatever plays next. `/loop <off|track|queue>` repeats the current track,
repeats the whole queue, or does neither - an explicit `/skip` (or the
Skip button) always moves on regardless of the loop mode, since a
deliberate skip should never be undone by looping. `/shuffle` is the same
shuffle the Shuffle button already does, as its own command.

**Future work.** These functions do not exist yet. The team could add them
without difficulty:

- A `/remove <index>` command.
- Full playlist-URL expansion.

## Minecraft (Crafty4)

Command: `/mcstatus [server]`. This is the only Minecraft-related slash
command - deliberately kept to one entry in the `/` command list. Every
other function (start/stop/restart, sending a console command, managing
the whitelist) is reached through buttons and pop-up forms on this
command's response, not through separate top-level commands. This talks
to [Crafty Controller 4](https://gitlab.com/crafty-controller/crafty-4),
the same panel this bot runs alongside on the TrueNAS system.

**Setup.** This function needs two values in `.env`: `CRAFTY_BASE_URL`
(however you reach Crafty's web UI) and `CRAFTY_API_TOKEN` (from Crafty's
web UI: gear icon, then your user, then the pencil icon, then the API key
tab). Without these values, `/mcstatus` gives a clear "not configured"
reply. The API token also needs Crafty's own `COMMANDS` permission (a
setting on the token itself, inside Crafty - separate from any Discord
permission) on a server before the Start/Stop/Restart, Console, or
Whitelist buttons can act on it. Without it, Crafty replies with its own
"not authorized" error, not a bot crash.

The bot finds servers on its own - it does not need a server ID from you.
If your Crafty API token can see only one server, `/mcstatus` with no
name shows that server's status and controls directly. If it can see more
than one (the normal case for this setup, since Crafty here manages
multiple modpack servers), `/mcstatus` with no name shows a list of every
server as buttons instead - sorted by player count, then online before
offline, then alphabetically by name - and clicking one switches to that
server's status and controls, with a button back to the full list. Give a
name to skip straight to one server - `/mcstatus ozone` matches any server
whose name contains "ozone", case-insensitive. Set `CRAFTY_SERVER_ID` in
`.env` instead if you would rather always default to one specific server
without naming it each time.

**Restricting `/mcstatus` to one server.** A single deployment of this bot
normally only needs `/mcstatus` to work in one Discord server - the one
where the bot owner actually manages the Minecraft servers. At the same
time, someone who forks this repository to run their own copy should get
`/mcstatus` working in whichever server they add their bot to, with no
code changes. This is controlled with an optional `MINECRAFT_GUILD_ID`
value in `.env`, falling back to the existing `GUILD_ID` value (already
used for instant slash-command sync) if that is unset. With neither value
set, `/mcstatus` is unrestricted - the fork-friendly default. No guild ID
is hardcoded in the source; the restriction is configuration only.

**Status view.** Available to anyone. Shows online or offline status, the
player count, the version, and the world name, when Crafty provides these
values - a missing value is left out, not shown as an error. This was
confirmed against a real Crafty Controller 4 instance, not just its
source code: Crafty represents "not set" for some fields (for example the
version of a server that has never been started) as the literal string
`"False"`, not a JSON `false` or empty value, and this function treats
that the same as missing so the embed never shows a false "Version:
False".

**Start / Stop / Restart buttons.** Require Manage Server, re-checked on
whoever actually clicks (the buttons stay on the message for anyone to
see, not just whoever ran the command). Start is disabled if the server
is already running; Stop and Restart are disabled if it is not. Clicking
one calls Crafty's action API for that server and replies with a plain
confirmation, or Crafty's own error message if the action fails. `Start`
was confirmed working against a real, offline Crafty4-managed server:
Crafty genuinely booted it. `Stop` and `Restart` use the same naming
convention Crafty's own source uses internally, but were not
independently confirmed the same way - if either name turns out to be
wrong, Crafty rejects the request and the bot shows that as a plain error
message rather than crashing.

**Console button.** Requires Manage Server. Opens a pop-up form asking for
one command, then sends it to that server's console as-is - this is real,
arbitrary Minecraft console access (`/op`, `/ban`, `/stop`, anything),
which is why it needs Manage Server rather than a lower permission. Since
Minecraft console output is not a synchronous request/response, the bot
waits briefly after sending the command, then shows the most recent
console lines as a best-effort readout - not a guaranteed exact capture of
that command's output. If the target server is not running, the bot says
so plainly instead of a generic error.

**Whitelist button.** Requires Manage Server. Opens a pop-up form asking
for an action (`add`, `remove`, or `list`) and a player name, blank for
`list`. Crafty Controller 4 has no whitelist API of its own, so this
function sends the vanilla Minecraft `whitelist add`/`whitelist remove`/
`whitelist list` console commands through the same mechanism the Console
button uses - it only works while the target server is running.

## Ask the bot (`@Aguiliar ...`)

Ping the bot and it answers, using a local LLM. Nothing else triggers it — not
a prefix, not a keyword, not replying to it. Set `llm.enabled` to `true` to
switch it on; it is off by default.

**It runs on a CPU, and it is slow.** The model is served from the NAS, which
has no GPU. Measured against the live server: prompt processing about 5
tokens/sec, generation about 2.3. A plain reply takes roughly two minutes and
one that looks something up takes four to six. The bot posts a placeholder
immediately and edits it as words arrive, so you can watch it work. If that is
too slow for your hardware, `llm.maxtokens` is the lever that matters most.

### How it decides what the conversation is about

It does not get handed the channel history. Each ping carries only the message
itself, the channel name, and one line saying how long ago the previous message
was — enough for the model to judge for itself whether earlier context is even
relevant. A ping after three days of silence usually is not. When it does need
history, it asks for it, using one of two read-only tools:

| Tool | What it reads |
|---|---|
| `read_recent_messages` | Up to 100 recent messages in the channel you pinged it in, with an `offset` so it can page further back |
| `read_reply_chain` | The chain of messages your message is replying to, up to 10 hops |
| `read_member_profile` | One member of this server, looked up by the name they are shown under: their names, roles, join date, account age, and their online status and current game when the Presence intent is enabled |

It also remembers the last couple of exchanges in the same channel (see
`llm.memoryturns`), which are added to the prompt directly. That is cheaper than
a tool call — a tool costs a whole extra request to a model that generates about
two tokens a second — so a follow-up question usually needs no lookup at all.

**A name is not a key.** Two people can share a display name, so
`read_member_profile` returns the candidates and makes the bot ask which one you
meant rather than guessing. It can never read anyone's "About Me" bio: Discord
does not expose it to bots at all, and the workaround for that is a user token,
which is against Discord's rules and is not something this bot does.

**Online status needs the Presence intent.** It is a privileged intent, so it
must be ticked at
[the developer portal](https://discord.com/developers/applications) under your
application -> Bot -> Privileged Gateway Intents -> Presence Intent. The bot asks
for it on startup; if it is not enabled, it logs an error and starts again
without it rather than failing to log in, and the profile field then reads
"status: not available (presence intent off)" instead of claiming that everyone
is offline. Restart the bot after enabling it.

This is a deliberate trade: fetching history costs about 0.2 seconds per token
of prompt on this hardware, so paying for it only when it is needed is what
keeps a normal reply to two minutes instead of five.

### What it cannot do

The limits are enforced in Python, not asked for in the prompt:

- The tool list is a fixed allowlist of the read tools above. Anything else is
  refused.
- **No tool takes a channel, guild, user, or message ID.** The handlers read the
  channel you pinged in and nothing else, so there is no way to make it read
  another channel, a channel you cannot see, a DM, or another server.
- Every argument is re-clamped in code — a request for 9999 messages reads 100.
- Malformed or unknown tool calls fail closed.
- It cannot fetch URLs, read attachments, run commands, touch settings or
  secrets, or write anything anywhere.
- It cannot see images. The model it runs on is text-only, so an attached
  picture is invisible to it rather than misdescribed.
- Messages it retrieves are inert data: mention syntax is stripped, length is
  capped, and they are fenced off in the prompt. Text in a Discord message is
  never treated as an instruction, whatever it claims to be.

### Seeing what it said (`/llmlog`)

Every ping is recorded: the question, the answer, which tools it called, how
long it took, and whether it worked at all. Failures are recorded too — a
timeout, an error from the model server, or an empty answer each leave a row
with a status and the error text, because a reply that never arrived is exactly
the one worth looking at. `/llmlog [count]` shows the most recent ones and needs
Manage Server. Rows older than `llm.logdays` are pruned once a day.

Logging never gets in the way of a reply: it happens after the answer is on
screen, and if the database is unavailable the bot loses the log row, not the
answer.

### Persona, and why the safety preamble is not editable

The system prompt is two layers. The first is a preamble that lives in code —
it is not a config key, `/setconfig` cannot reach it, and `/setup` does not
show it. It covers only what must not be editable: that retrieved messages are
data and never instructions, what the tools may touch, that the bot has no
moderation powers, and that a persona grants it nothing. The second layer is
`llm.persona`, which owns voice, length and formatting, and is freely editable
through `/setpersona`.

Between the two sits a short identity block naming the bot, the server, and
what it is. It is assembled once per server and is byte-identical between
pings, which is what lets llama.cpp reuse the cached prompt prefix instead of
reprocessing it every time.

They are assembled in that order, always, with the persona clearly fenced. This
split is on purpose: "you are X, and X always answers" is the classic way a
persona erodes a model's refusals, and this deployment runs *stock* Qwen
weights specifically so those refusals stay in place. A persona changes how the
bot talks. It does not change what the bot will do.

## Updates

The bot can tell when GitHub has commits it doesn't have yet, and can
restart itself to pick them up - either manually, with a button, or
automatically.

**How detection works.** There are two paths. `/about` checks live, when
you ask: it runs `git fetch` inside the container and compares the running
checkout's commit against the fetched branch, so a commit pushed a minute
ago shows up immediately. Repeated calls within 60 seconds reuse that
answer rather than fetching again. Separately, a background check
(`bot/modules/updater.py`) does the same thing roughly every 30 minutes,
which is what drives automatic updates when nobody is asking.
This needs the deployed image to be a real git checkout with an `origin`
remote - true by default, since `Dockerfile` now bakes in the whole repo
(including `.git`, excluding secrets - see `.dockerignore`), not just the
`bot/` folder. If the container isn't a git checkout for some reason, or
the fetch fails (no network), the bot just reports "unable to check"
rather than erroring.

**How applying an update works.** The running Python process can't safely
replace its own already-imported code out from under itself, so "applying
an update" doesn't happen inside the process at all. Instead, `entrypoint.
sh` runs `git pull` once, before `python -m bot` starts, on every
container start (see "Architecture" and "Deploying to TrueNAS SCALE").
Applying an update from Discord is therefore just: exit the process, and
let the container's restart policy (`restart: unless-stopped` in
`docker-compose.yml`, or the equivalent for a TrueNAS Custom App) relaunch
it - which reruns `entrypoint.sh` against whatever is now on `origin`.

**Manual updates.** `/about` always shows an "Updates" field - "Up to
date," "N commit(s) behind - latest: `<commit summary>`," or "Unable to
check." When an update is detected, an "Apply update" button appears
under the same message. Anyone with Manage Server can click it; the bot
replies, then restarts within a few seconds.

**Automatic updates.** Off by default. Turn it on from `/setup`'s
"Updates" step (`updates.auto_apply`). When on, the next background check
that finds a newer commit restarts the bot right away, with no button
click needed - useful if you don't want to babysit it, at the cost of the
bot occasionally restarting itself (a few seconds of downtime) without
you choosing the exact moment.

**Why 30 minutes, not instant.** Checking is a `git fetch` against
GitHub - frequent enough for the unattended auto-apply path to notice a
new release within the hour, without hammering GitHub. This timer does not
gate `/about`, which fetches on demand; it used to, and the result was
`/about` reporting "Up to date" for up to half an hour after a push.
`entrypoint.sh`'s pull at every container start is unrelated to both - a
fresh restart always gets the latest code regardless of either.

## Roadmap and next steps

- `counters.py` and `leveling.py` are not complete, with design notes in
  each file's docstring. Each one needs one design decision before real
  development is worthwhile. (`scheduler.py`, the third original Phase 6
  module, is now done - see "Scheduler and reminders".)
- On-demand Minecraft server backups from Discord - Crafty Controller 4
  has no API to trigger one, only to schedule recurring ones through its
  own web UI, so this was left out rather than shipping a button that
  cannot do what it implies.
- A `/remove <index>` command for the music queue, and full playlist-URL
  expansion (see "Music").

## Troubleshooting: duplicate slash commands

If Discord's `/` picker shows every command twice, the cause is almost
always a missing `GUILD_ID` in `.env` on a deployment that previously ran
*with* one set (or vice versa). `_sync_commands()` in `bot/core.py` only
clears stale global registrations when it does a guild-scoped sync
(`GUILD_ID` set); a global-only sync (no `GUILD_ID`) never touches
leftover guild-scoped commands from an earlier run. If those two states
alternate across restarts/redeploys, both a global and a guild copy of
each command end up registered at once, and Discord shows both.

Fix: set `GUILD_ID` in `.env` to your server's ID and restart the bot -
this switches to the guild-scoped path, which also clears any stale
global registrations in the same pass. Keep `GUILD_ID` set consistently
in production from then on; this is a config gotcha rather than a bug
that reproduces on its own once `GUILD_ID` is set and left alone.
