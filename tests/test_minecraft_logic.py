from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.modules.minecraft import (
    CraftyUnavailableError,
    Minecraft,
    MinecraftGuildRestrictedError,
    ServerControlView,
    ServerNotRunningError,
    build_status_embed,
    minecraft_guild_only,
    sort_servers,
)


def test_build_status_embed_shows_online():
    embed = build_status_embed({"running": True, "online": 2, "max": 20, "version": "1.20.1"})
    assert "Online" in embed.description
    assert any(f.name == "Players" and f.value == "2/20" for f in embed.fields)
    assert any(f.name == "Version" and f.value == "1.20.1" for f in embed.fields)


def test_build_status_embed_shows_offline():
    embed = build_status_embed({"running": False})
    assert "Offline" in embed.description


def test_build_status_embed_shows_unknown_when_running_missing():
    embed = build_status_embed({})
    assert "Unknown" in embed.description


def test_build_status_embed_omits_missing_fields():
    embed = build_status_embed({"running": True})
    assert list(embed.fields) == []


def test_build_status_embed_uses_server_name_when_given():
    embed = build_status_embed({"running": True}, server_name="SkyFactory")
    assert embed.title == "SkyFactory"


def test_build_status_embed_treats_crafty_false_string_as_missing():
    """Regression test: Crafty represents "not set" for some fields (e.g. a
    server that's never been started) as the literal string "False", not
    JSON false/null - this must not show up as "Version: False"."""
    embed = build_status_embed({"running": False, "version": "False", "world_name": "False"})
    assert list(embed.fields) == []


def _make_cog():
    bot = MagicMock()
    cog = Minecraft(bot)
    cog.session = MagicMock()
    return cog


def _mock_response(status=200, json_body=None):
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_body if json_body is not None else {})

    class _CM:
        async def __aenter__(self):
            return resp

        async def __aexit__(self, *args):
            return False

    return _CM()


@pytest.mark.asyncio
async def test_request_reports_not_configured_when_env_missing(monkeypatch):
    monkeypatch.delenv("CRAFTY_BASE_URL", raising=False)
    monkeypatch.delenv("CRAFTY_API_TOKEN", raising=False)
    cog = _make_cog()

    with pytest.raises(CraftyUnavailableError, match="isn't configured"):
        await cog._request("/api/v2/servers/")


@pytest.mark.asyncio
async def test_request_reports_non_200_response(monkeypatch):
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.get = MagicMock(return_value=_mock_response(status=403))

    with pytest.raises(CraftyUnavailableError, match="rejected the request"):
        await cog._request("/api/v2/servers/")


@pytest.mark.asyncio
async def test_request_returns_data_on_success(monkeypatch):
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.get = MagicMock(return_value=_mock_response(json_body={"status": "ok", "data": {"a": 1}}))

    data = await cog._request("/api/v2/servers/")

    assert data == {"a": 1}


@pytest.mark.asyncio
async def test_resolve_server_uses_env_override_when_no_name_given(monkeypatch):
    monkeypatch.setenv("CRAFTY_SERVER_ID", "abc-123")
    cog = _make_cog()

    server_id, server_name = await cog._resolve_server(None)

    assert server_id == "abc-123"
    assert server_name is None


@pytest.mark.asyncio
async def test_resolve_server_auto_picks_when_exactly_one(monkeypatch):
    monkeypatch.delenv("CRAFTY_SERVER_ID", raising=False)
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.get = MagicMock(
        return_value=_mock_response(
            json_body={"status": "ok", "data": [{"server_id": "only-one", "server_name": "Main"}]}
        )
    )

    server_id, server_name = await cog._resolve_server(None)

    assert server_id == "only-one"
    assert server_name == "Main"


@pytest.mark.asyncio
async def test_resolve_server_rejects_zero_servers(monkeypatch):
    monkeypatch.delenv("CRAFTY_SERVER_ID", raising=False)
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.get = MagicMock(return_value=_mock_response(json_body={"status": "ok", "data": []}))

    with pytest.raises(CraftyUnavailableError, match="No servers"):
        await cog._resolve_server(None)


@pytest.mark.asyncio
async def test_resolve_server_lists_names_when_multiple_and_none_given(monkeypatch):
    monkeypatch.delenv("CRAFTY_SERVER_ID", raising=False)
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.get = MagicMock(
        return_value=_mock_response(
            json_body={
                "status": "ok",
                "data": [
                    {"server_id": "one", "server_name": "ftb 3"},
                    {"server_id": "two", "server_name": "Project ozone 3"},
                ],
            }
        )
    )

    with pytest.raises(CraftyUnavailableError, match="ftb 3, Project ozone 3"):
        await cog._resolve_server(None)


@pytest.mark.asyncio
async def test_resolve_server_matches_by_name(monkeypatch):
    monkeypatch.delenv("CRAFTY_SERVER_ID", raising=False)
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.get = MagicMock(
        return_value=_mock_response(
            json_body={
                "status": "ok",
                "data": [
                    {"server_id": "one", "server_name": "ftb 3"},
                    {"server_id": "two", "server_name": "Project ozone 3"},
                ],
            }
        )
    )

    server_id, server_name = await cog._resolve_server("ozone")

    assert server_id == "two"
    assert server_name == "Project ozone 3"


