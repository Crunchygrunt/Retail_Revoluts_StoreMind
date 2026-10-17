"""
Layer-0 validator.

Runs BEFORE any Gemini call. Pure Python, no LLM, no network — cheap and
deterministic. Its job is to keep obviously broken rows from ever reaching
the Catalog Structuring Agent, so a single bad row in the sample catalog
can never crash or silently corrupt the live demo.

Design contract:
    - validate_row() never raises. It always returns a ValidationResult.
    - A row is either ACCEPTED (passed through to Gemini) or REJECTED
      (with one or more human-readable reasons attached).
    - validate_batch() also flags duplicate SKUs across the batch, which
      a single-row check can't catch on its own.
    - Every ACCEPTED row's cleaned_row carries a unique "sku" — the
      row's own sku/id if it had one, otherwise a generated fallback.
      Downstream stages (catalog_agent.py, main.py) key storage writes
      off this field; without it, every sku-less row in a batch used to
      collide on the same literal "unknown" key and silently overwrite
      each other in the store.
    - cleaned_row also carries a "currency" (ISO 4217 code, or the raw
      symbol/token when we can't confidently map it) whenever one is
      detected in the price string. Previously currency was detected
      only to choose a parser, then discarded — every price downstream
      was a bare number, so 125000 could be rupees, rupiah, or anything
      else with no way to tell.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from babel.numbers import NumberFormatError, parse_decimal
from price_parser import Price

# price-parser infers thousands-vs-decimal by counting digits after the
# last separator — a good general-purpose guess when the currency/locale
# is unknown. It's a guess, though, and Indian lakh/crore grouping
# ("1,50,000") isn't in its documented test cases. When we can identify
# INR specifically, there's no need to guess: Babel is told the locale's
# separator convention directly (via CLDR) rather than inferring it, so
# it's evaluated first for anything that looks like a rupee price and
# falls through to price-parser otherwise.
#
# Uses (?<![a-zA-Z])/(?![a-zA-Z]) rather than \b: \b treats digits as
# word characters too, so a plain \b boundary check misses "Rs" or
# "INR" touching a digit with no space (confirmed live: "Rs1,500" and
# "1,500Rs" both slipped past \bRs\b\.?, fell through to price-parser,
# and its own currency token — a bare "Rs", not resolved to INR at
# all — ended up as the stored currency). Excluding adjacent LETTERS
# specifically (not digits) catches those while still correctly
# rejecting "Rs" as part of an unrelated word, e.g. "Mrs. Smith".
_INR_MARKER_PATTERN = re.compile(
    r"₹|(?<![a-zA-Z])Rs\.?(?![a-zA-Z])|(?<![a-zA-Z])INR(?![a-zA-Z])", re.IGNORECASE
)

# India-first pre-normalization: the previous INR path only handled
# well-formed grouped numbers ("1,50,000"). Confirmed live against
# real Indian listing conventions that it did NOT handle — every one
# of these was silently REJECTED outright, never even reaching Gemini:
#   "1.5 lakh", "2 Cr", "15L", "50k"   (scale-word shorthand)
#   "1,499/-", "999 only"             (trailing price-tag noise)
# This is a closed, small vocabulary — Indian listings use almost
# exclusively these words — so a vendored regex is lower-risk here
# than taking on a new dependency for five fixed tokens. (doshomik,
# an open-source lakh/crore parser, was evaluated and explicitly
# supports English "lakh"/"crore"/"cr" tokens — a reasonable
# alternative if this vocabulary ever needs to grow past what's
# below, but it's Bangla/BDT-oriented and an extra dependency for a
# closed five-word set felt like the wrong trade for now.)
_INR_SCALE_WORDS: dict[str, str] = {
    "cr": "10000000", "crore": "10000000", "crores": "10000000",
    "l": "100000", "lac": "100000", "lacs": "100000",
    "lakh": "100000", "lakhs": "100000",
    "k": "1000",
}
# Matched only against an already currency-marker-stripped, otherwise
# isolated price string — never against free text like a name field —
# so a short token like bare "l" or "k" has no realistic collision risk.
_INR_SCALE_WORD_PATTERN = re.compile(
    r"^(?P<num>[\d.,\s]+)\s*(?P<scale>cr\.?|crores?|lacs?|lakhs?|l|k)\.?$",
    re.IGNORECASE,
)
# "/-", "-/-", trailing "only" — Indian price-tag conventions with no
# numeric meaning of their own; stripped before scale-word matching.
_INR_TRAILING_NOISE_PATTERN = re.compile(r"(/-|-/-)\s*$|\bonly\b\.?\s*$", re.IGNORECASE)

REQUIRED_FIELDS = ("name", "price")
MAX_NAME_LEN = 200
MAX_DESC_LEN = 5000

# Maps the raw currency token price-parser extracts (or our own INR
# detection) to an ISO 4217 code. Scoped deliberately to the currencies
# this product actually targets (SEA + India + a common fallback) —
# there's no general "symbol -> ISO code" library because the mapping
# is genuinely ambiguous without a locale/country signal (e.g. "$"
# alone is used by 20+ currencies). An unrecognized token is returned
# as-is rather than discarded: "K" or "Kč" on a listing is still more
# useful than no currency information at all.
_CURRENCY_TOKEN_TO_ISO = {
    "rp": "IDR",
    "₫": "VND",
    "฿": "THB",
    "₭": "LAK",
    "k": "MMK",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
}


def _resolve_currency(raw_token: str | None) -> str | None:
    if not raw_token:
        return None
    token = raw_token.strip()
    return _CURRENCY_TOKEN_TO_ISO.get(token.lower(), token)


@dataclass
class _ParsedPrice:
    amount: float | None
    currency: str | None = None


@dataclass
class ValidationResult:
    row_id: str
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    cleaned_row: dict[str, Any] | None = None


class Layer0Validator:
    def __init__(
        self,
        required_fields: tuple[str, ...] = REQUIRED_FIELDS,
        max_name_len: int = MAX_NAME_LEN,
        max_desc_len: int = MAX_DESC_LEN,
    ) -> None:
        self.required_fields = required_fields
        self.max_name_len = max_name_len
        self.max_desc_len = max_desc_len

    # ---- single row -----------------------------------------------------

    def validate_row(self, row: dict[str, Any], row_id: str | None = None) -> ValidationResult:
        reasons: list[str] = []
        # Falling back to a literal "unknown" here used to mean every
        # sku-less row in a batch collided on the exact same identifier
        # downstream (catalog_agent.py does the same "sku or id or
        # 'unknown'" fallback on the cleaned row) — confirmed live: 4
        # sku-less rows in one /ingest batch all structured and scored
        # correctly, but only 1 survived in the store, because each
        # write silently overwrote the last. A uuid-based fallback here
        # guarantees uniqueness for direct validate_row() calls; batch
        # calls pass their own per-index row_id in (see validate_batch),
        # which takes priority via the `row_id or ...` short-circuit.
        row_id = row_id or str(row.get("sku") or row.get("id") or f"row-{uuid.uuid4().hex[:8]}")

        if not isinstance(row, dict):
            return ValidationResult(row_id, accepted=False, reasons=["row is not a record"])

        # 1. required fields present and non-empty
        for f in self.required_fields:
            val = row.get(f)
            if val is None or (isinstance(val, str) and not val.strip()):
                reasons.append(f"missing required field: {f}")

        # 2. name sanity
        name = row.get("name")
        if isinstance(name, str):
            if len(name.strip()) == 0:
                reasons.append("name is empty")
            elif len(name) > self.max_name_len:
                reasons.append(f"name exceeds {self.max_name_len} chars")
            elif self._looks_mojibake(name):
                reasons.append("name contains likely encoding corruption")

        # 3. price sanity — must parse to a positive number
        price = row.get("price")
        normalized_price = None
        currency = None
        if price is not None:
            parsed_price = self._normalize_price(price)
            normalized_price = parsed_price.amount
            currency = parsed_price.currency
            if normalized_price is None:
                reasons.append(f"price is not a parseable number: {price!r}")
            elif normalized_price <= 0:
                reasons.append(f"price must be positive: {normalized_price}")

        # 4. description sanity (optional field, but if present must be sane)
        desc = row.get("description")
        if isinstance(desc, str) and len(desc) > self.max_desc_len:
            reasons.append(f"description exceeds {self.max_desc_len} chars")

        # 5. image reference sanity (optional, but flag empty strings vs missing)
        image = row.get("image") or row.get("image_url")
        if image is not None and isinstance(image, str) and not image.strip():
            reasons.append("image field present but empty")

        accepted = len(reasons) == 0
        cleaned_row = None
        if accepted:
            cleaned_row = dict(row)
            if normalized_price is not None:
                cleaned_row["price"] = normalized_price
            if currency is not None:
                cleaned_row.setdefault("currency", currency)
            if isinstance(name, str):
                cleaned_row["name"] = name.strip()
            # row_id is already unique (the row's real sku/id, or a
            # generated fallback) — carry it forward as "sku" so
            # catalog_agent.py's own "sku or id or 'unknown'" fallback
            # resolves to something unique instead of colliding with
            # every other sku-less row in the batch. setdefault so a
            # row's own explicit sku is never touched.
            cleaned_row.setdefault("sku", row_id)

        return ValidationResult(row_id, accepted=accepted, reasons=reasons, cleaned_row=cleaned_row)

    # ---- batch ------------------------------------------------------------

    def validate_batch(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[ValidationResult], list[ValidationResult]]:
        """Returns (accepted_results, rejected_results). Also rejects rows
        whose SKU duplicates an earlier row in the same batch — a Gemini
        structuring pass can't detect that on its own since it only ever
        sees one row at a time."""
        results: list[ValidationResult] = []
        seen_skus: dict[str, int] = {}
        # One token per batch (i.e. per /ingest call). A plain f"row_{i}"
        # fallback fixes collisions WITHIN one batch but not ACROSS two —
        # confirmed live: two separate 4-row sku-less batches both
        # generated row_0..row_3, and the second batch silently
        # overwrote the first, product for product, in the store.
        batch_token = uuid.uuid4().hex[:8]

        for i, row in enumerate(rows):
            row_id = str(row.get("sku") or row.get("id") or f"row-{batch_token}-{i}")
            result = self.validate_row(row, row_id=row_id)

            sku = row.get("sku")
            if sku:
                if sku in seen_skus:
                    result.accepted = False
                    result.reasons.append(f"duplicate sku (first seen at row {seen_skus[sku]})")
                else:
                    seen_skus[sku] = i

            results.append(result)

        accepted = [r for r in results if r.accepted]
        rejected = [r for r in results if not r.accepted]
        return accepted, rejected

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _normalize_price(raw: Any) -> _ParsedPrice:
        """Parses a raw price string into an amount AND a currency.

        Currency was previously detected only to pick a parser, then
        thrown away — the amount alone was stored everywhere downstream
        (structured output, storage, the Products table), so a bare
        `125000` could be rupees, rupiah, or anything else with no way
        to tell. price-parser already extracts a currency token as part
        of parsing (`Price.fromstring("Rp 1.550.000").currency ==
        'Rp'`) — it just wasn't being read before.

        INR is detected and routed to Babel (https://babel.pocoo.org/,
        CLDR-backed) with locale="en_IN" and strict=True — strict mode
        specifically validates the lakh/crore grouping rather than just
        stripping separators, so a malformed price like "15,0,000" is
        rejected rather than silently guessed at (confirmed live: it's
        rejected with a suggested correction). Everything else goes
        through price-parser (https://github.com/scrapinghub/price-parser)
        as before — it disambiguates thousands vs. decimal separators
        generically, proven against 900+ real-world price strings
        across locales, which is what's needed when the currency isn't
        identifiable up front.
        """
        if isinstance(raw, (int, float)):
            return _ParsedPrice(amount=float(raw))
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return _ParsedPrice(amount=None)
            is_negative = text.startswith("-")
            text_unsigned = text[1:] if is_negative else text

            if _INR_MARKER_PATTERN.search(text_unsigned):
                numeric_text = _INR_MARKER_PATTERN.sub("", text_unsigned).strip()
                value = Layer0Validator._parse_inr_numeric(numeric_text)
                if value is None:
                    return _ParsedPrice(amount=None)
                currency = "INR"
            else:
                parsed = Price.fromstring(text_unsigned)
                if parsed.amount is None:
                    return _ParsedPrice(amount=None)
                value = float(parsed.amount)
                currency = _resolve_currency(parsed.currency)

            return _ParsedPrice(amount=(-value if is_negative else value), currency=currency)
        return _ParsedPrice(amount=None)

    @staticmethod
    def _parse_inr_numeric(numeric_text: str) -> float | None:
        """Parses an already currency-marker-stripped INR numeric string,
        handling the two conventions plain parse_decimal(locale="en_IN")
        can't: scale-word shorthand ("1.5 lakh", "2 Cr", "15L", "50k")
        and trailing price-tag noise ("1,499/-", "999 only"). Confirmed
        live: before this, every one of those forms was silently
        rejected outright by the strict-grouping check below, never
        reaching Gemini.

        Strategy:
          1. Strip trailing noise, then a trailing scale word if present
             (recording its multiplier — 1 if none was found).
          2. Try strict lakh/crore grouping (en_IN) on what's left.
          3. If that fails, retry as STRICT (not naive-separator-strip)
             western grouping. This matters: a non-strict retry would
             silently accept genuinely malformed groupings like
             "15,0,000" by just stripping the commas — confirmed live,
             this defeats the exact malformed-grouping protection
             strict=True exists for. Requiring strict shape validation
             under BOTH conventions means a price is only ever accepted
             if it's a properly formed number under at least one of
             them, never merely "digits with some commas somewhere."
          4. Multiply by the scale-word multiplier (identity if none).

        Case 3's outcome isn't currently surfaced as a confidence flag
        the way currency_inferred is (e.g. for legitimate western-
        grouped INR from a Shopify-style export) — worth adding to
        cleaned_row / the decision log if that distinction matters
        downstream.
        """
        text = _INR_TRAILING_NOISE_PATTERN.sub("", numeric_text).strip()
        multiplier = Decimal("1")
        scale_match = _INR_SCALE_WORD_PATTERN.match(text)
        if scale_match:
            multiplier = Decimal(_INR_SCALE_WORDS[scale_match.group("scale").lower().rstrip(".")])
            text = scale_match.group("num").strip()

        for locale, strict in (("en_IN", True), ("en_US", True)):
            try:
                return float(parse_decimal(text, locale=locale, strict=strict) * multiplier)
            except NumberFormatError:
                continue
        return None

    @staticmethod
    def _looks_mojibake(text: str) -> bool:
        """Cheap heuristic: a run of replacement/control characters, or a
        suspiciously high ratio of non-printable characters, usually means
        an encoding step upstream (e.g. a bad CSV export) mangled the text."""
        if "\ufffd" in text:
            return True
        non_printable = sum(
            1 for ch in text if unicodedata.category(ch) in ("Cc", "Cn") and ch not in "\t\n"
        )
        return non_printable > max(1, len(text) // 10)
    