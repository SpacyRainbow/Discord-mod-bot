from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.starboard import STAR_EMOJI, Starboard, star_line
from bot.stores import Stores

GUILD = 111
SOURCE_CHANNEL = 10
STARBOARD_CHANNEL = 20


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    return bot


class _AsyncUserIter:
    def __init__(self, users):
        self._users = list(users)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._users:
            raise StopAsyncIteration
        return self._users.pop(0)


def _make_reaction(count, star_user_ids):
    reaction = MagicMock()
    reaction.emoji = STAR_EMOJI
    reaction.count = count
    users = [MagicMock(id=uid) for uid in star_user_ids]
    reaction.users = MagicMock(return_value=_AsyncUserIter(users))
    return reaction


def _make_message(author_id, reactions):
    message = MagicMock()
    message.id = 555
    message.author = MagicMock(id=author_id)
    message.content = "look at this"
    message.attachments = []
    message.jump_url = "https://discord.com/channels/1/2/3"
    message.reactions = reactions
    message.created_at = discord.utils.utcnow()
    return message


def _make_payload(emoji=STAR_EMOJI, channel_id=SOURCE_CHANNEL, message_id=555):
    payload = MagicMock()
    payload.guild_id = GUILD
    payload.emoji = emoji
    payload.channel_id = channel_id
    payload.message_id = message_id
    return payload


def _make_guild(source_channel, starboard_channel):
    guild = MagicMock()
    guild.id = GUILD

    def get_channel(cid):
        return {SOURCE_CHANNEL: source_channel, STARBOARD_CHANNEL: starboard_channel}.get(cid)

    guild.get_channel = MagicMock(side_effect=get_channel)
    return guild


def test_star_line_formats_count():
    assert star_line(5) == f"{STAR_EMOJI} **5**"


