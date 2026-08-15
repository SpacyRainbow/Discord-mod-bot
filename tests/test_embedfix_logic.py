from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.embedfix import (
    CROSS_MARK,
    PLATFORMS,
    EmbedFix,
    find_links,
    fix_links,
    platform_for,
    rewrite,
    strip_uneditable,
)
from bot.stores import Stores

ALL = set(PLATFORMS)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/user/status/123", "https://fxtwitter.com/user/status/123"),
        ("https://twitter.com/user/status/123", "https://fxtwitter.com/user/status/123"),
        ("https://www.twitter.com/user/status/123", "https://fxtwitter.com/user/status/123"),
        ("https://mobile.x.com/user/status/123", "https://fxtwitter.com/user/status/123"),
        ("https://www.tiktok.com/@who/video/7", "https://tiktokfix.com/@who/video/7"),
        ("https://vm.tiktok.com/ZMabc/", "https://tiktokfix.com/ZMabc/"),
        ("https://www.tiktok.com/t/ZMabc/", "https://tiktokfix.com/t/ZMabc/"),
        ("https://instagram.com/reel/abc/", "https://kkinstagram.com/reel/abc/"),
        ("https://old.reddit.com/r/x/comments/1/t/", "https://rxddit.com/r/x/comments/1/t/"),
        ("https://bsky.app/profile/a.bsky.social/post/1", "https://fxbsky.app/profile/a.bsky.social/post/1"),
        ("https://www.pixiv.net/artworks/12345", "https://phixiv.net/artworks/12345"),
        ("https://clips.twitch.tv/SomeClipName", "https://fxtwitch.seria.moe/SomeClipName"),
    ],
)
def test_rewrite_maps_each_platform_to_its_proxy(url, expected):
    assert rewrite(url, ALL) == expected


def test_rewrite_upgrades_http_to_https():
    assert rewrite("http://x.com/user/status/123", ALL) == "https://fxtwitter.com/user/status/123"


def test_rewrite_is_case_insensitive_about_the_host():
    assert rewrite("https://X.COM/user/status/123", ALL) == "https://fxtwitter.com/user/status/123"


def test_rewrite_ignores_unknown_host():
    assert rewrite("https://example.com/a/b", ALL) is None


def test_rewrite_ignores_a_bare_domain_with_nothing_to_embed():
    assert rewrite("https://x.com/", ALL) is None
    assert rewrite("https://x.com", ALL) is None


def test_rewrite_is_idempotent_on_an_already_fixed_link():
    # Stops the bot fixing its own reply, or a link someone pre-fixed by hand.
    assert rewrite("https://fxtwitter.com/user/status/123", ALL) is None
    assert rewrite("https://vxtwitter.com/user/status/123", ALL) is None
    assert rewrite("https://ddinstagram.com/reel/abc/", ALL) is None


def test_rewrite_respects_a_disabled_platform():
    assert rewrite("https://x.com/user/status/123", ALL - {"twitter"}) is None
    # ...while leaving the others alone.
    assert rewrite("https://vm.tiktok.com/ZMabc/", ALL - {"twitter"}) is not None


def test_rewrite_strips_tracking_params_but_keeps_real_ones():
    got = rewrite("https://x.com/u/status/1?t=abc&s=20&lang=en", ALL)
    assert got == "https://fxtwitter.com/u/status/1?lang=en"


def test_rewrite_drops_the_fragment():
    assert rewrite("https://x.com/u/status/1#anchor", ALL) == "https://fxtwitter.com/u/status/1"


def test_platform_for_names_the_platform():
    assert platform_for("https://vt.tiktok.com/ZMabc/") == "tiktok"
    assert platform_for("https://example.com") is None


def test_strip_uneditable_blanks_angle_wrapped_links():
    assert "x.com" not in strip_uneditable("look <https://x.com/u/status/1>")


def test_strip_uneditable_blanks_code():
    assert "x.com" not in strip_uneditable("`https://x.com/u/status/1`")
    assert "x.com" not in strip_uneditable("```\nhttps://x.com/u/status/1\n```")


def test_find_links_strips_trailing_prose_punctuation():
    assert find_links("see https://x.com/u/status/1, then go") == ["https://x.com/u/status/1"]
    assert find_links("(https://x.com/u/status/1)") == ["https://x.com/u/status/1"]


