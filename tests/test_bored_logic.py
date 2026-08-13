"""bored check_loop exception-safety (review F4).

tasks.loop only auto-restarts on a small allowlist of network errors; any
other exception stops the loop for the life of the process, silently
disabling the nudge feature until a manual restart.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.modules.bored import Bored
from bot.stores import Stores

GUILD = 222
CHANNEL = 333


def _make_bot(db):
    bot = MagicMock()
    bot.stores = Stores(db)
    bot.wait_until_ready = AsyncMock()
    bot.guilds = []
    return bot


def _make_cog(bot):
    cog = Bored(bot)
    cog.check_loop.cancel()  # don't let the real 60s loop run against the mocks
    return cog


def _make_guild(channel=None):
    guild = MagicMock()
    guild.id = GUILD
    guild.get_channel = MagicMock(return_value=channel)
    return guild


@pytest.mark.asyncio
async def test_check_loop_survives_a_failing_store(db):
    bot = _make_bot(db)
    bot.guilds = [_make_guild()]
    cog = _make_cog(bot)
    bot.stores.config.get_int = AsyncMock(side_effect=RuntimeError("db exploded"))

    await cog.check_loop.coro(cog)  # must return normally, not propagate


@pytest.mark.asyncio
async def test_check_loop_survives_a_failing_send(db):
    bot = _make_bot(db)
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=RuntimeError("gateway exploded"))
    bot.guilds = [_make_guild(channel)]
    cog = _make_cog(bot)
    await bot.stores.config.set(GUILD, "bored.channel", str(CHANNEL))
    await bot.stores.config.set(GUILD, "bored.idle_seconds", "0")
    cog._last_activity[CHANNEL] = time.monotonic() - 10_000

    await cog.check_loop.coro(cog)  # must return normally


@pytest.mark.asyncio
async def test_check_loop_posts_the_nudge_when_the_channel_is_idle(db):
    bot = _make_bot(db)
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.guilds = [_make_guild(channel)]
    cog = _make_cog(bot)
    await bot.stores.config.set(GUILD, "bored.channel", str(CHANNEL))
    await bot.stores.config.set(GUILD, "bored.idle_seconds", "0")
    cog._last_activity[CHANNEL] = time.monotonic() - 10_000

    await cog.check_loop.coro(cog)

    channel.send.assert_awaited_once()
    assert cog._fired[CHANNEL] is True


# --- review F5: the nudge used to reset its own idle timer ---------------

def _make_message(is_bot: bool, guild=object()):
    message = MagicMock()
    message.author.bot = is_bot
    message.guild = guild
    message.channel.id = CHANNEL
    return message


@pytest.mark.asyncio
async def test_on_message_ignores_bot_authors():
    """check_loop's own nudge re-entered on_message, resetting _last_activity
    and clearing the _fired latch that exists solely to stop repeat nudges - so
    a silent channel got posted in every idle_seconds forever."""
    cog = _make_cog(_make_bot(None))
    cog._fired[CHANNEL] = True

    await cog.on_message(_make_message(is_bot=True))

    assert CHANNEL not in cog._last_activity
    assert cog._fired[CHANNEL] is True  # latch survives the bot's own message


@pytest.mark.asyncio
async def test_on_message_from_a_human_resets_activity_and_latch():
    cog = _make_cog(_make_bot(None))
    cog._fired[CHANNEL] = True
    before = time.monotonic()

    await cog.on_message(_make_message(is_bot=False))

    assert cog._last_activity[CHANNEL] >= before
    assert cog._fired[CHANNEL] is False


@pytest.mark.asyncio
async def test_on_message_still_ignores_dms():
    cog = _make_cog(_make_bot(None))
    await cog.on_message(_make_message(is_bot=False, guild=None))
    assert CHANNEL not in cog._last_activity


# --- review F10: _last_activity/_fired grew one entry per channel, forever ---


def _sweep_cog(db):
    cog = Bored(_make_bot(db))
    cog.check_loop.cancel()
    return cog


async def test_sweep_activity_drops_a_channel_not_seen_for_a_day(db):
    cog = _sweep_cog(db)
    now = 200_000.0
    cog._last_activity[CHANNEL] = now - 90_000
    cog._fired[CHANNEL] = True
    assert cog._sweep_activity(now, watched=set()) == 1
    assert CHANNEL not in cog._last_activity
    assert CHANNEL not in cog._fired


async def test_sweep_activity_keeps_a_recently_active_channel(db):
    cog = _sweep_cog(db)
    now = 200_000.0
    cog._last_activity[CHANNEL] = now - 60
    assert cog._sweep_activity(now, watched=set()) == 0
    assert CHANNEL in cog._last_activity


async def test_sweep_activity_never_drops_a_currently_watched_channel(db):
    """Evicting the watched channel would also clear its _fired latch and
    re-arm the nudge, undoing phase 2's F5 fix."""
    cog = _sweep_cog(db)
    now = 200_000.0
    cog._last_activity[CHANNEL] = now - 90_000
    cog._fired[CHANNEL] = True
    assert cog._sweep_activity(now, watched={CHANNEL}) == 0
    assert cog._fired[CHANNEL] is True
