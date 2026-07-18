from bot.modules.markov import build_chain, generate


def test_build_chain_links_consecutive_words():
    chain = build_chain(["the quick brown fox"])
    assert "quick" in chain["the"]
    assert "brown" in chain["quick"]


def test_build_chain_merges_across_messages():
    chain = build_chain(["the quick fox", "the lazy dog"])
    assert set(chain["the"]) == {"quick", "lazy"}


def test_generate_returns_empty_for_empty_chain():
    assert generate({}) == ""


def test_generate_produces_bounded_length():
    chain = build_chain(["a b c d e f g"])
    result = generate(chain, length=3)
    assert len(result.split()) <= 3


def test_generate_stops_at_dead_end():
    chain = {"only": ["word"]}  # "word" has no outgoing edges
    result = generate(chain, length=10)
    assert result in ("only word", "word")
