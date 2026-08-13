import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.music import (
    GuildMusicState,
    Music,
    Track,
    classify_youtube_link,
    clamp_seek_target,
    extract_spotify_playlist_id,
    extract_spotify_track_id,
    merge_skip_segments,
    next_skip_target,
)


def _make_track(title="Song"):
    return Track(title=title, webpage_url="https://example.com/" + title, requester_id=1)


def _make_cog():
    cog = Music(MagicMock())
    cog.position_check.cancel()
    return cog


def _make_ctx(guild_id=1):
    ctx = MagicMock()
    ctx.guild.id = guild_id
    ctx.send = AsyncMock()
    return ctx


def test_merge_skip_segments_empty_returns_empty():
    assert merge_skip_segments([]) == []


def test_merge_skip_segments_already_disjoint_stays_unchanged():
    assert merge_skip_segments([(0, 10), (20, 30)]) == [(0, 10), (20, 30)]


def test_merge_skip_segments_sorts_unsorted_input():
    assert merge_skip_segments([(20, 30), (0, 10)]) == [(0, 10), (20, 30)]


def test_merge_skip_segments_merges_overlapping_ranges():
    assert merge_skip_segments([(0, 15), (10, 20)]) == [(0, 20)]


def test_merge_skip_segments_merges_adjacent_ranges():
    assert merge_skip_segments([(0, 10), (10, 20)]) == [(0, 20)]


def test_merge_skip_segments_merges_fully_contained_range():
    assert merge_skip_segments([(0, 30), (10, 20)]) == [(0, 30)]


def test_next_skip_target_returns_none_outside_any_segment():
    assert next_skip_target(5.0, [(10.0, 20.0)]) is None


def test_next_skip_target_returns_end_when_inside_segment():
    assert next_skip_target(15.0, [(10.0, 20.0)]) == 20.0


def test_next_skip_target_at_segment_start_is_inside():
    assert next_skip_target(10.0, [(10.0, 20.0)]) == 20.0


def test_next_skip_target_at_segment_end_is_outside():
    # end is exclusive - once position reaches the end it's already skipped past
    assert next_skip_target(20.0, [(10.0, 20.0)]) is None


def test_next_skip_target_checks_back_to_back_segments():
    segments = [(10.0, 20.0), (20.0, 30.0)]
    assert next_skip_target(25.0, segments) == 30.0


def test_extract_spotify_track_id_plain_link():
    assert extract_spotify_track_id("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC") == (
        "4uLU6hMCjMI75M1A2tKUQC"
    )


def test_extract_spotify_track_id_with_query_string():
    url = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc123"
    assert extract_spotify_track_id(url) == "4uLU6hMCjMI75M1A2tKUQC"


def test_extract_spotify_track_id_with_locale_prefix():
    url = "https://open.spotify.com/intl-en/track/4uLU6hMCjMI75M1A2tKUQC"
    assert extract_spotify_track_id(url) == "4uLU6hMCjMI75M1A2tKUQC"


def test_extract_spotify_track_id_ignores_non_spotify_query():
    assert extract_spotify_track_id("lil dicky earth") is None


def test_extract_spotify_track_id_ignores_youtube_link():
    assert extract_spotify_track_id("https://youtu.be/dQw4w9WgXcQ") is None


def test_extract_spotify_track_id_ignores_spotify_playlist_link():
    assert extract_spotify_track_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") is None


def test_extract_spotify_playlist_id_plain_link():
    url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
    assert extract_spotify_playlist_id(url) == "37i9dQZF1DXcBWIGoYBM5M"


def test_extract_spotify_playlist_id_ignores_track_link():
    assert extract_spotify_playlist_id("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC") is None


def test_classify_youtube_link_plain_search_is_single():
    assert classify_youtube_link("lil dicky earth") == "single"