def test_find_links_keeps_balanced_parens_in_a_path():
    got = find_links("https://reddit.com/r/x/wiki/Foo_(bar)")
    assert got == ["https://reddit.com/r/x/wiki/Foo_(bar)"]


def test_fix_links_ignores_an_opted_out_link():
    assert fix_links("<https://x.com/u/status/1>", ALL) == []


def test_fix_links_ignores_a_link_in_a_code_block():
    assert fix_links("```\nhttps://x.com/u/status/1\n```", ALL) == []


def test_fix_links_returns_nothing_for_a_plain_message():
    assert fix_links("hey did you see the new patch notes", ALL) == []


def test_fix_links_deduplicates():
    content = "https://x.com/u/status/1 and again https://x.com/u/status/1"
    assert fix_links(content, ALL) == ["https://fxtwitter.com/u/status/1"]


def test_fix_links_caps_the_number_of_rewrites():
    content = " ".join(f"https://x.com/u/status/{i}" for i in range(10))
    assert len(fix_links(content, ALL, limit=3)) == 3


def test_fix_links_handles_mixed_platforms_in_one_message():
    content = "https://x.com/u/status/1 https://vm.tiktok.com/ZMabc/ https://example.com/x"
    assert fix_links(content, ALL) == [
        "https://fxtwitter.com/u/status/1",
        "https://tiktokfix.com/ZMabc/",
    ]


def test_every_platform_replacement_is_a_known_proxy_host():
    # Guards the idempotency contract: a host the fixer can produce must also
    # be a host it refuses to re-fix, or two bots would ping-pong a link.
    from bot.modules.embedfix import PROXY_HOSTS

    for _pattern, replacement, _drop in PLATFORMS.values():
        assert replacement in PROXY_HOSTS


# --- the undo cross-mark's lifetime ---------------------------------------
#
# The cross-mark is only meant to be up while the undo actually works, so these
# cover: not offering it when it can never work, booking its removal, the
# scheduled removal itself, and the self-heal that catches a leftover.

GUILD = 111
BOT_ID = 42
POSTER_ID = 7
BYSTANDER_ID = 8


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.user = MagicMock(id=BOT_ID)
    return bot


def _make_reply(age_seconds=0):
    reply = MagicMock()
    reply.id = 555
    reply.channel = MagicMock(id=10)
    reply.created_at = discord.utils.utcnow() - timedelta(seconds=age_seconds)
    reply.add_reaction = AsyncMock()
    reply.remove_reaction = AsyncMock()
    reply.delete = AsyncMock()
    return reply


def _make_source_message(reply):
    message = MagicMock()
    message.author = MagicMock(id=POSTER_ID, bot=False)
    message.guild = MagicMock(id=GUILD)
    message.channel = MagicMock(id=10)
    message.content = "https://x.com/u/status/1"
    message.reply = AsyncMock(return_value=reply)
    message.edit = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_reply_gets_a_cross_mark_and_a_booked_expiry(db):
    bot = _make_bot(db)
    cog = EmbedFix(bot)
    reply = _make_reply()

    await cog.on_message(_make_source_message(reply))

    reply.add_reaction.assert_awaited_once_with(CROSS_MARK)
    due = await bot.stores.scheduled.due((reply.created_at + timedelta(seconds=121)).isoformat())
    assert [(kind, payload) for _id, _g, kind, payload in due] == [
        ("embedfix_expire", {"channel_id": 10, "message_id": 555})
    ]


@pytest.mark.asyncio
async def test_expiry_is_booked_for_the_end_of_the_window_not_before(db):
    bot = _make_bot(db)
    cog = EmbedFix(bot)
    reply = _make_reply()

    await cog.on_message(_make_source_message(reply))

    one_second_early = reply.created_at + timedelta(seconds=119)
    assert await bot.stores.scheduled.due(one_second_early.isoformat()) == []


@pytest.mark.asyncio
async def test_no_cross_mark_at_all_when_the_window_is_zero(db):
    # Nobody but a moderator could ever use it, so it would be a dead control.
    bot = _make_bot(db)
    cog = EmbedFix(bot)
    await bot.stores.config.set(GUILD, "embedfix.remove_seconds", "0")
    reply = _make_reply()

    await cog.on_message(_make_source_message(reply))

    reply.add_reaction.assert_not_awaited()
    assert await bot.stores.scheduled.due(discord.utils.utcnow().isoformat()) == []


