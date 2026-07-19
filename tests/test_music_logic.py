from bot.modules.music import (
    classify_youtube_link,
    clamp_seek_target,
    extract_spotify_playlist_id,
    extract_spotify_track_id,
    merge_skip_segments,
    next_skip_target,
)


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
