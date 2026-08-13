"""
setup - a mod-only, re-runnable step-by-step wizard for every per-guild
setting this bot has (log channel, autorole, spam thresholds, automod,
bored detector, sponsorblock, and the mute role).

Deliberately does NOT touch Spotify credentials - those are a bot-wide
SPOTIFY_CLIENT_ID/SECRET .env secret (see bot/modules/music.py and the
README), not per-guild config. Putting them behind a Discord command would
leak the secret into Discord's own interaction history the moment someone
types it, apply bot-wide from a single guild's command, and require
persisting a secret in the bot's own SQLite DB. /setup only reports
configured/not-configured and points to the README's .env setup step.
"""

from __future__ import annotations

import os
from typing import Awaitable, Callable, Optional

import discord
from discord.ext import commands

from bot.modules.updater import UpdateStatus, describe_status

STEP_GENERAL = "general"
STEP_LOGGING = "logging"
STEP_MODERATION = "moderation"
STEP_ANTISPAM = "antispam"
STEP_AUTOMOD = "automod"
STEP_RAID = "raid"
STEP_ANTINUKE = "antinuke"
STEP_ROLES = "roles"
STEP_BORED = "bored"
STEP_STARBOARD = "starboard"
STEP_TICKETS = "tickets"
STEP_GREETINGS = "greetings"
STEP_MUSIC = "music"
STEP_UPDATES = "updates"
STEP_SUMMARY = "summary"

STEPS = [
    STEP_GENERAL,
    STEP_LOGGING,
    STEP_MODERATION,
    STEP_ANTISPAM,
    STEP_AUTOMOD,
    STEP_RAID,
    STEP_ANTINUKE,
    STEP_ROLES,
    STEP_BORED,
    STEP_STARBOARD,
    STEP_TICKETS,
    STEP_GREETINGS,
    STEP_MUSIC,
    STEP_UPDATES,
    STEP_SUMMARY,
]

STEP_TITLES = {
    STEP_GENERAL: "General",
    STEP_LOGGING: "Logging",
    STEP_MODERATION: "Moderation",
    STEP_ANTISPAM: "Anti-spam",
    STEP_AUTOMOD: "Automod",
    STEP_RAID: "Raid protection",
    STEP_ANTINUKE: "Anti-nuke",
    STEP_ROLES: "Roles",
    STEP_BORED: "Bored detector",
    STEP_STARBOARD: "Starboard",
    STEP_TICKETS: "Tickets",
    STEP_GREETINGS: "Welcome/leave messages",
    STEP_MUSIC: "Music",
    STEP_UPDATES: "Updates",
    STEP_SUMMARY: "Summary",
}

_BORED_DEFAULT = "...it's quiet in here. Too quiet."
_WELCOME_DEFAULT = "Welcome {member} to {server}!"
_LEAVE_DEFAULT = "{member_name} has left {server}."

# (key, reset_value, display_default, description) - the machine-readable
# twin of the README's "Config reference" table. reset_value is what
# "Reset to defaults" writes back - None means the real default is "no
# value stored" (so reset deletes the row instead of writing a literal
# "unset"). display_default is always the text shown to a moderator, so
# a value that's already at its default still says so, and one that
# isn't shows what it would revert to.
CONFIG_MANIFEST = [
    ("commandprefix", "!", "!", "Command prefix"),
    ("logging.channel", None, "unset", "Log channel"),
    ("logging.edits", "true", "true", "Log message edits"),
    ("logging.deletes", "true", "true", "Log message deletes"),
    ("logging.joins", "true", "true", "Log joins/leaves"),
    ("moderation.mute_role", None, "unset", "Mute role"),
    ("spam.max_messages", "5", "5", "Messages allowed per window"),
    ("spam.window_seconds", "6", "6", "Rolling window (seconds)"),
    ("spam.max_duplicates", "3", "3", "Duplicate messages allowed"),
    ("spam.max_mentions", "5", "5", "Mentions before flagged"),
    ("spam.timeout_seconds", "300", "300", "Timeout duration (seconds)"),
    ("automod.block_invites", "true", "true", "Block invite links"),
    ("automod.caps_threshold", "70", "70", "Caps % threshold"),
    ("automod.caps_minlen", "10", "10", "Caps min message length"),
    ("raid.min_account_age_hours", "0", "0 (off)", "Minimum account age to join (hours)"),
    ("raid.join_threshold", "0", "0 (off)", "Joins counted as a raid burst"),
    ("raid.join_window_seconds", "30", "30", "Raid burst window (seconds)"),
    ("raid.auto_lockdown", "false", "false", "Auto-lockdown on burst"),
    ("antinuke.enabled", "false", "false", "Anti-nuke enabled"),
    ("antinuke.action_threshold", "3", "3", "Destructive actions before punishing"),
    ("antinuke.window_seconds", "30", "30", "Anti-nuke window (seconds)"),
    ("roles.autorole", None, "unset", "Auto-role on join"),
    ("bored.channel", None, "unset", "Bored-nudge channel"),
    ("bored.idle_seconds", "1800", "1800", "Idle seconds before nudge"),
    ("bored.message", _BORED_DEFAULT, _BORED_DEFAULT, "Nudge message"),
    ("starboard.channel", None, "unset", "Starboard channel"),
    ("starboard.threshold", "3", "3", "Stars needed to post"),
    ("tickets.category_id", None, "unset", "Ticket channel category"),
    ("welcome.channel_id", None, "unset", "Welcome message channel"),
    ("welcome.message", _WELCOME_DEFAULT, _WELCOME_DEFAULT, "Welcome message"),
    ("leave.channel_id", None, "unset", "Leave message channel"),
    ("leave.message", _LEAVE_DEFAULT, _LEAVE_DEFAULT, "Leave message"),
    ("music.sponsorblock_enabled", "true", "true", "SponsorBlock auto-skip"),
    ("updates.auto_apply", "false", "false", "Automatically restart to apply detected updates"),
]

