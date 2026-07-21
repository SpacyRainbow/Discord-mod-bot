from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.modules.quote import Quote, _resolve_mentions
from bot.stores import Stores

GUILD = 111


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    return bot


def _make_ctx(guild):
    ctx = MagicMock()
    ctx.guild = guild
    ctx.author.id = 999
    ctx.send = AsyncMock()
    ctx.defer = AsyncMock()
    return ctx


def _make_guild(members=None, roles=None, channels=None):
    guild = MagicMock()
    guild.id = GUILD
    guild.get_member = MagicMock(side_effect=lambda uid: (members or {}).get(uid))
    guild.get_role = MagicMock(side_effect=lambda rid: (roles or {}).get(rid))
    guild.get_channel = MagicMock(side_effect=lambda cid: (channels or {}).get(cid))
    return guild


def test_resolve_mentions_replaces_user_mention():
    member = MagicMock()
    member.display_name = "SpacyRainbow"
    guild = _make_guild(members={460208657821466624: member})

    assert _resolve_mentions(guild, "hi <@460208657821466624>!") == "hi @SpacyRainbow!"


def test_resolve_mentions_handles_nickname_mention_format():
    member = MagicMock()
    member.display_name = "SpacyRainbow"
    guild = _make_guild(members={42: member})

    assert _resolve_mentions(guild, "<@!42>") == "@SpacyRainbow"


def test_resolve_mentions_replaces_role_and_channel_mentions():
    role = MagicMock()
    role.name = "Mods"
    channel = MagicMock()
    channel.name = "general"
    guild = _make_guild(roles={7: role}, channels={8: channel})

    assert _resolve_mentions(guild, "<@&7> in <#8>") == "@Mods in #general"


def test_resolve_mentions_leaves_unresolvable_mention_unchanged():
    guild = _make_guild()

    assert _resolve_mentions(guild, "<@999>") == "<@999>"


def test_resolve_mentions_returns_text_unchanged_without_a_guild():
    assert _resolve_mentions(None, "<@1>") == "<@1>"


@pytest.mark.asyncio
async def test_quote_command_resolves_mentioned_author(db):
    member = MagicMock()
    member.display_name = "SpacyRainbow"
    guild = _make_guild(members={460208657821466624: member})
    cog = Quote(_make_bot(db))
    ctx = _make_ctx(guild)

    await Quote.add_quote.callback(cog, ctx, "<@460208657821466624>", content="meow meow meowieee")
    await Quote.quote.callback(cog, ctx, None)

    embed = ctx.send.await_args.kwargs["embed"]
    assert "@SpacyRainbow" in embed.footer.text
    assert "460208657821466624" not in embed.footer.text
