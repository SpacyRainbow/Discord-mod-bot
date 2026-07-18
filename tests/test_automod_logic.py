from bot.modules.automod import caps_percentage, contains_banned_word, contains_invite_link


def test_contains_invite_link_detects_standard_format():
    assert contains_invite_link("join us at discord.gg/abc123") is True


def test_contains_invite_link_detects_full_domain():
    assert contains_invite_link("https://discord.com/invite/abc123") is True


def test_contains_invite_link_ignores_normal_message():
    assert contains_invite_link("hey did you see the new patch notes") is False


def test_caps_percentage_all_caps():
    assert caps_percentage("HELLO WORLD") == 100.0


def test_caps_percentage_mixed():
    # "Hello" has 1 upper of 5 letters = 20%
    assert caps_percentage("Hello") == 20.0


def test_caps_percentage_no_letters_returns_zero():
    assert caps_percentage("123 !!! :)") == 0.0


def test_contains_banned_word_matches_whole_word_only():
    assert contains_banned_word("this is a class assignment", ["ass"]) is None


def test_contains_banned_word_matches_standalone_word():
    assert contains_banned_word("that word is banned here", ["banned"]) == "banned"


def test_contains_banned_word_case_insensitive():
    assert contains_banned_word("this is BANNED content", ["banned"]) == "banned"
