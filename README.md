# Discord Mod/Utility Bot

This is a Discord bot. You run this bot yourself, on your own computer or
server. This process has the name self-hosting. The bot works with one
Discord server at a time.

The bot has these functions:

- Anti-spam control, based on member behavior.
- Automod, based on message content.
- Moderation commands, with a case history.
- Role management.
- Music playback from YouTube and other sources.
- Personality functions: quotes, tags, buckets, witty replies, a bored
  detector, and a text generator.

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
└── modules/
    ├── status.py        ping, uptime, about, help, setconfig, getconfig
    ├── automod.py        word filter, invite block, capital-letter check
    ├── antispam.py        message flooding, repeated messages, mass mentions
    ├── moderation.py      kick, ban, mute, warn, case history, Mute role
    ├── logging_module.py  message edit/delete logs, join/leave logs, mod log
    ├── roles.py           self-assign roles, reaction roles, auto-role
    ├── quote.py           save and show quotes
    ├── tag.py             custom text commands
    ├── bucket.py          named random-pick lists
    ├── witty.py           random reply when a member mentions the bot
    ├── bored.py           sends a message after a channel is quiet
    ├── markov.py          makes text from recent channel messages
    ├── music.py           YouTube playback; SponsorBlock auto-skip
    ├── setup.py           setup wizard for server settings
    ├── scheduler.py       not complete; see the file for design notes
    ├── counters.py        not complete; see the file for design notes
    ├── leveling.py         not complete; see the file for design notes
    └── minecraft.py        not complete; needs your Crafty4 API details
```

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
| 6 | scheduler, counters, leveling, minecraft | Not complete - see the notes in each file |
| 7 | Hybrid `/slash` commands, music (yt-dlp and SponsorBlock) | Done |
| 8 | Antispam bulk-delete fix, role-based mute, `/setup` wizard, `/help` | Done |

At this time, 103 automated tests exist for this bot, and all 103 tests
pass. Run the tests with the command `pytest -q`. The tests check three
parts of the bot: the detection logic in `automod.py`, `antispam.py`,
`markov.py`, and `music.py` (this logic does not depend on Discord); the
moderation and status commands (these tests use mock Discord objects and a
temporary SQLite database file); and the data storage layer in `stores.py`
(these tests also use a temporary SQLite database file).

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
| `ban <member> [reason]` **mod** | moderation | Bans a member from the server; adds a case history record |
| `mute <member> [duration] [reason]` **mod** | moderation | Mutes a member. See "Moderation" for the duration rules |
| `unmute <member>` **mod** | moderation | Removes a mute: the timeout, the Mute role, and any scheduled end time |
| `warn <member> [reason]` **mod** | moderation | Sends a warning; sends a direct message; adds a case history record |
| `cases <member>` **mod** | moderation | Shows the case history for a member |
| `case <id>` **mod** | moderation | Shows one case by its ID |
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

The anti-spam function, and part of the automod function, have no commands
of their own. Anti-spam checks for message flooding. Automod checks for
capital letters and invite links. These checks are always active. Change
their settings with the `setconfig` command or the `setup` wizard - see
"Settings reference".

## Local setup

```bash
git clone <your-repo-url>
cd discord-bot
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

Some settings have their own command: `setlogchannel`, `setautorole`,
`setboredchannel`, and `filter add`. You can change every other setting
with `setconfig <key> <value>` and `getconfig <key>` (both mod-only). The
`/setup` wizard shows and changes all settings, in a guided series of
steps, and is the easiest way to configure a server. See the next section.

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
| 1/9 | General | A button, "Edit prefix", opens a form with one field: the command prefix | `commandprefix` |
| 2/9 | Logging | A menu selects the log channel. Three on/off buttons: "Log edits", "Log deletes", "Log joins/leaves" | `logging.channel`, `logging.edits`, `logging.deletes`, `logging.joins` |
| 3/9 | Moderation | No button or menu. The bot creates the Mute role if it does not exist, or confirms the role if it does, and shows its name. Run `/setup` again to re-apply the role's channel permissions everywhere, for example after adding a new channel | `moderation.mute_role` |
| 4/9 | Anti-spam | A button, "Edit thresholds", opens a form with 5 fields: max messages per window, window length, max duplicate messages, max mentions, and timeout duration. All five must be whole numbers | `spam.max_messages`, `spam.window_seconds`, `spam.max_duplicates`, `spam.max_mentions`, `spam.timeout_seconds` |
| 5/9 | Automod | An on/off button, "Block invites". A separate button, "Edit caps thresholds", opens a form with 2 fields: the caps percent threshold, and the minimum message length for the caps check. This step does not include the banned-word list - that list is a set of words, not one value, and is managed instead with `filter add`, `filter remove`, and `filter` (list) | `automod.block_invites`, `automod.caps_threshold`, `automod.caps_minlen` |
| 6/9 | Roles | A menu selects the auto-role for new members | `roles.autorole` |
| 7/9 | Bored detector | A menu selects the nudge channel. A button, "Edit bored settings", opens a form with 2 fields: idle seconds before the nudge, and the nudge message | `bored.channel`, `bored.idle_seconds`, `bored.message` |
| 8/9 | Music | An on/off button, "SponsorBlock auto-skip" | `music.sponsorblock_enabled` |
| 9/9 | Summary | A read-only summary of every setting above, its current value or default, and one line for Spotify that shows only "Configured" or "Not configured" - this step never shows or accepts the real Spotify credentials | None - display only |

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

## Music

Commands: `play`, `skip`, `pause`, `resume`, `rewind`, `forward`, `stop`,
`queue`, `nowplaying`, `leave`.

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

**Future work.** These functions do not exist yet. The team could add them
without difficulty:

- A `/volume` command.
- A loop function, for one track or for the whole queue.
- A `/remove <index>` command.
- Full playlist-URL expansion.

## Roadmap and next steps

- The Phase 6 modules (`scheduler.py`, `counters.py`, `leveling.py`,
  `minecraft.py`) are not complete, with design notes in each file's
  docstring. Each one needs one design decision before real development is
  worthwhile.
- `minecraft.py` specifically needs your Crafty4 Controller API details
  (host, API token, server ID), or a decision to use the simpler
  `mcstatus`-only route, before it can query your actual server. This is a
  planned follow-up now that the bot runs alongside Crafty4 on the same
  TrueNAS system.
- The music extras noted above (`/volume`, loop, `/shuffle`, and similar
  functions).
