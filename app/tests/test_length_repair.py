"""
Tests for length_repair.py — the bounded compress-and-recheck loop with
a guaranteed deterministic truncation fallback. This is the actual
self-repair LOOP in the ACP conformance work (the brand cascade is a
fallback CHAIN, tried once per stage — see test_brand_resolution.py).
"""

from __future__ import annotations

from length_repair import ELLIPSIS, MAX_COMPRESSION_ATTEMPTS, repair_field_length

LONG_TEXT = "word " * 40  # 200 chars, well over any limit used below


def test_text_already_under_limit_is_returned_unchanged():
    result = repair_field_length("short text", limit=150)
    assert result.method == "none"
    assert result.repaired is False
    assert result.text == "short text"
    assert result.attempts == 0


def test_no_compress_fn_goes_straight_to_guaranteed_truncation():
    result = repair_field_length(LONG_TEXT, limit=150, compress_fn=None)
    assert len(result.text) <= 150
    assert result.method == "truncated"
    assert result.text.endswith(ELLIPSIS)


def test_compress_fn_succeeding_on_first_attempt_is_used_as_is():
    def compress(text, limit):
        return text[:limit]

    result = repair_field_length(LONG_TEXT, limit=150, compress_fn=compress)
    assert result.method == "compressed"
    assert result.attempts == 1
    assert len(result.text) <= 150


def test_bounded_retry_actually_retries_before_succeeding():
    """The loop must be a real loop, not a single attempt: a compressor
    that fails once and succeeds on the second call should land as
    'compressed' with attempts == 2, not fall through to truncation."""
    calls = {"n": 0}

    def flaky_compress(text, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            return text  # doesn't actually shorten -> must retry
        return text[:limit]

    result = repair_field_length(LONG_TEXT, limit=150, compress_fn=flaky_compress)
    assert result.method == "compressed"
    assert result.attempts == 2
    assert calls["n"] == 2


def test_gives_up_after_max_attempts_and_still_guarantees_the_limit():
    def useless_compress(text, limit):
        return text  # never actually shortens

    result = repair_field_length(LONG_TEXT, limit=150, compress_fn=useless_compress)
    assert result.method == "truncated"
    assert result.attempts == MAX_COMPRESSION_ATTEMPTS
    assert len(result.text) <= 150


def test_a_raising_compress_fn_is_caught_and_the_loop_still_terminates():
    def raising_compress(text, limit):
        raise RuntimeError("simulated API failure")

    result = repair_field_length(LONG_TEXT, limit=150, compress_fn=raising_compress)
    assert result.method == "truncated"
    assert len(result.text) <= 150
    assert "raised" in result.notes[0]


def test_a_compress_fn_returning_empty_text_does_not_crash_and_still_terminates():
    def empty_compress(text, limit):
        return "   "  # whitespace-only, must not be accepted as a real result

    result = repair_field_length(LONG_TEXT, limit=150, compress_fn=empty_compress)
    assert result.method == "truncated"
    assert len(result.text) <= 150


def test_single_long_word_with_no_space_still_respects_the_limit():
    result = repair_field_length("x" * 300, limit=150, compress_fn=None)
    assert len(result.text) <= 150


def test_unbroken_text_keeps_real_content_not_just_a_placeholder():
    """Regression guard for the specific reason textwrap.shorten isn't
    used alone: given text with no break opportunity inside the width,
    shorten() returns ONLY the placeholder, destroying all content. A
    product name in a script that doesn't use whitespace (Thai, Khmer,
    Lao, Myanmar — all already handled in readiness_score.py) is exactly
    that case. Truncation must degrade to a character slice there."""
    thai_like = "ก" * 300
    result = repair_field_length(thai_like, limit=150, compress_fn=None)
    assert len(result.text) <= 150
    stripped = result.text.replace("\u2026", "").strip()
    assert len(stripped) > 100, f"content was destroyed, not truncated: {result.text!r}"


def test_no_break_opportunity_ascii_also_keeps_content():
    result = repair_field_length("NICELONGYIFORMEN" * 20, limit=150, compress_fn=None)
    stripped = result.text.replace("\u2026", "").strip()
    assert len(stripped) > 100, f"content was destroyed, not truncated: {result.text!r}"


def test_repair_never_returns_text_over_the_limit_regardless_of_path():
    """A blanket invariant check across every path this module can take."""
    for compress_fn in (None, lambda t, l: t, lambda t, l: t[:l], lambda t, l: (_ for _ in ()).throw(RuntimeError())):
        result = repair_field_length(LONG_TEXT, limit=70, compress_fn=compress_fn)
        assert len(result.text) <= 70, f"limit violated for compress_fn={compress_fn!r}: {len(result.text)}"
        
        