# Prefixes a step's "Reset to defaults" button clears - STEP_MODERATION and
# STEP_SUMMARY are deliberately absent (nothing user-editable to reset).
STEP_RESET_PREFIXES = {
    STEP_GENERAL: ["commandprefix"],
    STEP_LOGGING: ["logging."],
    STEP_ANTISPAM: ["spam."],
    STEP_AUTOMOD: ["automod."],
    STEP_RAID: ["raid."],
    STEP_ANTINUKE: ["antinuke."],
    STEP_ROLES: ["roles."],
    STEP_BORED: ["bored."],
    STEP_STARBOARD: ["starboard."],
    STEP_TICKETS: ["tickets."],
    STEP_GREETINGS: ["welcome.", "leave."],
    STEP_MUSIC: ["music."],
    STEP_UPDATES: ["updates."],
}


def format_status(enabled: bool) -> str:
    """Pure: a green/red circle plus the word, so on/off state reads as a
    color, not just text - Discord embeds can't color individual lines, so
    an emoji is the closest real "color coding" achievable inline."""
    return "\N{LARGE GREEN CIRCLE} Enabled" if enabled else "\N{LARGE RED CIRCLE} Disabled"


def combined_status_color(states: list) -> discord.Color:
    """Pure: green if every given toggle is on, red if every one is off,
    blurple if mixed - used for a step's embed color when it bundles more
    than one independent on/off setting."""
    if all(states):
        return discord.Color.green()
    if not any(states):
        return discord.Color.red()
    return discord.Color.blurple()


def _is_boolean_key(display_default: str) -> bool:
    return display_default.lower() in ("true", "false")


def build_summary_lines(values: dict) -> list[str]:
    """Pure: given a {key: stored_value_or_None} mapping, builds one display
    line per manifest entry - the text /getconfig would show for each key,
    all at once. Always shows the default, whether or not a value is set,
    so a stored value can be compared against what it would reset to."""
    lines = []
    for key, _, display_default, description in CONFIG_MANIFEST:
        value = values.get(key)
        if value is None:
            shown = f"{display_default} (default)"
        elif _is_boolean_key(display_default):
            enabled = value.lower() in ("1", "true", "yes", "on")
            shown = f"{format_status(enabled)} (default: {format_status(display_default == 'true')})"
        else:
            shown = f"{value} (default: {display_default})"
        lines.append(f"**{description}** (`{key}`): {shown}")
    return lines


