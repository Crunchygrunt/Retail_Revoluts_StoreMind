"""
Tests for the INR pre-normalization pipeline added to
layer0_validator.py's _normalize_price: scale words (lakh/crore/k/L/Cr),
trailing price-tag noise (/-, "only"), and the strict-vs-strict fallback
that lets legitimate western-grouped INR through without reopening the
malformed-grouping hole (see the "fallback_strict_western" note in
layer0_validator.py's own docstring for why NAIVE non-strict parsing was
tried first and rejected - it silently defeated strict mode's own
malformed-grouping protection).

These tests exercise the INR marker branch only, which is the part that
changed this session - the non-INR price_parser branch is untouched and
covered by test_layer0_validator.py instead. price_parser is a real,
declared project dependency, imported normally here like anywhere else
in the codebase - an earlier version of this file conditionally stubbed
it (for a sandbox with no network access to install it), and that stub
leaked into sys.modules globally, silently breaking every price_parser
call in test_layer0_validator.py for the rest of the same pytest
session. Removed entirely rather than scoped, since price_parser being
genuinely unavailable was never actually true for this project.
"""

import pytest

from layer0_validator import Layer0Validator

_v = Layer0Validator()


def _price(text):
    result = _v.validate_row({"name": "Test Product", "price": text})
    return result.cleaned_row["price"] if result.accepted else None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("₹1.5 lakh", 150000.0),
        ("₹2 Cr", 20000000.0),
        ("Rs 15L", 1500000.0),
        ("Rs. 15 L", 1500000.0),
        ("INR 50k", 50000.0),
        ("₹2.5cr", 25000000.0),
        ("Rs 15 Lakh only", 1500000.0),
        ("Rs. 2 Crores only", 20000000.0),
    ],
)
def test_scale_word_shorthand_is_parsed(text, expected):
    assert _price(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("₹1,499/-", 1499.0),
        ("Rs 999 only", 999.0),
        ("₹1,50,000/-", 150000.0),
        ("₹15L/-", 1500000.0),
    ],
)
def test_trailing_price_tag_noise_is_stripped(text, expected):
    assert _price(text) == expected


def test_correct_indian_grouping_still_works():
    assert _price("₹1,50,000") == 150000.0
    assert _price("₹1,50,00,000") == 15000000.0


def test_legitimate_western_grouping_under_inr_marker_is_accepted():
    """A Shopify-style export priced in INR but grouped 3-3-3 rather
    than 3-2-2 is real data, not garbage - must be accepted via the
    strict-western fallback."""
    assert _price("₹1,500,000") == 1500000.0


def test_malformed_grouping_is_still_rejected_under_both_conventions():
    """The regression this suite exists to prevent: an early version of
    the western-grouping fallback used strict=False, which silently
    accepted "15,0,000" (invalid under EITHER real convention) by just
    stripping the commas - defeating strict mode's whole purpose. Both
    attempts must require real, valid grouping shape."""
    assert _price("Rs 15,0,000") is None
    assert _price("₹1,50,00,00") is None  # malformed Indian (extra digit)


def test_garbage_after_currency_marker_is_rejected():
    assert _price("₹abc") is None
    