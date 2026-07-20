from bot.modules.setup import build_summary_lines


def test_build_summary_lines_shows_default_when_unset():
    lines = build_summary_lines({})
    assert any("`spam.max_messages`" in line and "5 (default)" in line for line in lines)


def test_build_summary_lines_shows_stored_value_when_set():
    lines = build_summary_lines({"spam.max_messages": "10"})
    matching = [line for line in lines if "`spam.max_messages`" in line]
    assert matching and "10" in matching[0] and "(default)" not in matching[0]


def test_build_summary_lines_covers_every_manifest_key():
    from bot.modules.setup import CONFIG_MANIFEST

    lines = build_summary_lines({})
    assert len(lines) == len(CONFIG_MANIFEST)
