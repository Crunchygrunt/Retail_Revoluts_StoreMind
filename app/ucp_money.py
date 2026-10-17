"""
UCP monetary amount conversion.

UCP (2026-04-08) represents all monetary amounts as integers in the
currency's minor unit, per ISO 4217. The minor-to-major ratio is NOT
uniform — it depends on the currency's exponent, which Babel/CLDR exposes
via get_currency_precision(). Do not hardcode a ×100 assumption: several
of this project's target currencies are not 2-decimal (VND and JPY are 0;
KWD is 3), and getting this wrong is a silent, compounding money bug, not
a crash.

Spec reference (ucp.dev/specification/reference/):
  "Monetary amount in the currency's minor unit as defined by ISO 4217.
   Refer to the currency's exponent to determine minor-to-major ratio
   (e.g., 2 for USD, 0 for JPY, 3 for KWD)."
"""

from decimal import Decimal, ROUND_HALF_UP

from babel.numbers import get_currency_precision, is_currency


class UnknownCurrencyPrecisionError(ValueError):
    """Raised when the given code is not a real ISO 4217 currency Babel recognizes."""


def _precision(currency: str) -> int:
    """
    IMPORTANT, found by actually running this against Babel rather than
    trusting the docstring: get_currency_precision() does NOT raise for
    an unrecognized code — it silently returns 2 (e.g. get_currency_precision
    ("XYZ") == 2). That fallback is dangerous specifically for this project:
    currency_inference.py's own design note records that an unmapped
    currency symbol is stored as a raw, non-ISO token rather than guaranteed
    ISO 4217. Silently treating a raw token as a real currency at precision 2
    is the same class of bug as the fabrication-blindness readiness-score
    issue already fixed elsewhere in this project: plausible-looking output
    masking bad input. So validity is checked explicitly with is_currency(),
    which is itself case-sensitive (only "INR" passes, not "inr") — this
    function upper()s first so callers can pass either case, while a
    genuinely non-currency token still fails regardless of case.
    """
    if not currency or not isinstance(currency, str):
        raise UnknownCurrencyPrecisionError(
            f"'{currency}' is not a recognized ISO 4217 currency code."
        )
    normalized = currency.upper()
    if not is_currency(normalized):
        raise UnknownCurrencyPrecisionError(
            f"'{currency}' is not a recognized ISO 4217 currency code. "
            "If this came from currency_inference.py's raw-token fallback "
            "for an unmapped symbol, it must be resolved to a real ISO code "
            "before reaching the UCP layer — do not guess a precision for it."
        )
    return get_currency_precision(normalized)


def to_minor_units(amount: Decimal, currency: str) -> int:
    """
    Convert a major-unit decimal amount to a UCP minor-unit integer.

    amount: Decimal (NOT float — float would reintroduce the exact class
            of rounding bug length_repair.py and currency_inference.py
            were already written carefully to avoid elsewhere in this
            project). Callers passing a float or str get an explicit
            error, not a silently-imprecise conversion.
    currency: ISO 4217 code, e.g. "INR", "VND", "JPY".

    Rounding: HALF_UP at the currency's own precision, applied to a
    Decimal input — this is deterministic and auditable, unlike the
    float double-rounding that binary floating point invites here.
    This module intentionally does not accept negative amounts as
    "prices" (UCP's own `amount` field in totals-lines allows negative
    values for discounts, but that's a totals-array concern, not this
    per-item conversion; discounts are explicitly Tier C / not built).
    """
    if not isinstance(amount, Decimal):
        raise TypeError(
            f"to_minor_units requires a Decimal amount, got {type(amount).__name__}. "
            "Convert with Decimal(str(x)) at the call site, never Decimal(float)."
        )
    if amount < 0:
        raise ValueError(f"to_minor_units received a negative amount: {amount}")

    exponent = _precision(currency)
    scale = Decimal(10) ** exponent
    scaled = (amount * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled)


def from_minor_units(amount: int, currency: str) -> Decimal:
    """
    Inverse of to_minor_units. Returns a Decimal at the currency's own
    precision (e.g. Decimal("14.99") for 1499 USD, Decimal("15000") for
    15000 VND — not "150.00").
    """
    if not isinstance(amount, int):
        raise TypeError(
            f"from_minor_units requires an int, got {type(amount).__name__}"
        )
    exponent = _precision(currency)
    scale = Decimal(10) ** exponent
    return (Decimal(amount) / scale).quantize(
        Decimal(1).scaleb(-exponent) if exponent else Decimal(1)
    )


def format_ucp_amount(amount: Decimal, currency: str) -> dict:
    """
    Convenience wrapper matching the shape UCP line items / totals expect:
        {"amount": <int minor units>, "currency": "INR"}
    Mirrors the existing format_acp_price() pattern in acp_schema.py, so
    call sites can follow the same convention across both protocol layers.
    """
    return {
        "amount": to_minor_units(amount, currency),
        "currency": currency.upper(),
    }
    