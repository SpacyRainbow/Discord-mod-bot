import pytest

from bot.modules.embedfix import PLATFORMS, find_links, fix_links, platform_for, rewrite, strip_uneditable

ALL = set(PLATFORMS)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/user/status/123", "https://fxtwitter.com/user/status/123"),
        ("https://twitter.com/user/status/123", "https://fxtwitter.com/user/status/123"),
        ("https://www.twitter.com/user/status/123", "https://fxtwitter.com/user/status/123"),
        ("https://mobile.x.com/user/status/123", "https://fxtwitter.com/user/status/123"),
        ("https://www.tiktok.com/@who/video/7", "https://vxtiktok.com/@who/video/7"),
        ("https://vm.tiktok.com/ZMabc/", "https://vxtiktok.com/ZMabc/"),
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
        "https://vxtiktok.com/ZMabc/",
    ]


def test_every_platform_replacement_is_a_known_proxy_host():
    # Guards the idempotency contract: a host the fixer can produce must also
    # be a host it refuses to re-fix, or two bots would ping-pong a link.
    from bot.modules.embedfix import PROXY_HOSTS

    for _pattern, replacement, _drop in PLATFORMS.values():
        assert replacement in PROXY_HOSTS