@pytest.mark.asyncio
async def test_reaction_ignored_when_starboard_not_configured(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    source_channel = MagicMock()
    source_channel.fetch_message = AsyncMock()
    guild = _make_guild(source_channel, None)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._handle_reaction(_make_payload())

    source_channel.fetch_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_ignored_for_non_star_emoji(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    await bot.stores.config.set(GUILD, "starboard.channel", str(STARBOARD_CHANNEL))
    guild = _make_guild(MagicMock(), MagicMock())
    bot.get_guild = MagicMock(return_value=guild)

    await cog._handle_reaction(_make_payload(emoji="\N{THUMBS UP SIGN}"))

    guild.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_reaction_ignored_inside_the_starboard_channel_itself(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    await bot.stores.config.set(GUILD, "starboard.channel", str(STARBOARD_CHANNEL))
    guild = _make_guild(MagicMock(), MagicMock())
    bot.get_guild = MagicMock(return_value=guild)

    await cog._handle_reaction(_make_payload(channel_id=STARBOARD_CHANNEL))

    # bails out before ever resolving the starboard channel object
    guild.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_below_threshold_does_not_post(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    await bot.stores.config.set(GUILD, "starboard.channel", str(STARBOARD_CHANNEL))
    await bot.stores.config.set(GUILD, "starboard.threshold", "3")
    message = _make_message(author_id=1, reactions=[_make_reaction(2, [4, 5])])
    source_channel = MagicMock()
    source_channel.fetch_message = AsyncMock(return_value=message)
    starboard_channel = MagicMock()
    starboard_channel.send = AsyncMock()
    guild = _make_guild(source_channel, starboard_channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._handle_reaction(_make_payload())

    starboard_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_at_threshold_posts_to_starboard(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    await bot.stores.config.set(GUILD, "starboard.channel", str(STARBOARD_CHANNEL))
    await bot.stores.config.set(GUILD, "starboard.threshold", "2")
    message = _make_message(author_id=1, reactions=[_make_reaction(2, [4, 5])])
    source_channel = MagicMock()
    source_channel.fetch_message = AsyncMock(return_value=message)
    starboard_channel = MagicMock()
    posted = MagicMock(id=999)
    starboard_channel.send = AsyncMock(return_value=posted)
    guild = _make_guild(source_channel, starboard_channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._handle_reaction(_make_payload())

    starboard_channel.send.assert_awaited_once()
    stored = await bot.stores.starboard.get(GUILD, message.id)
    assert stored == 999


@pytest.mark.asyncio
async def test_authors_own_star_does_not_count(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    await bot.stores.config.set(GUILD, "starboard.channel", str(STARBOARD_CHANNEL))
    await bot.stores.config.set(GUILD, "starboard.threshold", "2")
    # count=2, but one of the two stars is the author's own
    message = _make_message(author_id=1, reactions=[_make_reaction(2, [1, 5])])
    source_channel = MagicMock()
    source_channel.fetch_message = AsyncMock(return_value=message)
    starboard_channel = MagicMock()
    starboard_channel.send = AsyncMock()
    guild = _make_guild(source_channel, starboard_channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._handle_reaction(_make_payload())

    starboard_channel.send.assert_not_awaited()  # real count is 1, below threshold of 2


@pytest.mark.asyncio
async def test_already_posted_message_updates_star_count_instead_of_reposting(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    await bot.stores.config.set(GUILD, "starboard.channel", str(STARBOARD_CHANNEL))
    await bot.stores.config.set(GUILD, "starboard.threshold", "2")
    await bot.stores.starboard.set(GUILD, 555, 999)
    message = _make_message(author_id=1, reactions=[_make_reaction(1, [5])])
    source_channel = MagicMock()
    source_channel.fetch_message = AsyncMock(return_value=message)
    starboard_channel = MagicMock()
    existing_post = MagicMock()
    existing_post.edit = AsyncMock()
    starboard_channel.fetch_message = AsyncMock(return_value=existing_post)
    starboard_channel.send = AsyncMock()
    guild = _make_guild(source_channel, starboard_channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._handle_reaction(_make_payload())

    existing_post.edit.assert_awaited_once_with(content=star_line(1))
    starboard_channel.send.assert_not_awaited()  # never reposted, even below the original threshold


@pytest.mark.asyncio
async def test_post_forbidden_does_not_raise(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    await bot.stores.config.set(GUILD, "starboard.channel", str(STARBOARD_CHANNEL))
    await bot.stores.config.set(GUILD, "starboard.threshold", "1")
    message = _make_message(author_id=1, reactions=[_make_reaction(1, [5])])
    source_channel = MagicMock()
    source_channel.fetch_message = AsyncMock(return_value=message)
    starboard_channel = MagicMock()
    starboard_channel.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    guild = _make_guild(source_channel, starboard_channel)
    bot.get_guild = MagicMock(return_value=guild)

    await cog._handle_reaction(_make_payload())  # must not raise


# ---- edge cases ----


@pytest.mark.asyncio
async def test_reaction_outside_a_guild_is_ignored(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    bot.get_guild = MagicMock()
    payload = _make_payload()
    payload.guild_id = None

    await cog._handle_reaction(payload)

    bot.get_guild.assert_not_called()


@pytest.mark.asyncio
async def test_reaction_for_a_guild_the_bot_no_longer_sees_is_ignored(db):
    bot = _make_bot(db)
    cog = Starboard(bot)
    bot.get_guild = MagicMock(return_value=None)

    await cog._handle_reaction(_make_payload())  # must not raise


# --- review F9: concurrent reactions produced a duplicate starboard post --

@pytest.mark.asyncio
async def test_two_simultaneous_reactions_post_only_once(db):
    """_handle_reaction decided to post by checking get(...) is None first. Two
    reactions crossing the threshold together both saw None, both posted, and
    the second set() raised IntegrityError past the `except RuntimeError`."""
    import asyncio

    bot = _make_bot(db)
    await bot.stores.config.set(GUILD, "starboard.channel", str(STARBOARD_CHANNEL))
    await bot.stores.config.set(GUILD, "starboard.threshold", "2")

    posted = []

    async def send(**kwargs):
        await asyncio.sleep(0)  # yield mid-post, so an unguarded second call interleaves
        message = MagicMock()
        message.id = 700 + len(posted)
        posted.append(message)
        return message

    starboard_channel = MagicMock()
    starboard_channel.send = AsyncMock(side_effect=send)
    starboard_channel.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))

    source_channel = MagicMock()
    source_channel.fetch_message = AsyncMock(
        side_effect=lambda _: _make_message(999, [_make_reaction(3, [1, 2, 3])])
    )
    guild = _make_guild(source_channel, starboard_channel)
    bot.get_guild = MagicMock(return_value=guild)

    cog = Starboard(bot)
    await asyncio.gather(cog._handle_reaction(_make_payload()), cog._handle_reaction(_make_payload()))

    assert len(posted) == 1
    assert await bot.stores.starboard.get(GUILD, 555) == posted[0].id


# --- review F10: _locks grew one entry per starred message, forever ---


async def test_sweep_locks_drops_an_unheld_lock(db):
    cog = Starboard(_make_bot(db))
    cog._lock_for(GUILD, 500)
    assert cog._sweep_locks() == 1
    assert cog._locks == {}


async def test_sweep_locks_never_evicts_a_held_lock(db):
    cog = Starboard(_make_bot(db))
    lock = cog._lock_for(GUILD, 500)
    async with lock:
        assert cog._sweep_locks() == 0
        assert cog._lock_for(GUILD, 500) is lock


async def test_sweep_locks_loop_body_survives_a_raising_sweep(db):
    cog = Starboard(_make_bot(db))
    cog._sweep_locks = MagicMock(side_effect=RuntimeError("boom"))
    await cog.sweep_locks.coro(cog)  # must not raise (review F4)
