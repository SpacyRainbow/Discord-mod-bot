"""
minecraft - status and mod-only control for Minecraft servers managed by
Crafty Controller 4 (https://gitlab.com/crafty-controller/crafty-4), all
under one command: /mcstatus. Everything past the initial status view
(start/stop/restart, console, whitelist) is reached through buttons and
modals on that one command's response, not separate slash commands - kept
to one entry in the / command list on purpose.

Bot-wide config, read from the environment - not per-guild, for the same
reason Spotify's credentials in music.py aren't per-guild:
  CRAFTY_BASE_URL     - e.g. https://192.168.1.50:8443
  CRAFTY_API_TOKEN    - Bearer token, from Crafty's web UI: gear icon ->
                        your user -> pencil/edit icon -> API key tab. This
                        token also needs Crafty's own COMMANDS permission
                        (a per-server, Crafty-side permission, separate
                        from anything in Discord) on a server for the
                        action/console/whitelist buttons to work against
                        it - Crafty replies with its own "not authorized"
                        error otherwise.
  CRAFTY_CA_BUNDLE    - optional; path to the certificate (or internal CA)
                        Crafty's HTTPS listener presents. Self-hosted
                        Crafty usually runs a self-signed cert, which the
                        system trust store won't accept - point this at it
                        rather than turning verification off, because this
                        connection carries CRAFTY_API_TOKEN, which has full
                        Minecraft console access.
  CRAFTY_INSECURE_TLS - optional; 1/true/yes disables certificate
                        verification entirely. Last resort only - it
                        exposes CRAFTY_API_TOKEN to anyone on the network
                        path. Logs a warning every time it's used.
  CRAFTY_SERVER_ID    - optional; skips server discovery entirely and
                        always targets this one server ID. Without it,
                        /mcstatus looks servers up by name (or auto-picks
                        if there's only one) via GET /api/v2/servers/.
  MINECRAFT_GUILD_ID  - optional; restricts /mcstatus to this one Discord
                        guild ID. Falls back to GUILD_ID (already used
                        for slash-command sync) if unset, and to no
                        restriction at all if neither is set - so a fresh
                        fork of this bot gets /mcstatus working in
                        whatever server it's added to, by default.

Confirmed against a real Crafty4 instance (not just the API source) that:
  - stats fields (running/online/max/version/world_name) are top-level in
    the stats response's "data", exactly as expected from reading the
    source - EXCEPT server_name, which is nested under data["server_id"]
    ["server_name"] in the stats payload (the "server_id" key there is
    confusingly a whole embedded server-config object, not just an ID
    string) - so the display name is threaded through from the /servers/
    listing instead of re-extracted from stats.
  - Crafty represents "no value yet" for some fields (version, desc) as
    the literal *string* "False", not JSON false/null - _clean() below
    treats that the same as missing so the embed doesn't show "Version:
    False" for a server that's never been started.
  - POST /servers/{id}/action/{name}/ : "start_server" is confirmed
    working (live-tested against an offline server with explicit sign-off
    - it genuinely booted the server before failing on an unrelated port
    conflict, then settled back to a clean stopped state). "stop_server"
    and "restart_server" follow the same {verb}_server naming used by
    Crafty's own source but aren't independently live-tested here; if
    either name is wrong, Crafty rejects the request and that surfaces as
    a normal, non-crashing CraftyUnavailableError message.
  - POST /servers/{id}/stdin/ : the request body is the raw command text
    itself, not JSON. A server that isn't running replies with
    {"status": "error", "error": "SERVER_NOT_RUNNING"}.
  - GET /servers/{id}/logs/ : returns {"status": "ok", "data": [<lines>]},
    no line-count parameter - used for a best-effort "recent output"
    readout after the console/whitelist modals, not a guaranteed exact
    capture of one command's output (Minecraft console output is
    asynchronous, not a synchronous request/response).
  - There is no on-demand backup trigger and no whitelist API in Crafty
    4's REST API at all (confirmed by reading every backup/whitelist
    related route, and an open, unimplemented GitLab feature request for
    the latter) - the whitelist button is implemented via the vanilla
    Minecraft `whitelist add/remove/list` console commands instead,
    through the same stdin endpoint, and only works while the target
    server is running.

The Console and Whitelist buttons give Discord mods real, arbitrary
Minecraft console access (anything from `whitelist add` to `/op` to
`/stop`) - both are gated at manage_guild, the same trust tier as /setup,
not something lower.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from collections import Counter
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

logger = logging.getLogger("bot.modules.minecraft")

API_BASE_PATH = "/api/v2"
REQUEST_TIMEOUT_SECONDS = 10
CONSOLE_OUTPUT_DELAY_SECONDS = 1.5
CONSOLE_OUTPUT_POLL_ATTEMPTS = 3
LOG_COMPARISON_WINDOW = 40
RECENT_LOG_LINES = 5
ACTION_REFRESH_POLL_ATTEMPTS = 3
ACTION_REFRESH_POLL_INTERVAL_SECONDS = 1.5
# Crafty's own placeholder for "not set" on some fields - see module docstring.
_PLACEHOLDER_VALUES = {None, "", "False", "None"}


class CraftyUnavailableError(Exception):
    """Raised for any Crafty API problem - not configured, unreachable, a
    non-200 response, or an unexpected body - so the command can give one
    consistent, clear message instead of an uncaught exception."""


class ServerNotRunningError(CraftyUnavailableError):
    """Crafty's stdin endpoint reported the target server isn't running."""


class MinecraftGuildRestrictedError(commands.CheckFailure):
    """Raised by minecraft_guild_only() when /mcstatus is used outside the
    one guild it's configured to be restricted to."""


