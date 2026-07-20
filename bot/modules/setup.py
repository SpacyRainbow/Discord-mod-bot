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

STEP_GENERAL = "general"
STEP_LOGGING = "logging"
STEP_MODERATION = "moderation"
STEP_ANTISPAM = "antispam"
STEP_AUTOMOD = "automod"
STEP_ROLES = "roles"
STEP_BORED = "bored"
STEP_MUSIC = "music"
STEP_SUMMARY = "summary"

STEPS = [
    STEP_GENERAL,
    STEP_LOGGING,
    STEP_MODERATION,
    STEP_ANTISPAM,
    STEP_AUTOMOD,
    STEP_ROLES,
    STEP_BORED,
    STEP_MUSIC,
    STEP_SUMMARY,
]

STEP_TITLES = {
    STEP_GENERAL: "General",
    STEP_LOGGING: "Logging",
    STEP_MODERATION: "Moderation",
    STEP_ANTISPAM: "Anti-spam",
    STEP_AUTOMOD: "Automod",
    STEP_ROLES: "Roles",
    STEP_BORED: "Bored detector",
    STEP_MUSIC: "Music",
    STEP_SUMMARY: "Summary",
}

# (key, default, description) - the machine-readable twin of the README's
# "Config reference" table, used to build the final summary step.
CONFIG_MANIFEST = [
    ("commandprefix", "!", "Command prefix"),
    ("logging.channel", "unset", "Log channel"),
    ("logging.edits", "true", "Log message edits"),
    ("logging.deletes", "true", "Log message deletes"),
    ("logging.joins", "true", "Log joins/leaves"),
    ("moderation.mute_role", "unset", "Mute role"),
    ("spam.max_messages", "5", "Messages allowed per window"),
    ("spam.window_seconds", "6", "Rolling window (seconds)"),
    ("spam.max_duplicates", "3", "Duplicate messages allowed"),
    ("spam.max_mentions", "5", "Mentions before flagged"),
    ("spam.timeout_seconds", "300", "Timeout duration (seconds)"),
    ("automod.block_invites", "true", "Block invite links"),
    ("automod.caps_threshold", "70", "Caps % threshold"),
    ("automod.caps_minlen", "10", "Caps min message length"),
    ("roles.autorole", "unset", "Auto-role on join"),
    ("bored.channel", "unset", "Bored-nudge channel"),
    ("bored.idle_seconds", "1800", "Idle seconds before nudge"),
    ("bored.message", "...it's quiet in here. Too quiet.", "Nudge message"),
    ("music.sponsorblock_enabled", "true", "SponsorBlock auto-skip"),
]


def build_summary_lines(values: dict) -> list[str]:
    """Pure: given a {key: stored_value_or_None} mapping, builds one display
    line per manifest entry - the text /getconfig would show for each key,
    all at once."""
    lines = []
    for key, default, description in CONFIG_MANIFEST:
        value = values.get(key)
        shown = value if value is not None else f"{default} (default)"
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
            self.add_item(_LogChannelSelect(self))
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
        elif step == STEP_ROLES:
            self.add_item(_AutoRoleSelect(self))
        elif step == STEP_BORED:
            self.add_item(_BoredChannelSelect(self))
            self.add_item(_ModalButton("Edit bored settings", lambda: _build_bored_modal(self)))
        elif step == STEP_MUSIC:
            self.add_item(
                await _ToggleButton.create(self, "music.sponsorblock_enabled", "SponsorBlock auto-skip", True)
            )

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
            prefix = await self.cfg("commandprefix", "!")
            embed.description = f"**Command prefix**: `{prefix}`"
        elif step == STEP_LOGGING:
            channel_id = await self.cfg("logging.channel")
            channel_text = f"<#{channel_id}>" if channel_id else "not set"
            edits = await self.cfg_bool("logging.edits", True)
            deletes = await self.cfg_bool("logging.deletes", True)
            joins = await self.cfg_bool("logging.joins", True)
            embed.description = (
                f"**Log channel**: {channel_text}\n"
                f"**Log edits**: {edits}\n**Log deletes**: {deletes}\n**Log joins/leaves**: {joins}"
            )
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
            embed.description = "\n".join(await self._manifest_lines("automod."))
        elif step == STEP_ROLES:
            role_id = await self.cfg("roles.autorole")
            embed.description = f"**Auto-role on join**: {f'<@&{role_id}>' if role_id else 'not set'}"
        elif step == STEP_BORED:
            embed.description = "\n".join(await self._manifest_lines("bored."))
        elif step == STEP_MUSIC:
            enabled = await self.cfg_bool("music.sponsorblock_enabled", True)
            embed.description = f"**SponsorBlock auto-skip**: {enabled}"
        elif step == STEP_SUMMARY:
            values = {key: await self.cfg(key) for key, _, _ in CONFIG_MANIFEST}
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
        for key, default, description in CONFIG_MANIFEST:
            if not key.startswith(key_prefix):
                continue
            value = await self.cfg(key)
            lines.append(f"**{description}**: {value if value is not None else f'{default} (default)'}")
        return lines


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