class SetupView(discord.ui.View):
    def __init__(self, cog: "Setup", guild: discord.Guild, invoker_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.invoker_id = invoker_id
        self.step_index = 0
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This setup session isn't yours.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    @property
    def step(self) -> str:
        return STEPS[self.step_index]

    async def cfg(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return await self.cog.bot.stores.config.get(self.guild.id, key, default)

    async def cfg_bool(self, key: str, default: bool) -> bool:
        return await self.cog.bot.stores.config.get_bool(self.guild.id, key, default)

    async def cfg_int(self, key: str, default: int) -> int:
        return await self.cog.bot.stores.config.get_int(self.guild.id, key, default)

    async def start(self, ctx: commands.Context) -> None:
        await self._rebuild_items()
        embed = await self._build_embed()
        self.message = await ctx.send(embed=embed, view=self)

    async def refresh(self, interaction: discord.Interaction) -> None:
        await self._rebuild_items()
        embed = await self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _rebuild_items(self) -> None:
        self.clear_items()
        step = self.step

        if step == STEP_GENERAL:
            self.add_item(_ModalButton("Edit prefix", lambda: _build_prefix_modal(self)))
        elif step == STEP_LOGGING:
            self.add_item(await _LogChannelSelect.create(self))
            self.add_item(await _ToggleButton.create(self, "logging.edits", "Log edits", True))
            self.add_item(await _ToggleButton.create(self, "logging.deletes", "Log deletes", True))
            self.add_item(await _ToggleButton.create(self, "logging.joins", "Log joins/leaves", True))
        elif step == STEP_MODERATION:
            mod_cog = self.cog.moderation_cog()
            if mod_cog is not None:
                await mod_cog.ensure_mute_role(self.guild)
        elif step == STEP_ANTISPAM:
            self.add_item(_ModalButton("Edit thresholds", lambda: _build_antispam_modal(self)))
        elif step == STEP_AUTOMOD:
            self.add_item(await _ToggleButton.create(self, "automod.block_invites", "Block invites", True))
            self.add_item(_ModalButton("Edit caps thresholds", lambda: _build_automod_modal(self)))
        elif step == STEP_RAID:
            self.add_item(
                await _ToggleButton.create(self, "raid.auto_lockdown", "Auto-lockdown on burst", False)
            )
            self.add_item(_ModalButton("Edit raid thresholds", lambda: _build_raid_modal(self)))
        elif step == STEP_ANTINUKE:
            self.add_item(await _ToggleButton.create(self, "antinuke.enabled", "Anti-nuke enabled", False))
            self.add_item(_ModalButton("Edit anti-nuke thresholds", lambda: _build_antinuke_modal(self)))
        elif step == STEP_ROLES:
            self.add_item(await _AutoRoleSelect.create(self))
        elif step == STEP_BORED:
            self.add_item(await _BoredChannelSelect.create(self))
            self.add_item(_ModalButton("Edit bored settings", lambda: _build_bored_modal(self)))
        elif step == STEP_STARBOARD:
            self.add_item(await _StarboardChannelSelect.create(self))
            self.add_item(_ModalButton("Edit star threshold", lambda: _build_starboard_modal(self)))
        elif step == STEP_TICKETS:
            self.add_item(await _TicketCategorySelect.create(self))
        elif step == STEP_GREETINGS:
            self.add_item(await _WelcomeChannelSelect.create(self))
            self.add_item(await _LeaveChannelSelect.create(self))
            self.add_item(_ModalButton("Edit welcome/leave text", lambda: _build_greetings_modal(self)))
        elif step == STEP_MUSIC:
            self.add_item(
                await _ToggleButton.create(self, "music.sponsorblock_enabled", "SponsorBlock auto-skip", True)
            )
        elif step == STEP_UPDATES:
            self.add_item(await _ToggleButton.create(self, "updates.auto_apply", "Auto-update", False))

        if step in STEP_RESET_PREFIXES:
            self.add_item(_ResetButton())

        back = _NavButton("back")
        back.disabled = self.step_index == 0
        self.add_item(back)
        self.add_item(_NavButton("finish" if self.step_index == len(STEPS) - 1 else "next"))

    async def _build_embed(self) -> discord.Embed:
        step = self.step
        embed = discord.Embed(
            title=f"Setup - {STEP_TITLES[step]} ({self.step_index + 1}/{len(STEPS)})",
            color=discord.Color.blurple(),
        )

        if step == STEP_GENERAL:
            embed.description = f"**Command prefix**: {await self._value_line('commandprefix')}"
        elif step == STEP_LOGGING:
            channel_id = await self.cfg("logging.channel")
            channel_text = f"<#{channel_id}>" if channel_id else "not set"
            edits = await self.cfg_bool("logging.edits", True)
            deletes = await self.cfg_bool("logging.deletes", True)
            joins = await self.cfg_bool("logging.joins", True)
            embed.description = (
                f"**Log channel**: {channel_text}\n"
                f"**Log edits**: {format_status(edits)}\n"
                f"**Log deletes**: {format_status(deletes)}\n"
                f"**Log joins/leaves**: {format_status(joins)}"
            )
            embed.color = combined_status_color([edits, deletes, joins])
        elif step == STEP_MODERATION:
            mod_cog = self.cog.moderation_cog()
            role = await mod_cog.get_mute_role(self.guild) if mod_cog is not None else None
            embed.description = (
                f"**Mute role**: {role.mention if role else 'being created...'}\n\n"
                "Auto-created and kept in sync across every text channel (denies sending "
                "messages/reactions/threads). Nothing to configure here - this just confirms "
                "it exists."
            )
        elif step == STEP_ANTISPAM:
            embed.description = "\n".join(await self._manifest_lines("spam."))
        elif step == STEP_AUTOMOD:
            block_invites = await self.cfg_bool("automod.block_invites", True)
            embed.description = "\n".join(await self._manifest_lines("automod."))
            embed.color = discord.Color.green() if block_invites else discord.Color.red()
        elif step == STEP_RAID:
            min_age = await self.cfg_int("raid.min_account_age_hours", 0)
            join_threshold = await self.cfg_int("raid.join_threshold", 0)
            auto_lockdown = await self.cfg_bool("raid.auto_lockdown", False)
            embed.description = (
                f"**Minimum account age gate**: {format_status(min_age > 0)}\n"
                f"**Join-burst detection**: {format_status(join_threshold > 0)}\n"
                f"**Auto-lockdown on burst**: {format_status(auto_lockdown)}\n\n"
                + "\n".join(await self._manifest_lines("raid."))
                + "\n\nBoth automatic checks are off by default (0) - a fresh server sees no "
                "kicks or alerts until you set a threshold here."
            )
            embed.color = combined_status_color([min_age > 0, join_threshold > 0, auto_lockdown])
        elif step == STEP_ANTINUKE:
            enabled = await self.cfg_bool("antinuke.enabled", False)
            embed.description = (
                f"**Anti-nuke**: {format_status(enabled)}\n\n"
                + "\n".join(await self._manifest_lines("antinuke."))
                + "\n\nOff by default. When on, one non-owner member deleting channels/roles or "
                "mass-banning members past the threshold gets their dangerous permission roles "
                "stripped automatically, with an alert to the log channel."
            )
            embed.color = discord.Color.green() if enabled else discord.Color.red()
        elif step == STEP_ROLES:
            role_id = await self.cfg("roles.autorole")
            role_text = f"<@&{role_id}>" if role_id else "unset (default)"
            embed.description = f"**Auto-role on join**: {format_status(bool(role_id))} - {role_text}"
            embed.color = discord.Color.green() if role_id else discord.Color.red()
        elif step == STEP_BORED:
            channel_id = await self.cfg("bored.channel")
            embed.description = (
                f"**Bored detector**: {format_status(bool(channel_id))}\n\n"
                + "\n".join(await self._manifest_lines("bored."))
            )
            embed.color = discord.Color.green() if channel_id else discord.Color.red()
        elif step == STEP_STARBOARD:
            channel_id = await self.cfg("starboard.channel")
            embed.description = (
                f"**Starboard**: {format_status(bool(channel_id))}\n\n"
                + "\n".join(await self._manifest_lines("starboard."))
            )
            embed.color = discord.Color.green() if channel_id else discord.Color.red()
        elif step == STEP_TICKETS:
            category_id = await self.cfg("tickets.category_id")
            category_text = f"<#{category_id}>" if category_id else "unset (default)"
            embed.description = (
                f"**Ticket channel category**: {category_text}\n\n"
                "Tickets work with no category set - new ticket channels are just created at "
                "the top level instead. There's nothing to \"disable\" here."
            )
        elif step == STEP_GREETINGS:
            welcome_channel = await self.cfg("welcome.channel_id")
            leave_channel = await self.cfg("leave.channel_id")
            embed.description = (
                f"**Welcome message**: {format_status(bool(welcome_channel))}\n"
                f"**Leave message**: {format_status(bool(leave_channel))}\n\n"
                + "\n".join(await self._manifest_lines("welcome."))
                + "\n"
                + "\n".join(await self._manifest_lines("leave."))
            )
            embed.color = combined_status_color([bool(welcome_channel), bool(leave_channel)])
        elif step == STEP_MUSIC:
            enabled = await self.cfg_bool("music.sponsorblock_enabled", True)
            embed.description = f"**SponsorBlock auto-skip**: {format_status(enabled)}"
            embed.color = discord.Color.green() if enabled else discord.Color.red()
        elif step == STEP_UPDATES:
            auto_apply = await self.cfg_bool("updates.auto_apply", False)
            updater_cog = self.cog.bot.get_cog("Updater")
            live_status = updater_cog.status if updater_cog is not None else UpdateStatus(checked=False)
            embed.description = (
                f"**Auto-update**: {format_status(auto_apply)}\n"
                f"**Current status**: {describe_status(live_status)}\n\n"
                "When enabled, the bot restarts itself the next time it notices a newer "
                "commit on GitHub (checked roughly every 30 minutes) - this relies on the "
                "container's restart policy and its `git pull`-on-start entrypoint, see the "
                "README's \"Updates\" section. A manual \"Apply update\" button also appears "
                "under `/about` any time an update is detected, regardless of this setting."
            )
            embed.color = discord.Color.green() if auto_apply else discord.Color.red()
        elif step == STEP_SUMMARY:
            values = {key: await self.cfg(key) for key, _, _, _ in CONFIG_MANIFEST}
            embed.description = "\n".join(build_summary_lines(values))
            spotify_ok = bool(os.getenv("SPOTIFY_CLIENT_ID")) and bool(os.getenv("SPOTIFY_CLIENT_SECRET"))
            embed.add_field(
                name="Spotify",
                value=(
                    ("Configured" if spotify_ok else "Not configured")
                    + " - a one-time host-level `.env` step (see the README), never set through "
                    "a Discord command."
                ),
                inline=False,
            )

        return embed

    async def _manifest_lines(self, key_prefix: str) -> list[str]:
        lines = []
        for key, _, display_default, description in CONFIG_MANIFEST:
            if not key.startswith(key_prefix):
                continue
            value = await self.cfg(key)
            if value is None:
                shown = f"{display_default} (default)"
            elif _is_boolean_key(display_default):
                enabled = value.lower() in ("1", "true", "yes", "on")
                shown = f"{format_status(enabled)} (default: {format_status(display_default == 'true')})"
            else:
                shown = f"{value} (default: {display_default})"
            lines.append(f"**{description}**: {shown}")
        return lines

    async def _value_line(self, key: str) -> str:
        """One key's current-value-vs-default line, for steps with a single
        display value (General's prefix, Roles' autorole, Tickets' category)."""
        display_default = next(d for k, _, d, _ in CONFIG_MANIFEST if k == key)
        value = await self.cfg(key)
        if value is None:
            return f"{display_default} (default)"
        return f"{value} (default: {display_default})"


class _NavButton(discord.ui.Button):
    def __init__(self, direction: str):
        label = {"back": "Back", "next": "Next", "finish": "Finish"}[direction]
        style = discord.ButtonStyle.success if direction == "finish" else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style)
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        view: SetupView = self.view
        if self.direction == "back":
            view.step_index = max(0, view.step_index - 1)
            await view.refresh(interaction)
        elif self.direction == "next":
            view.step_index = min(len(STEPS) - 1, view.step_index + 1)
            await view.refresh(interaction)
        else:  # finish
            for item in view.children:
                item.disabled = True
            await interaction.response.edit_message(view=view)
            view.stop()


class _ResetButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Reset to defaults", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        view: SetupView = self.view
        prefixes = STEP_RESET_PREFIXES.get(view.step, [])
        for key, reset_value, _, _ in CONFIG_MANIFEST:
            if not any(key.startswith(prefix) for prefix in prefixes):
                continue
            if reset_value is None:
                await view.cog.bot.stores.config.delete(view.guild.id, key)
            else:
                await view.cog.bot.stores.config.set(view.guild.id, key, reset_value)
        await view.refresh(interaction)


class _ToggleButton(discord.ui.Button):
    def __init__(self, view: SetupView, key: str, description: str, current: bool):
        super().__init__(
            label=f"{description}: {'On' if current else 'Off'}",
            style=discord.ButtonStyle.success if current else discord.ButtonStyle.secondary,
        )
        self.setup_view = view
        self.key = key
        self.description_text = description

    @classmethod
    async def create(cls, view: SetupView, key: str, description: str, default: bool) -> "_ToggleButton":
        current = await view.cfg_bool(key, default)
        return cls(view, key, description, current)

    async def callback(self, interaction: discord.Interaction):
        currently_on = self.label.endswith("On")
        await self.setup_view.cog.bot.stores.config.set(
            self.setup_view.guild.id, self.key, str(not currently_on)
        )
        await self.setup_view.refresh(interaction)


class _ModalButton(discord.ui.Button):
    def __init__(self, label: str, modal_builder: Callable[[], Awaitable[discord.ui.Modal]]):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.modal_builder = modal_builder

    async def callback(self, interaction: discord.Interaction):
        modal = await self.modal_builder()
        await interaction.response.send_modal(modal)


async def _current_default(view: SetupView, key: str) -> list:
    """A select's default_values list for whatever is already stored at
    key, or [] if unset - discord.Object resolves to the right type
    (channel/role) automatically based on which select it's attached to,
    so the same helper covers both ChannelSelect and RoleSelect."""
    current_id = await view.cfg(key)
    return [discord.Object(id=int(current_id))] if current_id else []


class _LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: SetupView, default_values: list):
        super().__init__(
            placeholder="Log channel",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            default_values=default_values,
        )
        self.setup_view = view

    @classmethod
    async def create(cls, view: SetupView) -> "_LogChannelSelect":
        return cls(view, await _current_default(view, "logging.channel"))

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "logging.channel", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _AutoRoleSelect(discord.ui.RoleSelect):
    def __init__(self, view: SetupView, default_values: list):
        super().__init__(
            placeholder="Auto-role on join", min_values=0, max_values=1, default_values=default_values
        )
        self.setup_view = view

    @classmethod
    async def create(cls, view: SetupView) -> "_AutoRoleSelect":
        return cls(view, await _current_default(view, "roles.autorole"))

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "roles.autorole", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _BoredChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: SetupView, default_values: list):
        super().__init__(
            placeholder="Bored-nudge channel",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            default_values=default_values,
        )
        self.setup_view = view

    @classmethod
    async def create(cls, view: SetupView) -> "_BoredChannelSelect":
        return cls(view, await _current_default(view, "bored.channel"))

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "bored.channel", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _StarboardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: SetupView, default_values: list):
        super().__init__(
            placeholder="Starboard channel",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            default_values=default_values,
        )
        self.setup_view = view

    @classmethod
    async def create(cls, view: SetupView) -> "_StarboardChannelSelect":
        return cls(view, await _current_default(view, "starboard.channel"))

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "starboard.channel", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _TicketCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, view: SetupView, default_values: list):
        super().__init__(
            placeholder="Ticket channel category",
            channel_types=[discord.ChannelType.category],
            min_values=0,
            max_values=1,
            default_values=default_values,
        )
        self.setup_view = view

    @classmethod
    async def create(cls, view: SetupView) -> "_TicketCategorySelect":
        return cls(view, await _current_default(view, "tickets.category_id"))

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "tickets.category_id", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _WelcomeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: SetupView, default_values: list):
        super().__init__(
            placeholder="Welcome message channel",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            default_values=default_values,
        )
        self.setup_view = view

    @classmethod
    async def create(cls, view: SetupView) -> "_WelcomeChannelSelect":
        return cls(view, await _current_default(view, "welcome.channel_id"))

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "welcome.channel_id", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _LeaveChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: SetupView, default_values: list):
        super().__init__(
            placeholder="Leave message channel",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            default_values=default_values,
        )
        self.setup_view = view

    @classmethod
    async def create(cls, view: SetupView) -> "_LeaveChannelSelect":
        return cls(view, await _current_default(view, "leave.channel_id"))

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "leave.channel_id", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