def minecraft_guild_only():
    """Restricts a command to one Discord guild, configured via
    MINECRAFT_GUILD_ID (falling back to GUILD_ID, already used for slash
    command sync). With neither set, this is a no-op - a fork of this repo
    run by someone else gets /mcstatus working unrestricted in whatever
    server they add it to, by default. Nothing about a specific guild ID
    is hardcoded here."""

    async def predicate(ctx: commands.Context) -> bool:
        restricted_id = os.getenv("MINECRAFT_GUILD_ID") or os.getenv("GUILD_ID")
        if not restricted_id:
            return True
        if ctx.guild is None or str(ctx.guild.id) != restricted_id:
            raise MinecraftGuildRestrictedError("/mcstatus isn't available in this server.")
        return True

    return commands.check(predicate)


def _clean(value):
    """None if value is missing or one of Crafty's own placeholder strings
    for "not set", otherwise the value unchanged."""
    return None if value in _PLACEHOLDER_VALUES else value


def build_status_embed(stats: dict, server_name: Optional[str] = None) -> discord.Embed:
    """Pure: builds a server status embed from a Crafty stats payload. Every
    field is read with .get() and cleaned of Crafty's placeholder values,
    and only shown if actually present."""
    running = stats.get("running")
    if running is True:
        status_text, color = "Online", discord.Color.green()
    elif running is False:
        status_text, color = "Offline", discord.Color.red()
    else:
        status_text, color = "Unknown", discord.Color.greyple()

    title = server_name or _clean(stats.get("server_id", {}).get("server_name")) or "Minecraft server"
    embed = discord.Embed(title=title, description=f"**Status:** {status_text}", color=color)

    online, max_players = stats.get("online"), stats.get("max")
    if online is not None or max_players is not None:
        online_text = online if online is not None else "?"
        max_text = max_players if max_players is not None else "?"
        embed.add_field(name="Players", value=f"{online_text}/{max_text}")

    version = _clean(stats.get("version"))
    if version:
        embed.add_field(name="Version", value=str(version))

    world_name = _clean(stats.get("world_name"))
    if world_name:
        embed.add_field(name="World", value=str(world_name))

    return embed


def sort_servers(entries: list[dict]) -> list[dict]:
    """Pure: sorts server summary dicts (server_id/server_name/running/
    online) by player count descending, then online before offline, then
    alphabetically by name - the ordering requested for /mc status's
    server-list buttons."""

    def key(entry: dict):
        online = entry.get("online") or 0
        running_rank = 0 if entry.get("running") else 1
        name = (entry.get("server_name") or "").lower()
        return (-online, running_rank, name)

    return sorted(entries, key=key)