class _LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: SetupView):
        super().__init__(
            placeholder="Log channel", channel_types=[discord.ChannelType.text], min_values=0, max_values=1
        )
        self.setup_view = view

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "logging.channel", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _AutoRoleSelect(discord.ui.RoleSelect):
    def __init__(self, view: SetupView):
        super().__init__(placeholder="Auto-role on join", min_values=0, max_values=1)
        self.setup_view = view

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "roles.autorole", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _BoredChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: SetupView):
        super().__init__(
            placeholder="Bored-nudge channel",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
        )
        self.setup_view = view

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            await self.setup_view.cog.bot.stores.config.set(
                self.setup_view.guild.id, "bored.channel", str(self.values[0].id)
            )
        await self.setup_view.refresh(interaction)


class _PrefixModal(discord.ui.Modal, title="General settings"):
    def __init__(self, view: SetupView):
        super().__init__()
        self.setup_view = view
        self.prefix = discord.ui.TextInput(label="Command prefix", max_length=5, required=True)
        self.add_item(self.prefix)

    async def on_submit(self, interaction: discord.Interaction):
        await self.setup_view.cog.bot.stores.config.set(
            self.setup_view.guild.id, "commandprefix", self.prefix.value
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
        self.max_messages = discord.ui.TextInput(label="Max messages per window", required=True)
        self.window_seconds = discord.ui.TextInput(label="Window (seconds)", required=True)
        self.max_duplicates = discord.ui.TextInput(label="Max duplicate messages", required=True)
        self.max_mentions = discord.ui.TextInput(label="Max mentions", required=True)
        self.timeout_seconds = discord.ui.TextInput(label="Timeout duration (seconds)", required=True)
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
            try:
                int(value)
            except ValueError:
                await interaction.response.send_message(
                    f"`{value}` isn't a whole number - nothing was saved.", ephemeral=True
                )
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
        self.caps_threshold = discord.ui.TextInput(label="Caps % threshold (0 disables)", required=True)
        self.caps_minlen = discord.ui.TextInput(label="Caps min message length", required=True)
        self.add_item(self.caps_threshold)
        self.add_item(self.caps_minlen)

    async def on_submit(self, interaction: discord.Interaction):
        fields = {
            "automod.caps_threshold": self.caps_threshold.value,
            "automod.caps_minlen": self.caps_minlen.value,
        }
        for value in fields.values():
            try:
                int(value)
            except ValueError:
                await interaction.response.send_message(
                    f"`{value}` isn't a whole number - nothing was saved.", ephemeral=True
                )
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
        self.idle_seconds = discord.ui.TextInput(label="Idle seconds before nudge", required=True)
        self.message = discord.ui.TextInput(
            label="Nudge message", required=True, style=discord.TextStyle.long
        )
        self.add_item(self.idle_seconds)
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            int(self.idle_seconds.value)
        except ValueError:
            await interaction.response.send_message(
                f"`{self.idle_seconds.value}` isn't a whole number - nothing was saved.", ephemeral=True
            )
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
    modal.message.default = await view.cfg("bored.message", "...it's quiet in here. Too quiet.")
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