# Accepted ranges for every numeric config key these modals write. They mirror
# the bounds the readers pass to ConfigStore.get_int, so a value accepted here
# is a value that will actually be honoured at read time - previously the modals
# checked int-ness only, so "-5" and "999999999" were stored happily and then
# silently disabled detection or broke the Discord call. (review F14)
_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "spam.max_messages": (1, 100),
    "spam.window_seconds": (1, 3600),
    "spam.max_duplicates": (2, 100),
    "spam.max_mentions": (1, 100),
    "spam.timeout_seconds": (1, 2419200),  # Discord's 28-day timeout cap
    "automod.caps_threshold": (0, 100),
    "automod.caps_minlen": (1, 2000),
    "bored.idle_seconds": (60, 86400),
    "raid.min_account_age_hours": (0, 8760),
    "raid.join_threshold": (0, 1000),
    "raid.join_window_seconds": (1, 3600),
    "antinuke.action_threshold": (1, 1000),
    "antinuke.window_seconds": (1, 3600),
    "starboard.threshold": (1, 100),
}


def _int_field_error(key: str, value: str) -> Optional[str]:
    """Returns the ephemeral "nothing was saved" message if `value` isn't a
    whole number inside `key`'s accepted range, else None. (review F14)"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return f"`{value}` isn't a whole number - nothing was saved."
    bounds = _INT_BOUNDS.get(key)
    if bounds is None:
        return None
    minimum, maximum = bounds
    if not minimum <= parsed <= maximum:
        return (
            f"`{value}` is out of range for `{key}` (accepted: {minimum}-{maximum}) "
            "- nothing was saved."
        )
    return None


class _PrefixModal(discord.ui.Modal, title="General settings"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.prefix = discord.ui.TextInput(label="Command prefix", max_length=5, required=True)
        self.add_item(self.prefix)

    async def on_submit(self, interaction: discord.Interaction):
        # Discord's required=True doesn't reject whitespace, and an empty prefix
        # makes every message in the guild a command parse. (review F13)
        prefix = (self.prefix.value or "").strip()
        if not prefix:
            await interaction.response.send_message(
                "The command prefix can't be empty or only spaces - nothing was saved.", ephemeral=True
            )
            return
        await self.setup_view.cog.bot.stores.config.set(
            self.setup_view.guild.id, "commandprefix", prefix
        )
        await self.setup_view.refresh(interaction)


async def _build_prefix_modal(view: SetupView) -> _PrefixModal:
    modal = _PrefixModal(view)
    modal.prefix.default = await view.cfg("commandprefix", "!")
    return modal


class _AntiSpamModal(discord.ui.Modal, title="Anti-spam thresholds"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.max_messages = discord.ui.TextInput(label="Max messages per window (default 5)", required=True)
        self.window_seconds = discord.ui.TextInput(label="Window (seconds) (default 6)", required=True)
        self.max_duplicates = discord.ui.TextInput(label="Max duplicate messages (default 3)", required=True)
        self.max_mentions = discord.ui.TextInput(label="Max mentions (default 5)", required=True)
        self.timeout_seconds = discord.ui.TextInput(
            label="Timeout duration (seconds) (default 300)", required=True
        )
        for item in (
            self.max_messages,
            self.window_seconds,
            self.max_duplicates,
            self.max_mentions,
            self.timeout_seconds,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        fields = {
            "spam.max_messages": self.max_messages.value,
            "spam.window_seconds": self.window_seconds.value,
            "spam.max_duplicates": self.max_duplicates.value,
            "spam.max_mentions": self.max_mentions.value,
            "spam.timeout_seconds": self.timeout_seconds.value,
        }
        for key, value in fields.items():
            error = _int_field_error(key, value)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return
        for key, value in fields.items():
            await self.setup_view.cog.bot.stores.config.set(self.setup_view.guild.id, key, value)
        await self.setup_view.refresh(interaction)


async def _build_antispam_modal(view: SetupView) -> _AntiSpamModal:
    modal = _AntiSpamModal(view)
    modal.max_messages.default = await view.cfg("spam.max_messages", "5")
    modal.window_seconds.default = await view.cfg("spam.window_seconds", "6")
    modal.max_duplicates.default = await view.cfg("spam.max_duplicates", "3")
    modal.max_mentions.default = await view.cfg("spam.max_mentions", "5")
    modal.timeout_seconds.default = await view.cfg("spam.timeout_seconds", "300")
    return modal


class _AutomodModal(discord.ui.Modal, title="Automod thresholds"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.caps_threshold = discord.ui.TextInput(
            label="Caps % threshold (0 disables, def 70)", required=True
        )
        self.caps_minlen = discord.ui.TextInput(label="Caps min message length (default 10)", required=True)
        self.add_item(self.caps_threshold)
        self.add_item(self.caps_minlen)

    async def on_submit(self, interaction: discord.Interaction):
        fields = {
            "automod.caps_threshold": self.caps_threshold.value,
            "automod.caps_minlen": self.caps_minlen.value,
        }
        for key, value in fields.items():
            error = _int_field_error(key, value)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return
        for key, value in fields.items():
            await self.setup_view.cog.bot.stores.config.set(self.setup_view.guild.id, key, value)
        await self.setup_view.refresh(interaction)


async def _build_automod_modal(view: SetupView) -> _AutomodModal:
    modal = _AutomodModal(view)
    modal.caps_threshold.default = await view.cfg("automod.caps_threshold", "70")
    modal.caps_minlen.default = await view.cfg("automod.caps_minlen", "10")
    return modal


class _BoredModal(discord.ui.Modal, title="Bored detector"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.idle_seconds = discord.ui.TextInput(
            label="Idle seconds before nudge (default 1800)", required=True
        )
        self.message = discord.ui.TextInput(
            label="Nudge message", required=True, style=discord.TextStyle.long
        )
        self.add_item(self.idle_seconds)
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):
        error = _int_field_error("bored.idle_seconds", self.idle_seconds.value)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await self.setup_view.cog.bot.stores.config.set(
            self.setup_view.guild.id, "bored.idle_seconds", self.idle_seconds.value
        )
        await self.setup_view.cog.bot.stores.config.set(
            self.setup_view.guild.id, "bored.message", self.message.value
        )
        await self.setup_view.refresh(interaction)


async def _build_bored_modal(view: SetupView) -> _BoredModal:
    modal = _BoredModal(view)
    modal.idle_seconds.default = await view.cfg("bored.idle_seconds", "1800")
    modal.message.default = await view.cfg("bored.message", _BORED_DEFAULT)
    return modal


class _RaidModal(discord.ui.Modal, title="Raid protection"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.min_account_age_hours = discord.ui.TextInput(
            label="Min account age hours (0 disables)", required=True
        )
        self.join_threshold = discord.ui.TextInput(label="Join burst size (0 disables)", required=True)
        self.join_window_seconds = discord.ui.TextInput(
            label="Join burst window (seconds, def 30)", required=True
        )
        self.add_item(self.min_account_age_hours)
        self.add_item(self.join_threshold)
        self.add_item(self.join_window_seconds)

    async def on_submit(self, interaction: discord.Interaction):
        fields = {
            "raid.min_account_age_hours": self.min_account_age_hours.value,
            "raid.join_threshold": self.join_threshold.value,
            "raid.join_window_seconds": self.join_window_seconds.value,
        }
        for key, value in fields.items():
            error = _int_field_error(key, value)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return
        for key, value in fields.items():
            await self.setup_view.cog.bot.stores.config.set(self.setup_view.guild.id, key, value)
        await self.setup_view.refresh(interaction)


async def _build_raid_modal(view: SetupView) -> _RaidModal:
    modal = _RaidModal(view)
    modal.min_account_age_hours.default = await view.cfg("raid.min_account_age_hours", "0")
    modal.join_threshold.default = await view.cfg("raid.join_threshold", "0")
    modal.join_window_seconds.default = await view.cfg("raid.join_window_seconds", "30")
    return modal


class _AntiNukeModal(discord.ui.Modal, title="Anti-nuke thresholds"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.action_threshold = discord.ui.TextInput(
            label="Actions before punishing (default 3)", required=True
        )
        self.window_seconds = discord.ui.TextInput(label="Window (seconds) (default 30)", required=True)
        self.add_item(self.action_threshold)
        self.add_item(self.window_seconds)

    async def on_submit(self, interaction: discord.Interaction):
        fields = {
            "antinuke.action_threshold": self.action_threshold.value,
            "antinuke.window_seconds": self.window_seconds.value,
        }
        for key, value in fields.items():
            error = _int_field_error(key, value)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return
        for key, value in fields.items():
            await self.setup_view.cog.bot.stores.config.set(self.setup_view.guild.id, key, value)
        await self.setup_view.refresh(interaction)


async def _build_antinuke_modal(view: SetupView) -> _AntiNukeModal:
    modal = _AntiNukeModal(view)
    modal.action_threshold.default = await view.cfg("antinuke.action_threshold", "3")
    modal.window_seconds.default = await view.cfg("antinuke.window_seconds", "30")
    return modal


class _StarboardModal(discord.ui.Modal, title="Starboard"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.threshold = discord.ui.TextInput(label="Stars needed to post (default 3)", required=True)
        self.add_item(self.threshold)

    async def on_submit(self, interaction: discord.Interaction):
        error = _int_field_error("starboard.threshold", self.threshold.value)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await self.setup_view.cog.bot.stores.config.set(
            self.setup_view.guild.id, "starboard.threshold", self.threshold.value
        )
        await self.setup_view.refresh(interaction)


async def _build_starboard_modal(view: SetupView) -> _StarboardModal:
    modal = _StarboardModal(view)
    modal.threshold.default = await view.cfg("starboard.threshold", "3")
    return modal


class _GreetingsModal(discord.ui.Modal, title="Welcome/leave messages"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.welcome_message = discord.ui.TextInput(
            label="Welcome message", required=True, style=discord.TextStyle.long
        )
        self.leave_message = discord.ui.TextInput(
            label="Leave message", required=True, style=discord.TextStyle.long
        )
        self.add_item(self.welcome_message)
        self.add_item(self.leave_message)

    async def on_submit(self, interaction: discord.Interaction):
        await self.setup_view.cog.bot.stores.config.set(
            self.setup_view.guild.id, "welcome.message", self.welcome_message.value
        )
        await self.setup_view.cog.bot.stores.config.set(
            self.setup_view.guild.id, "leave.message", self.leave_message.value
        )
        await self.setup_view.refresh(interaction)


async def _build_greetings_modal(view: SetupView) -> _GreetingsModal:
    modal = _GreetingsModal(view)
    modal.welcome_message.default = await view.cfg("welcome.message", _WELCOME_DEFAULT)
    modal.leave_message.default = await view.cfg("leave.message", _LEAVE_DEFAULT)
    return modal


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def moderation_cog(self):
        return self.bot.get_cog("Moderation")

    @commands.hybrid_command(name="setup", description="Configure this bot for your server (mod-only)")
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def setup_cmd(self, ctx: commands.Context):
        view = SetupView(self, ctx.guild, ctx.author.id)
        await view.start(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