class ConsoleModal(discord.ui.Modal, title="Send console command"):
    def __init__(self, cog: "Minecraft", server_id: str, server_name: Optional[str]):
        super().__init__()
        self.cog = cog
        self.server_id = server_id
        self.server_name = server_name
        self.command_input = discord.ui.TextInput(
            label="Command", placeholder="say hello", max_length=200
        )
        self.add_item(self.command_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog._console_and_reply(
            interaction, self.server_id, self.server_name, str(self.command_input.value)
        )


class WhitelistModal(discord.ui.Modal, title="Manage whitelist"):
    def __init__(self, cog: "Minecraft", server_id: str, server_name: Optional[str]):
        super().__init__()
        self.cog = cog
        self.server_id = server_id
        self.server_name = server_name
        self.action_input = discord.ui.TextInput(label="Action: add, remove, or list", max_length=10)
        self.player_input = discord.ui.TextInput(
            label="Player name (blank for list)", required=False, max_length=32
        )
        self.add_item(self.action_input)
        self.add_item(self.player_input)

    async def on_submit(self, interaction: discord.Interaction):
        action = str(self.action_input.value).strip().lower()
        player = str(self.player_input.value).strip() or None
        if action not in ("add", "remove", "list"):
            await interaction.response.send_message(
                "Action must be `add`, `remove`, or `list`.", ephemeral=True
            )
            return
        if action != "list" and not player:
            await interaction.response.send_message(
                "A player name is required for `add`/`remove`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        command = f"whitelist {action}" + (f" {player}" if player else "")
        await self.cog._console_and_reply(interaction, self.server_id, self.server_name, command)


class ServerControlView(discord.ui.View):
    """One server's detail embed and every control for it: Start/Stop/
    Restart buttons, Console and Whitelist buttons (each opening a modal
    for the text input a slash command option would otherwise need), and
    an optional button back to the full server list. Public - anyone can
    view it and use Back, but the action/console/whitelist buttons
    re-check manage_guild on whoever actually clicks, since buttons
    persist on the message for anyone to see."""

    def __init__(
        self,
        cog: "Minecraft",
        server_id: str,
        server_name: Optional[str],
        running: bool,
        show_back: bool,
        *,
        timeout: float = 180.0,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.server_id = server_id
        self.server_name = server_name
        self.show_back = show_back
        self.start_button.disabled = running
        self.stop_button.disabled = not running
        self.restart_button.disabled = not running
        if not show_back:
            self.remove_item(self.back_button)

    async def _require_mod(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.manage_guild:
            await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
            return False
        return True

    async def _run_action(self, interaction: discord.Interaction, action_name: str, verb: str):
        if not await self._require_mod(interaction):
            return
        previous_running = self.start_button.disabled
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.send_action(self.server_id, action_name)
        except CraftyUnavailableError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await interaction.followup.send(f"{verb} **{self.server_name or 'the server'}**.", ephemeral=True)
        await self._refresh_status_message(interaction, previous_running)

    async def _refresh_status_message(self, interaction: discord.Interaction, previous_running: bool) -> None:
        """Re-fetches stats and edits the underlying message so Start/Stop/
        Restart reflect the new state - without this, a message's buttons
        stay frozen at whatever running state the view was built with (e.g.
        Start staying disabled forever after a Stop click). Polls a few
        times since Crafty can take a moment to reflect a state change."""
        stats: dict = {}
        for _ in range(ACTION_REFRESH_POLL_ATTEMPTS):
            await asyncio.sleep(ACTION_REFRESH_POLL_INTERVAL_SECONDS)
            try:
                stats = await self.cog._request(f"{API_BASE_PATH}/servers/{self.server_id}/stats/") or {}
            except CraftyUnavailableError:
                continue
            if stats.get("running") is not previous_running:
                break
        embed = build_status_embed(stats, server_name=self.server_name)
        view = ServerControlView(
            self.cog, self.server_id, self.server_name, stats.get("running") is True, self.show_back
        )
        try:
            await interaction.message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Start", style=discord.ButtonStyle.green, row=0)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run_action(interaction, "start_server", "Starting")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.red, row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run_action(interaction, "stop_server", "Stopping")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.grey, row=0)
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run_action(interaction, "restart_server", "Restarting")

    @discord.ui.button(label="Console", style=discord.ButtonStyle.blurple, row=1)
    async def console_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_mod(interaction):
            return
        await interaction.response.send_modal(ConsoleModal(self.cog, self.server_id, self.server_name))

    @discord.ui.button(label="Whitelist", style=discord.ButtonStyle.blurple, row=1)
    async def whitelist_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._require_mod(interaction):
            return
        await interaction.response.send_modal(WhitelistModal(self.cog, self.server_id, self.server_name))

    @discord.ui.button(label="Back to list", style=discord.ButtonStyle.secondary, row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            entries = await self.cog._gather_status_entries()
        except CraftyUnavailableError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        embed = self.cog._build_list_embed(entries)
        view = ServerListView(self.cog, entries)
        await interaction.edit_original_response(embed=embed, view=view)


class ServerListView(discord.ui.View):
    """One button per server, sorted via sort_servers(); clicking a button
    swaps the message to that server's detail/control view."""

    def __init__(self, cog: "Minecraft", entries: list[dict], *, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.cog = cog
        for entry in entries:
            running = entry.get("running")
            style = discord.ButtonStyle.green if running else discord.ButtonStyle.grey
            name = entry.get("server_name") or "?"
            if running:
                label = f"{name} ({entry.get('online') or 0} online)"
            else:
                label = f"{name} (offline)"
            button = discord.ui.Button(label=label[:80], style=style)
            button.callback = self._make_callback(entry.get("server_id"), name)
            self.add_item(button)

    def _make_callback(self, server_id: Optional[str], server_name: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            try:
                stats = await self.cog._request(f"{API_BASE_PATH}/servers/{server_id}/stats/") or {}
            except CraftyUnavailableError as e:
                await interaction.followup.send(str(e), ephemeral=True)
                return
            embed = build_status_embed(stats, server_name=server_name)
            view = ServerControlView(
                self.cog, server_id, server_name, stats.get("running") is True, show_back=True
            )
            await interaction.edit_original_response(embed=embed, view=view)

        return callback


class Minecraft(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        # Built once at cog_load and reused by _request/_post - see _ssl_context.
        self.ssl_context = None

    def _ssl_context(self):
        """Verify Crafty's certificate. Self-hosted Crafty usually runs a self-signed
        cert, so point CRAFTY_CA_BUNDLE at it (or at your internal CA) rather than
        disabling verification - this connection carries a token with full Minecraft
        console access. (review F8)"""
        if os.getenv("CRAFTY_INSECURE_TLS", "").lower() in ("1", "true", "yes"):
            logger.warning(
                "CRAFTY_INSECURE_TLS is set - Crafty's certificate is NOT verified "
                "and CRAFTY_API_TOKEN is exposed to anyone on the network path."
            )
            return False
        ca_bundle = os.getenv("CRAFTY_CA_BUNDLE")
        if ca_bundle:
            return ssl.create_default_context(cafile=ca_bundle)
        return None  # aiohttp default: verify against the system trust store

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()
        self.ssl_context = self._ssl_context()

    async def cog_unload(self) -> None:
        if self.session is not None:
            await self.session.close()

    def _base_url(self) -> Optional[str]:
        url = os.getenv("CRAFTY_BASE_URL")
        return url.rstrip("/") if url else None

    async def _request(self, path: str):
        base_url = self._base_url()
        token = os.getenv("CRAFTY_API_TOKEN")
        if not base_url or not token:
            raise CraftyUnavailableError(
                "Crafty isn't configured (missing CRAFTY_BASE_URL/CRAFTY_API_TOKEN)."
            )
        if self.session is None:
            raise CraftyUnavailableError("Crafty isn't available right now.")

        try:
            async with self.session.get(
                f"{base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                ssl=self.ssl_context,  # review F8 - see _ssl_context
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    raise CraftyUnavailableError(
                        f"Crafty rejected the request (HTTP {resp.status}) - check CRAFTY_API_TOKEN."
                    )
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise CraftyUnavailableError(f"Couldn't reach Crafty at {base_url}.") from e
        except ValueError as e:
            raise CraftyUnavailableError("Crafty returned an unexpected response.") from e

        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise CraftyUnavailableError("Crafty returned an error response.")
        return payload.get("data")

    async def _post(self, path: str, *, data: Optional[str] = None) -> Optional[dict]:
        base_url = self._base_url()
        token = os.getenv("CRAFTY_API_TOKEN")
        if not base_url or not token:
            raise CraftyUnavailableError(
                "Crafty isn't configured (missing CRAFTY_BASE_URL/CRAFTY_API_TOKEN)."
            )
        if self.session is None:
            raise CraftyUnavailableError("Crafty isn't available right now.")

        try:
            async with self.session.post(
                f"{base_url}{path}",
                data=data,
                headers={"Authorization": f"Bearer {token}"},
                ssl=self.ssl_context,  # review F8 - see _ssl_context
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    raise CraftyUnavailableError(f"Crafty rejected the request (HTTP {resp.status}).")
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise CraftyUnavailableError(f"Couldn't reach Crafty at {base_url}.") from e
        except ValueError as e:
            raise CraftyUnavailableError("Crafty returned an unexpected response.") from e

        if not isinstance(payload, dict) or payload.get("status") != "ok":
            error = payload.get("error") if isinstance(payload, dict) else None
            if error == "SERVER_NOT_RUNNING":
                raise ServerNotRunningError("That server isn't running.")
            raise CraftyUnavailableError(f"Crafty returned an error{f': {error}' if error else ''}.")
        return payload

    async def send_action(self, server_id: str, action_name: str) -> None:
        await self._post(f"{API_BASE_PATH}/servers/{server_id}/action/{action_name}/")

    async def send_console_command(self, server_id: str, text: str) -> None:
        await self._post(f"{API_BASE_PATH}/servers/{server_id}/stdin/", data=text)

    async def fetch_recent_logs(self, server_id: str, limit: int = RECENT_LOG_LINES) -> list[str]:
        lines = await self._request(f"{API_BASE_PATH}/servers/{server_id}/logs/")
        return lines[-limit:] if isinstance(lines, list) else []

    async def _list_servers(self) -> list:
        servers = await self._request(f"{API_BASE_PATH}/servers/")
        return servers or []

    async def _resolve_server(self, name: Optional[str]) -> tuple:
        """Returns (server_id, server_name). Raises CraftyUnavailableError
        with a message listing the actual available server names when the
        selection is ambiguous or not found."""
        override = os.getenv("CRAFTY_SERVER_ID")
        if override and not name:
            return override, None

        servers = await self._list_servers()
        if not servers:
            raise CraftyUnavailableError("No servers are visible to this Crafty API token.")

        if name:
            matches = [s for s in servers if name.lower() in (s.get("server_name") or "").lower()]
            if not matches:
                available = ", ".join(s.get("server_name", "?") for s in servers)
                raise CraftyUnavailableError(f"No server matching '{name}'. Available: {available}.")
            if len(matches) > 1:
                available = ", ".join(s.get("server_name", "?") for s in matches)
                raise CraftyUnavailableError(f"'{name}' matches more than one server: {available}.")
            match = matches[0]
            return match.get("server_id"), match.get("server_name")

        if len(servers) > 1:
            available = ", ".join(s.get("server_name", "?") for s in servers)
            raise CraftyUnavailableError(
                f"More than one server is visible ({available}) - use a server name to pick one."
            )

        only = servers[0]
        return only.get("server_id"), only.get("server_name")

    async def _gather_status_entries(self) -> list[dict]:
        servers = await self._list_servers()
        entries = []
        for server in servers:
            server_id = server.get("server_id")
            server_name = server.get("server_name")
            try:
                stats = await self._request(f"{API_BASE_PATH}/servers/{server_id}/stats/") or {}
            except CraftyUnavailableError:
                stats = {}
            entries.append(
                {
                    "server_id": server_id,
                    "server_name": server_name,
                    "running": stats.get("running"),
                    "online": stats.get("online"),
                    "stats": stats,
                }
            )
        return sort_servers(entries)

    def _build_list_embed(self, entries: list[dict]) -> discord.Embed:
        embed = discord.Embed(title="Minecraft servers", color=discord.Color.blurple())
        if not entries:
            embed.description = "No servers found."
            return embed
        lines = []
        for entry in entries:
            if entry.get("running"):
                online = entry.get("online")
                status = f"\N{LARGE GREEN CIRCLE} Online ({online} playing)" if online is not None else \
                    "\N{LARGE GREEN CIRCLE} Online"
            else:
                status = "\N{LARGE RED CIRCLE} Offline"
            lines.append(f"**{entry.get('server_name') or '?'}** - {status}")
        embed.description = "\n".join(lines)
        return embed

    async def _console_and_reply(
        self,
        interaction: discord.Interaction,
        server_id: str,
        server_name: Optional[str],
        command: str,
    ) -> None:
        """Sends a console command and replies on the given interaction -
        shared by the Console and Whitelist modals, called after
        interaction.response.defer(ephemeral=True) so this can just use
        followup.send throughout. Snapshots the log before sending and
        polls afterward, diffing against that snapshot, so "recent output"
        is actually the new lines the command produced rather than
        whatever 5 lines happened to already be there - the log can be
        busy enough (or slow enough to update) that a single fixed-delay
        read after sending often just re-showed stale output."""
        try:
            before = await self.fetch_recent_logs(server_id, limit=LOG_COMPARISON_WINDOW)
        except CraftyUnavailableError:
            before = []
        try:
            await self.send_console_command(server_id, command)
        except CraftyUnavailableError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        after = before
        for _ in range(CONSOLE_OUTPUT_POLL_ATTEMPTS):
            await asyncio.sleep(CONSOLE_OUTPUT_DELAY_SECONDS)
            try:
                after = await self.fetch_recent_logs(server_id, limit=LOG_COMPARISON_WINDOW)
            except CraftyUnavailableError:
                continue
            if after != before:
                break

        remaining = Counter(before)
        new_lines = []
        for line in after:
            if remaining.get(line, 0) > 0:
                remaining[line] -= 1
            else:
                new_lines.append(line)
        lines = (new_lines or after)[-RECENT_LOG_LINES:]
        output = "\n".join(lines) if lines else "(no recent output)"
        embed = discord.Embed(
            title=f"Sent to {server_name or 'the server'}",
            description=f"```\n{command}\n```\n**Recent output:**\n```\n{output[-1500:]}\n```",
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="mcstatus",
        description="Show Minecraft server status (mods get start/stop/console/whitelist buttons)",
    )
    @commands.guild_only()
    @minecraft_guild_only()
    async def mcstatus(self, ctx: commands.Context, *, server: Optional[str] = None):
        await ctx.defer()
        try:
            if server:
                server_id, server_name = await self._resolve_server(server)
                stats = await self._request(f"{API_BASE_PATH}/servers/{server_id}/stats/") or {}
                embed = build_status_embed(stats, server_name=server_name)
                view = ServerControlView(
                    self, server_id, server_name, stats.get("running") is True, show_back=True
                )
                await ctx.send(embed=embed, view=view)
                return

            entries = await self._gather_status_entries()
        except CraftyUnavailableError as e:
            await ctx.send(str(e))
            return

        if len(entries) == 1:
            only = entries[0]
            embed = build_status_embed(only["stats"], server_name=only["server_name"])
            view = ServerControlView(
                self, only["server_id"], only["server_name"], only["running"] is True, show_back=False
            )
            await ctx.send(embed=embed, view=view)
            return

        embed = self._build_list_embed(entries)
        view = ServerListView(self, entries)
        await ctx.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Minecraft(bot))
