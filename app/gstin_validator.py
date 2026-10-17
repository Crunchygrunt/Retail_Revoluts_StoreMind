"""
GSTIN (Goods and Services Tax Identification Number) validator.

Standalone, no-LLM, no-network — the same design contract as
Layer0Validator: validate_gstin() never raises, always returns a
GSTINValidationResult with human-readable reasons on rejection.

Scope: this validates a GSTIN's own internal consistency (structure +
check digit). It does NOT confirm the GSTIN is registered, active, or
genuine on the GST portal — that requires a live API call this module
deliberately doesn't make, so it stays offline-first like the rest of
the pipeline. What it catches for free, offline, in constant time: the
overwhelming majority of real-world GSTIN errors, which are typos
(a swapped digit, a dropped character, a stale/surrendered number
copy-pasted from an old document) rather than fraud.

This is a MERCHANT-identity check, not a per-row catalog check — call
it once per seller record (onboarding / seller profile), not per
product row. It's the concrete answer to the identity/trust-gap
question noted as unresolved elsewhere in the project: "should
readiness include a merchant-verification dimension?" GSTIN is a
government-issued, checksummed identifier that answers that
deterministically and offline, unlike the vaguer version of the
question.

Algorithm: ISO 7064 MOD 37-36 family ("Luhn mod 36") over a 36-character
alphabet (0-9 then A-Z). Confirmed against two independently-sourced
worked examples before trusting it (33AAACC1206D1Z -> N,
27AAPFU0939F1Z -> V) - both check out exactly.

Structure decoded from the checksum-valid GSTIN:
    2 digits   state code (not range-validated here - see note below)
    10 chars   embedded PAN (5 letters, 4 digits, 1 letter)
    1 char     entity/registration number for this PAN (1-9, A-Z)
    'Z'        fixed literal
    1 char     check digit

The PAN's own 4th character additionally encodes the holder type
(Individual/Company/Firm/etc.) per the Income Tax Department's
published PAN structure - decoded here since it's essentially free
once the GSTIN structure is parsed, and it's useful merchant metadata
(source: https://www.incometaxindia.gov.in, "How PAN is formed").

Known limitation, left deliberately unimplemented rather than guessed:
state codes are structurally validated as two digits only, not checked
against the actual list of currently-valid GST state/UT codes. That
list has changed over time (new codes added, e.g. for newer UTs) and
hardcoding a table here without an authoritative current source would
be worse than not having one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pan_validator import PAN_HOLDER_TYPES

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_GSTIN_STRUCTURE_PATTERN = re.compile(
    r"^(?P<state_code>\d{2})"
    r"(?P<pan>[A-Z]{5}\d{4}[A-Z])"
    r"(?P<entity_code>[1-9A-Z])"
    r"Z"
    r"(?P<check_digit>[0-9A-Z])$"
)


@dataclass
class GSTINValidationResult:
    input_value: str
    valid: bool
    reasons: list[str] = field(default_factory=list)
    # Populated only when the structure matched (checksum may still
    # have failed - a structurally sound but checksum-invalid GSTIN
    # still yields these, since they're useful to show *what* failed).
    normalized: str | None = None
    state_code: str | None = None
    pan: str | None = None
    entity_code: str | None = None
    holder_type: str | None = None  # e.g. "Company" - decoded PAN[3]
    check_digit_given: str | None = None
    check_digit_expected: str | None = None


def _compute_check_digit(first14: str) -> str:
    """Luhn mod 36 over `first14`. Caller guarantees every character is
    in _CHARSET (enforced by the structure regex before this runs)."""
    total = 0
    for i, ch in enumerate(first14):
        product = _CHARSET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _CHARSET[(36 - total % 36) % 36]


def validate_gstin(raw: str | None) -> GSTINValidationResult:
    """Validates a GSTIN's structure and check digit. Never raises."""
    if not raw or not isinstance(raw, str):
        return GSTINValidationResult(
            input_value=str(raw), valid=False, reasons=["GSTIN is missing or not a string"]
        )

    # Normalize case/whitespace only - never silently correct characters
    # (e.g. O -> 0), since that would mask exactly the typo class this
    # exists to catch.
    normalized = raw.strip().upper()

    match = _GSTIN_STRUCTURE_PATTERN.match(normalized)
    if not match:
        reason = (
            f"GSTIN must be exactly 15 characters: 2-digit state code + "
            f"10-char PAN + entity code + 'Z' + check digit (got "
            f"{len(normalized)} chars: {normalized!r})"
            if len(normalized) != 15
            else f"GSTIN does not match the required structure: {normalized!r}"
        )
        return GSTINValidationResult(input_value=raw, valid=False, reasons=[reason])

    state_code = match.group("state_code")
    pan = match.group("pan")
    entity_code = match.group("entity_code")
    check_digit_given = match.group("check_digit")

    expected_check_digit = _compute_check_digit(normalized[:14])
    holder_type = PAN_HOLDER_TYPES.get(pan[3])

    if check_digit_given != expected_check_digit:
        return GSTINValidationResult(
            input_value=raw,
            valid=False,
            reasons=[
                f"check digit mismatch: GSTIN gives {check_digit_given!r}, "
                f"expected {expected_check_digit!r} — likely a typo, not a "
                f"structurally different GSTIN"
            ],
            normalized=normalized,
            state_code=state_code,
            pan=pan,
            entity_code=entity_code,
            holder_type=holder_type,
            check_digit_given=check_digit_given,
            check_digit_expected=expected_check_digit,
        )

    return GSTINValidationResult(
        input_value=raw,
        valid=True,
        normalized=normalized,
        state_code=state_code,
        pan=pan,
        entity_code=entity_code,
        holder_type=holder_type,
        check_digit_given=check_digit_given,
        check_digit_expected=expected_check_digit,
    )