@pytest.mark.asyncio
async def test_expiry_handler_removes_only_the_bots_own_cross_mark(db):
    # remove_reaction needs no permission; clear_reaction would need Manage
    # Messages, which the bot is not guaranteed to have.
    bot = _make_bot(db)
    cog = EmbedFix(bot)
    reply = _make_reply(age_seconds=200)
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(return_value=reply)
    bot.get_channel = MagicMock(return_value=channel)

    await cog._handle_expire(GUILD, {"channel_id": 10, "message_id": 555})

    reply.remove_reaction.assert_awaited_once_with(CROSS_MARK, bot.user)


@pytest.mark.asyncio
async def test_expiry_handler_survives_an_already_undone_reply(db):
    # The reply being gone is the happy path: someone used the undo.
    bot = _make_bot(db)
    cog = EmbedFix(bot)
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    bot.get_channel = MagicMock(return_value=channel)

    await cog._handle_expire(GUILD, {"channel_id": 10, "message_id": 555})  # must not raise


@pytest.mark.asyncio
async def test_expired_is_true_only_past_the_window(db):
    bot = _make_bot(db)
    cog = EmbedFix(bot)

    assert await cog._expired(_make_reply(age_seconds=119), GUILD) is False
    assert await cog._expired(_make_reply(age_seconds=121), GUILD) is True


def _drive_reaction_setup(bot, reply, source, clicker_id, manage_messages):
    """Wires up the mocks on_raw_reaction_add walks: guild -> channel ->
    the bot's reply -> the message it was replying to."""
    reply.author = MagicMock(id=BOT_ID)
    reply.reference = MagicMock(message_id=999)

    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(
        side_effect=lambda mid: {reply.id: reply, 999: source}[mid]
    )
    guild = MagicMock(id=GUILD)
    guild.get_channel_or_thread = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=guild)

    member = MagicMock(id=clicker_id)
    member.guild = guild
    member.guild_permissions = MagicMock(manage_messages=manage_messages)

    payload = MagicMock(guild_id=GUILD, emoji=CROSS_MARK, user_id=clicker_id, message_id=reply.id, member=member)
    return payload


@pytest.mark.asyncio
async def test_a_late_click_takes_the_leftover_cross_mark_down(db):
    # Self-heal for a cross-mark the scheduler missed - a database outage, or
    # the gap before the next sweep.
    bot = _make_bot(db)
    cog = EmbedFix(bot)
    reply = _make_reply(age_seconds=200)
    source = _make_source_message(reply)
    payload = _drive_reaction_setup(bot, reply, source, POSTER_ID, manage_messages=False)

    await cog.on_raw_reaction_add(payload)

    reply.delete.assert_not_awaited()  # the window really is closed
    assert (CROSS_MARK, bot.user) in [call.args for call in reply.remove_reaction.await_args_list]


@pytest.mark.asyncio
async def test_a_bystander_click_inside_the_window_leaves_the_cross_mark_alone(db):
    # Refusing someone else's click must not cost the poster their undo.
    bot = _make_bot(db)
    cog = EmbedFix(bot)
    reply = _make_reply(age_seconds=5)
    source = _make_source_message(reply)
    payload = _drive_reaction_setup(bot, reply, source, BYSTANDER_ID, manage_messages=False)

    await cog.on_raw_reaction_add(payload)

    reply.delete.assert_not_awaited()
    assert (CROSS_MARK, bot.user) not in [call.args for call in reply.remove_reaction.await_args_list]


@pytest.mark.asyncio
async def test_a_moderator_can_still_undo_after_the_cross_mark_is_gone(db):
    # They re-add the cross-mark by hand; the handler is stateless, so it works.
    bot = _make_bot(db)
    cog = EmbedFix(bot)
    reply = _make_reply(age_seconds=5000)
    source = _make_source_message(reply)
    payload = _drive_reaction_setup(bot, reply, source, BYSTANDER_ID, manage_messages=True)

    await cog.on_raw_reaction_add(payload)

    reply.delete.assert_awaited_once()
    source.edit.assert_awaited_once_with(suppress=False)