@pytest.mark.asyncio
async def test_resolve_server_reports_no_match_for_unknown_name(monkeypatch):
    monkeypatch.delenv("CRAFTY_SERVER_ID", raising=False)
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.get = MagicMock(
        return_value=_mock_response(
            json_body={"status": "ok", "data": [{"server_id": "one", "server_name": "ftb 3"}]}
        )
    )

    with pytest.raises(CraftyUnavailableError, match="No server matching"):
        await cog._resolve_server("nonexistent")


@pytest.mark.asyncio
async def test_mcstatus_command_reports_crafty_errors_cleanly(monkeypatch):
    monkeypatch.delenv("CRAFTY_BASE_URL", raising=False)
    monkeypatch.delenv("CRAFTY_API_TOKEN", raising=False)
    cog = _make_cog()
    ctx = MagicMock()
    ctx.defer = AsyncMock()
    ctx.send = AsyncMock()

    await Minecraft.mcstatus.callback(cog, ctx, server="anything")

    ctx.send.assert_awaited_once()
    assert "isn't configured" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_mcstatus_command_reports_crafty_errors_cleanly_with_no_server(monkeypatch):
    monkeypatch.delenv("CRAFTY_BASE_URL", raising=False)
    monkeypatch.delenv("CRAFTY_API_TOKEN", raising=False)
    cog = _make_cog()
    ctx = MagicMock()
    ctx.defer = AsyncMock()
    ctx.send = AsyncMock()

    await Minecraft.mcstatus.callback(cog, ctx, server=None)

    ctx.send.assert_awaited_once()
    assert "isn't configured" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_mcstatus_command_skips_list_when_exactly_one_server(monkeypatch):
    monkeypatch.delenv("CRAFTY_SERVER_ID", raising=False)
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    servers_body = {"status": "ok", "data": [{"server_id": "only-one", "server_name": "Main"}]}
    stats_body = {"status": "ok", "data": {"running": True, "online": 2}}
    cog.session.get = MagicMock(
        side_effect=[_mock_response(json_body=servers_body), _mock_response(json_body=stats_body)]
    )
    ctx = MagicMock()
    ctx.defer = AsyncMock()
    ctx.send = AsyncMock()

    await Minecraft.mcstatus.callback(cog, ctx, server=None)

    ctx.send.assert_awaited_once()
    kwargs = ctx.send.await_args.kwargs
    assert kwargs["embed"].title == "Main"
    assert isinstance(kwargs["view"], ServerControlView)


@pytest.mark.asyncio
async def test_mcstatus_command_shows_list_view_when_multiple_servers(monkeypatch):
    monkeypatch.delenv("CRAFTY_SERVER_ID", raising=False)
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    servers_body = {
        "status": "ok",
        "data": [
            {"server_id": "one", "server_name": "ftb 3"},
            {"server_id": "two", "server_name": "Project ozone 3"},
        ],
    }
    stats_body = {"status": "ok", "data": {"running": False}}
    cog.session.get = MagicMock(
        side_effect=[
            _mock_response(json_body=servers_body),
            _mock_response(json_body=stats_body),
            _mock_response(json_body=stats_body),
        ]
    )
    ctx = MagicMock()
    ctx.defer = AsyncMock()
    ctx.send = AsyncMock()

    await Minecraft.mcstatus.callback(cog, ctx, server=None)

    ctx.send.assert_awaited_once()
    kwargs = ctx.send.await_args.kwargs
    assert kwargs["embed"].title == "Minecraft servers"
    assert len(kwargs["view"].children) == 2


# ---- sort_servers ----


def test_sort_servers_orders_by_player_count_then_status_then_name():
    entries = [
        {"server_name": "Zeta", "running": True, "online": 3},
        {"server_name": "Alpha", "running": False, "online": None},
        {"server_name": "Beta", "running": True, "online": 5},
        {"server_name": "Gamma", "running": True, "online": 3},
    ]

    ordered = [e["server_name"] for e in sort_servers(entries)]

    assert ordered == ["Beta", "Gamma", "Zeta", "Alpha"]


def test_sort_servers_treats_missing_online_as_zero():
    entries = [
        {"server_name": "HasPlayers", "running": True, "online": 1},
        {"server_name": "NoPlayerField", "running": True, "online": None},
    ]

    ordered = [e["server_name"] for e in sort_servers(entries)]

    assert ordered == ["HasPlayers", "NoPlayerField"]


def test_sort_servers_puts_online_before_offline_at_same_player_count():
    entries = [
        {"server_name": "Offline1", "running": False, "online": 0},
        {"server_name": "Online1", "running": True, "online": 0},
    ]

    ordered = [e["server_name"] for e in sort_servers(entries)]

    assert ordered == ["Online1", "Offline1"]


# ---- minecraft_guild_only ----


def _make_ctx(guild_id):
    ctx = MagicMock()
    if guild_id is None:
        ctx.guild = None
    else:
        ctx.guild = MagicMock()
        ctx.guild.id = guild_id
    return ctx