def test_classify_youtube_link_plain_video_is_single():
    assert classify_youtube_link("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "single"


def test_classify_youtube_link_short_link_is_single():
    assert classify_youtube_link("https://youtu.be/dQw4w9WgXcQ") == "single"


def test_classify_youtube_link_bare_playlist_is_playlist():
    url = "https://www.youtube.com/playlist?list=PLtestPlaylistId"
    assert classify_youtube_link(url) == "playlist"


def test_classify_youtube_link_video_with_list_param_is_ambiguous():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLtestPlaylistId"
    assert classify_youtube_link(url) == "video_in_playlist"


def test_classify_youtube_link_non_youtube_url_is_single():
    assert classify_youtube_link("https://soundcloud.com/someartist/sometrack") == "single"


def test_clamp_seek_target_within_bounds():
    assert clamp_seek_target(50.0, 10.0, 200.0) == 60.0


def test_clamp_seek_target_rewind_past_start_clamps_to_zero():
    assert clamp_seek_target(5.0, -10.0, 200.0) == 0.0


def test_clamp_seek_target_forward_past_end_clamps_below_duration():
    assert clamp_seek_target(195.0, 10.0, 200.0) == 199.0


def test_clamp_seek_target_unknown_duration_allows_forward_seek():
    assert clamp_seek_target(500.0, 10.0, None) == 510.0


def test_clamp_seek_target_rewind_from_zero_stays_zero():
    assert clamp_seek_target(0.0, -10.0, 200.0) == 0.0


# ---- GuildMusicState defaults ----


def test_guild_music_state_defaults():
    state = GuildMusicState(guild_id=1)
    assert state.volume == 1.0
    assert state.loop_mode == "off"
    assert state.skip_requested is False


# ---- _advance loop-mode handling ----


@pytest.mark.asyncio
async def test_advance_off_mode_does_not_requeue_finished_track():
    cog = _make_cog()
    state = GuildMusicState(guild_id=1)
    state.current = _make_track("A")
    cog._announce_queue_empty = AsyncMock()
    cog._schedule_idle_disconnect = MagicMock()

    await cog._advance(state)

    assert list(state.queue) == []


@pytest.mark.asyncio
async def test_advance_track_loop_requeues_finished_track_at_front():
    cog = _make_cog()
    state = GuildMusicState(guild_id=1)
    finished = _make_track("A")
    upcoming = _make_track("B")
    state.current = finished
    state.queue.append(upcoming)
    state.loop_mode = "track"
    cog._ensure_and_play = AsyncMock(return_value=True)

    await cog._advance(state)

    cog._ensure_and_play.assert_awaited_once_with(state, finished)
    assert list(state.queue) == [upcoming]


@pytest.mark.asyncio
async def test_advance_queue_loop_requeues_finished_track_at_back():
    cog = _make_cog()
    state = GuildMusicState(guild_id=1)
    finished = _make_track("A")
    upcoming = _make_track("B")
    state.current = finished
    state.queue.append(upcoming)
    state.loop_mode = "queue"
    cog._ensure_and_play = AsyncMock(return_value=True)

    await cog._advance(state)

    cog._ensure_and_play.assert_awaited_once_with(state, upcoming)
    assert list(state.queue) == [finished]


@pytest.mark.asyncio
async def test_advance_explicit_skip_bypasses_track_loop():
    cog = _make_cog()
    state = GuildMusicState(guild_id=1)
    state.current = _make_track("A")
    state.loop_mode = "track"
    state.skip_requested = True
    cog._announce_queue_empty = AsyncMock()
    cog._schedule_idle_disconnect = MagicMock()

    await cog._advance(state)

    assert list(state.queue) == []  # not requeued despite track loop mode
    assert state.skip_requested is False  # flag consumed either way


@pytest.mark.asyncio
async def test_advance_resets_skip_requested_flag_when_no_loop():
    cog = _make_cog()
    state = GuildMusicState(guild_id=1)
    state.current = _make_track("A")
    state.skip_requested = True
    cog._announce_queue_empty = AsyncMock()
    cog._schedule_idle_disconnect = MagicMock()

    await cog._advance(state)

    assert state.skip_requested is False


# ---- /volume, /loop, /shuffle commands ----


@pytest.mark.asyncio
async def test_volume_sets_state_and_live_source():
    cog = _make_cog()
    cog._require_shared_voice = AsyncMock(return_value=True)
    ctx = _make_ctx()
    state = cog._state(1)
    source = MagicMock(spec=discord.PCMVolumeTransformer)
    state.voice_client = MagicMock()
    state.voice_client.source = source

    await Music.volume.callback(cog, ctx, 50)

    assert state.volume == 0.5
    assert source.volume == 0.5
    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_volume_rejects_out_of_range():
    cog = _make_cog()
    cog._require_shared_voice = AsyncMock(return_value=True)
    ctx = _make_ctx()

    await Music.volume.callback(cog, ctx, 500)

    ctx.send.assert_awaited_once()
    assert "between 0 and 200" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_volume_with_no_active_source_still_updates_state_for_next_track():
    cog = _make_cog()
    cog._require_shared_voice = AsyncMock(return_value=True)
    ctx = _make_ctx()

    await Music.volume.callback(cog, ctx, 75)

    assert cog._state(1).volume == 0.75


@pytest.mark.asyncio
async def test_loop_sets_mode():
    cog = _make_cog()
    cog._require_shared_voice = AsyncMock(return_value=True)
    ctx = _make_ctx()

    await Music.loop_cmd.callback(cog, ctx, "queue")

    assert cog._state(1).loop_mode == "queue"
    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_loop_rejects_invalid_mode():
    cog = _make_cog()
    cog._require_shared_voice = AsyncMock(return_value=True)
    ctx = _make_ctx()

    await Music.loop_cmd.callback(cog, ctx, "sideways")

    ctx.send.assert_awaited_once()
    assert cog._state(1).loop_mode == "off"


@pytest.mark.asyncio
async def test_shuffle_cmd_rejects_short_queue():
    cog = _make_cog()
    cog._require_shared_voice = AsyncMock(return_value=True)
    ctx = _make_ctx()

    await Music.shuffle_cmd.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    assert "Not enough" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_shuffle_cmd_shuffles_without_losing_tracks():
    cog = _make_cog()
    cog._require_shared_voice = AsyncMock(return_value=True)
    ctx = _make_ctx()
    state = cog._state(1)
    for i in range(10):
        state.queue.append(_make_track(f"Song {i}"))
    original_titles = {t.title for t in state.queue}

    await Music.shuffle_cmd.callback(cog, ctx)

    assert len(state.queue) == 10
    assert {t.title for t in state.queue} == original_titles
    ctx.send.assert_awaited_once()


# --- review F4: the loop body must never let an exception escape -----------
# tasks.loop only auto-restarts on network errors, so an exception escaping
# position_check would silently disable SponsorBlock skipping forever.


def _make_playing_state(cog, guild_id):
    state = cog._state(guild_id)
    state.skip_segments = [(10.0, 20.0)]
    state.voice_client = MagicMock()
    state.voice_client.is_playing = MagicMock(return_value=True)
    return state


@pytest.mark.asyncio
async def test_position_check_survives_a_failing_player():
    cog = _make_cog()
    state = _make_playing_state(cog, 1)
    state.voice_client.is_playing = MagicMock(side_effect=RuntimeError("voice exploded"))

    await cog.position_check.coro(cog)  # must return normally, not propagate


@pytest.mark.asyncio
async def test_position_check_survives_a_failing_seek():
    cog = _make_cog()
    _make_playing_state(cog, 1)
    cog._seek_within_track = AsyncMock(side_effect=RuntimeError("seek exploded"))

    await cog.position_check.coro(cog)  # must return normally


@pytest.mark.asyncio
async def test_position_check_one_broken_guild_does_not_stop_the_others():
    cog = _make_cog()
    broken = _make_playing_state(cog, 1)
    broken.voice_client.is_playing = MagicMock(side_effect=RuntimeError("voice exploded"))
    healthy = _make_playing_state(cog, 2)
    healthy.position = MagicMock(return_value=12.0)  # inside the (10, 20) skip segment

    cog._seek_within_track = AsyncMock()

    await cog.position_check.coro(cog)

    # The healthy guild still got its skip check despite guild 1 blowing up.
    cog._seek_within_track.assert_awaited_once()
    assert cog._seek_within_track.await_args.args[0] is healthy


# --- review F17: _advance failures were swallowed; loops replayed dead URLs ---


@pytest.mark.asyncio
async def test_advance_clears_the_stream_url_when_re_queueing_for_loop_mode():
    """A signed CDN stream URL expires after a few hours, so re-queueing a
    looped track with it still set eventually hands ffmpeg a dead link."""
    cog = _make_cog()
    state = GuildMusicState(1)
    track = _make_track()
    track.stream_url = "https://cdn.example/expired-signed-url"
    state.current = track
    state.loop_mode = "queue"

    played = []

    async def fake_ensure_and_play(s, t, ctx=None):
        played.append(t)
        return True

    cog._ensure_and_play = fake_ensure_and_play
    await cog._advance(state)

    assert played == [track]
    assert track.stream_url is None
    assert track.resolve_query == track.webpage_url


@pytest.mark.asyncio
async def test_advance_leaves_the_stream_url_alone_when_not_looping():
    cog = _make_cog()
    state = GuildMusicState(1)
    finished = _make_track("Finished")
    finished.stream_url = "https://cdn.example/still-set"
    state.current = finished
    state.loop_mode = "off"
    cog._announce_queue_empty = AsyncMock()
    cog._schedule_idle_disconnect = MagicMock()

    await cog._advance(state)

    assert finished.stream_url == "https://cdn.example/still-set"
    assert state.current is None


def test_after_play_attaches_a_done_callback_so_advance_failures_get_logged():
    """Source-level: the Future returned by run_coroutine_threadsafe must not
    be discarded, or an exception inside _advance never surfaces anywhere."""
    import inspect

    from bot.modules import music as music_module

    src = inspect.getsource(music_module.Music._start_playback)
    assert "add_done_callback" in src
    assert "fut.result()" in src


# --- review F18: voice connect errors were unhandled ---


def _play_ctx(guild_id=1):
    ctx = _make_ctx(guild_id)
    ctx.defer = AsyncMock()
    ctx.author.voice.channel = MagicMock()
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [asyncio.TimeoutError(), discord.ClientException("Already connected")]
)
async def test_play_reports_a_failed_voice_connect_and_clears_the_stale_client(error):
    cog = _make_cog()
    ctx = _play_ctx()
    ctx.author.voice.channel.connect = AsyncMock(side_effect=error)
    state = cog._state(1)
    state.voice_client = None

    await Music.play.callback(cog, ctx, query="anything")

    ctx.send.assert_awaited_once()
    assert "couldn't join" in ctx.send.await_args.args[0].lower()
    assert cog._state(1).voice_client is None


# --- review F10: Music.states kept a GuildMusicState per guild forever ---


@pytest.mark.asyncio
async def test_full_stop_evicts_the_guild_state():
    cog = _make_cog()
    state = cog._state(42)
    state.voice_client = None
    cog._strip_player_buttons = AsyncMock()

    await cog._full_stop(state)

    assert 42 not in cog.states
