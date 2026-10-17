"""
Tests for ucp_money.py.

The currency matrix here isn't arbitrary — it's the project's actual
target set (SEA + India + common fallbacks), reused from the same set
currency_inference.py already scopes to. INR/VND/JPY/KWD are the four the
spec itself uses as worked examples (2/0/0/3 respectively); IDR is
included because it was *wrongly* assumed to be 0-decimal during this
build's own design discussion and Babel corrected that assumption before
any code shipped — this test pins the correction so it can't silently
regress back to a hardcoded guess.
"""

from decimal import Decimal
import pytest

from ucp_money import (
    to_minor_units,
    from_minor_units,
    format_ucp_amount,
    UnknownCurrencyPrecisionError,
)


# --- to_minor_units: the core currency matrix ---

@pytest.mark.parametrize(
    "amount,currency,expected_minor",
    [
        (Decimal("1499.00"), "INR", 149900),   # 2-decimal, the checklist's own example
        (Decimal("1499"), "INR", 149900),
        (Decimal("15000"), "VND", 15000),      # 0-decimal — must NOT become 1500000
        (Decimal("15000.00"), "VND", 15000),
        (Decimal("1499.00"), "IDR", 149900),   # 2-decimal — corrects the "IDR=0" assumption
        (Decimal("500"), "JPY", 500),          # 0-decimal
        (Decimal("12.500"), "KWD", 12500),     # 3-decimal
        (Decimal("9.99"), "USD", 999),
        (Decimal("0"), "USD", 0),
    ],
)
def test_to_minor_units_matrix(amount, currency, expected_minor):
    assert to_minor_units(amount, currency) == expected_minor


def test_to_minor_units_vnd_does_not_inflate_100x():
    """
    The specific bug this module exists to prevent: a hardcoded x100
    applied to a 0-decimal currency inflates the price 100x. This is a
    regression test in the same spirit as
    test_fabrication_from_thin_input_is_caught and
    test_availability_regression.py from the ACP work — pin the exact
    failure mode, not just the happy path.
    """
    vnd_price = Decimal("250000")  # a plausible VND product price
    minor = to_minor_units(vnd_price, "VND")
    assert minor == 250000, (
        f"VND has 0 decimal places; 250000 VND should convert to 250000 "
        f"minor units, not {minor}. A hardcoded x100 would produce 25000000."
    )


def test_to_minor_units_rounding_half_up():
    # 10.005 at 2dp -> rounds to 10.01 (HALF_UP), i.e. 1001 minor units
    assert to_minor_units(Decimal("10.005"), "USD") == 1001


def test_to_minor_units_rejects_float():
    with pytest.raises(TypeError):
        to_minor_units(14.99, "USD")  # float, not Decimal — must be rejected, not silently accepted


def test_to_minor_units_rejects_negative():
    with pytest.raises(ValueError):
        to_minor_units(Decimal("-5.00"), "USD")


def test_to_minor_units_rejects_unknown_currency():
    """
    Regression test for a real bug found while building this module:
    babel.numbers.get_currency_precision("XYZ") does NOT raise — it
    silently returns 2. Relying on that fallback would let an unmapped
    currency_inference.py raw token (see that module's docstring: unmapped
    symbols are stored as a raw token, not guaranteed ISO 4217) silently
    get treated as a real 2-decimal currency. _precision() must validate
    with is_currency() first and never reach get_currency_precision() for
    a code that isn't real.
    """
    with pytest.raises(UnknownCurrencyPrecisionError):
        to_minor_units(Decimal("10.00"), "XYZ")


def test_to_minor_units_rejects_malformed_currency_code():
    with pytest.raises(UnknownCurrencyPrecisionError):
        to_minor_units(Decimal("10.00"), "US")  # 2 chars, not a valid ISO 4217 code


# --- from_minor_units: the inverse ---

@pytest.mark.parametrize(
    "minor,currency,expected_major",
    [
        (149900, "INR", Decimal("1499.00")),
        (15000, "VND", Decimal("15000")),
        (500, "JPY", Decimal("500")),
        (12500, "KWD", Decimal("12.500")),
        (999, "USD", Decimal("9.99")),
    ],
)
def test_from_minor_units_matrix(minor, currency, expected_major):
    assert from_minor_units(minor, currency) == expected_major


def test_from_minor_units_rejects_non_int():
    with pytest.raises(TypeError):
        from_minor_units(149900.0, "INR")


# --- round-trip: catches asymmetric bugs between the two directions ---

@pytest.mark.parametrize("currency", ["INR", "VND", "IDR", "JPY", "KWD", "USD"])
def test_round_trip_stable(currency):
    original = Decimal("1234.00")
    minor = to_minor_units(original, currency)
    back = from_minor_units(minor, currency)
    # Compare as minor units again rather than Decimal equality, since
    # a 0-decimal currency legitimately loses fractional input on the
    # way in (1234.00 VND has no valid fractional VND to begin with).
    assert to_minor_units(back, currency) == minor


# --- format_ucp_amount: the call-site convenience wrapper ---

def test_format_ucp_amount_shape():
    result = format_ucp_amount(Decimal("1499.00"), "inr")  # lowercase in, uppercase out
    assert result == {"amount": 149900, "currency": "INR"}


def test_format_ucp_amount_zero_decimal():
    result = format_ucp_amount(Decimal("15000"), "vnd")
    assert result == {"amount": 15000, "currency": "VND"}
    
    