@pytest.mark.asyncio
async def test_minecraft_guild_only_unrestricted_when_no_env_set(monkeypatch):
    monkeypatch.delenv("MINECRAFT_GUILD_ID", raising=False)
    monkeypatch.delenv("GUILD_ID", raising=False)
    check = minecraft_guild_only()

    assert await check.predicate(_make_ctx(999)) is True
    assert await check.predicate(_make_ctx(None)) is True


@pytest.mark.asyncio
async def test_minecraft_guild_only_allows_configured_guild(monkeypatch):
    monkeypatch.delenv("MINECRAFT_GUILD_ID", raising=False)
    monkeypatch.setenv("GUILD_ID", "1257921024398331975")
    check = minecraft_guild_only()

    assert await check.predicate(_make_ctx(1257921024398331975)) is True


@pytest.mark.asyncio
async def test_minecraft_guild_only_rejects_other_guild(monkeypatch):
    monkeypatch.delenv("MINECRAFT_GUILD_ID", raising=False)
    monkeypatch.setenv("GUILD_ID", "1257921024398331975")
    check = minecraft_guild_only()

    with pytest.raises(MinecraftGuildRestrictedError):
        await check.predicate(_make_ctx(555))


@pytest.mark.asyncio
async def test_minecraft_guild_only_rejects_dm_when_restricted(monkeypatch):
    monkeypatch.delenv("MINECRAFT_GUILD_ID", raising=False)
    monkeypatch.setenv("GUILD_ID", "1257921024398331975")
    check = minecraft_guild_only()

    with pytest.raises(MinecraftGuildRestrictedError):
        await check.predicate(_make_ctx(None))


@pytest.mark.asyncio
async def test_minecraft_guild_only_prefers_minecraft_guild_id_override(monkeypatch):
    monkeypatch.setenv("MINECRAFT_GUILD_ID", "111")
    monkeypatch.setenv("GUILD_ID", "222")
    check = minecraft_guild_only()

    assert await check.predicate(_make_ctx(111)) is True
    with pytest.raises(MinecraftGuildRestrictedError):
        await check.predicate(_make_ctx(222))


# ---- send_action / send_console_command ----


def _mock_post_response(status=200, json_body=None):
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_body if json_body is not None else {})

    class _CM:
        async def __aenter__(self):
            return resp

        async def __aexit__(self, *args):
            return False

    return _CM()


@pytest.mark.asyncio
async def test_send_action_success(monkeypatch):
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.post = MagicMock(return_value=_mock_post_response(json_body={"status": "ok"}))

    await cog.send_action("server-1", "start_server")

    cog.session.post.assert_called_once()
    called_url = cog.session.post.call_args.args[0]
    assert called_url.endswith("/api/v2/servers/server-1/action/start_server/")


@pytest.mark.asyncio
async def test_send_console_command_sends_raw_text_body(monkeypatch):
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.post = MagicMock(return_value=_mock_post_response(json_body={"status": "ok"}))

    await cog.send_console_command("server-1", "say hello")

    cog.session.post.assert_called_once()
    assert cog.session.post.call_args.kwargs["data"] == "say hello"


@pytest.mark.asyncio
async def test_send_console_command_raises_server_not_running(monkeypatch):
    monkeypatch.setenv("CRAFTY_BASE_URL", "https://example.test:8443")
    monkeypatch.setenv("CRAFTY_API_TOKEN", "tok")
    cog = _make_cog()
    cog.session.post = MagicMock(
        return_value=_mock_post_response(json_body={"status": "error", "error": "SERVER_NOT_RUNNING"})
    )

    with pytest.raises(ServerNotRunningError):
        await cog.send_console_command("server-1", "say hello")


# ---- ServerControlView permission gating ----


@pytest.mark.asyncio
async def test_server_control_view_rejects_non_mod_clicker():
    cog = _make_cog()
    view = ServerControlView(cog, "server-1", "Main", running=True, show_back=True)

    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.__class__ = MagicMock  # not a discord.Member instance
    interaction.response.send_message = AsyncMock()

    allowed = await view._require_mod(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()


def test_server_control_view_omits_back_button_when_not_requested():
    cog = _make_cog()
    view = ServerControlView(cog, "server-1", "Main", running=True, show_back=False)

    labels = [item.label for item in view.children]

    assert "Back to list" not in labels


def test_server_control_view_includes_back_button_when_requested():
    cog = _make_cog()
    view = ServerControlView(cog, "server-1", "Main", running=True, show_back=True)

    labels = [item.label for item in view.children]

    assert "Back to list" in labels


def test_server_control_view_disables_start_when_already_running():
    cog = _make_cog()
    view = ServerControlView(cog, "server-1", "Main", running=True, show_back=False)

    assert view.start_button.disabled is True
    assert view.stop_button.disabled is False
    assert view.restart_button.disabled is False


def test_server_control_view_disables_stop_and_restart_when_offline():
    cog = _make_cog()
    view = ServerControlView(cog, "server-1", "Main", running=False, show_back=False)

    assert view.start_button.disabled is False
    assert view.stop_button.disabled is True
    assert view.restart_button.disabled is True
