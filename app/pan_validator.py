"""
PAN (Permanent Account Number) validator — STRUCTURAL ONLY, no checksum.

Deliberately does NOT claim a checksum check, unlike gstin_validator.py.
Several blog sources claim PAN's 10th character is a Luhn-algorithm
check digit. Checked before trusting that: python-stdnum — a
well-maintained, widely-used open-source library implementing checksum
validators for hundreds of national ID formats — explicitly states, in
its own PAN module, that "the last character is computed by an
undocumented checksum algorithm" and validates PAN by STRUCTURE ONLY,
declining to implement a checksum check at all. A forum thread
attempting to reverse-engineer one explicitly says "not sure if it's
accurate." Given gstin_validator.py's algorithm was only trusted after
two independently-sourced worked examples both checked out, an
unverified guess doesn't clear that bar here. So: structure only.

This is the SHARED source of truth for PAN structure and holder-type
decoding — gstin_validator.py imports PAN_HOLDER_TYPES from here rather
than keeping its own copy, since a GSTIN's positions 3-12 ARE a PAN and
the two must never drift out of sync.

Structure: 5 letters, 4 digits, 1 letter (10 chars total).
    AAAAA9999A
    ^^^^^          first 3: alphabetic series (no individual meaning)
         ^         4th: holder type — see PAN_HOLDER_TYPES
          ^        5th: first letter of surname/entity name
           ^^^^    sequential number, 0001-9999
               ^   check digit — NOT verified here, see docstring above

Source for the PAN_HOLDER_TYPES mapping: Income Tax Department's own
published FAQ on PAN structure (incometaxindia.gov.in).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PAN_STRUCTURE_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

PAN_HOLDER_TYPES = {
    "A": "Association of Persons (AOP)",
    "B": "Body of Individuals (BOI)",
    "C": "Company",
    "F": "Firm / Limited Liability Partnership",
    "G": "Government Agency",
    "H": "Hindu Undivided Family (HUF)",
    "J": "Artificial Juridical Person",
    "L": "Local Authority",
    "P": "Individual",
    "T": "Trust",
}


@dataclass
class PANValidationResult:
    input_value: str
    valid: bool
    reasons: list[str] = field(default_factory=list)
    normalized: str | None = None
    holder_type: str | None = None


def validate_pan(raw: str | None) -> PANValidationResult:
    """Validates PAN STRUCTURE only. Never raises. A structurally valid
    result does NOT mean the PAN is real or registered — see module
    docstring for why no checksum is checked."""
    if not raw or not isinstance(raw, str):
        return PANValidationResult(
            input_value=str(raw), valid=False, reasons=["PAN is missing or not a string"]
        )

    normalized = raw.strip().upper()
    if not _PAN_STRUCTURE_PATTERN.match(normalized):
        reason = (
            f"PAN must be exactly 10 characters: 5 letters + 4 digits + 1 "
            f"letter (got {len(normalized)} chars: {normalized!r})"
            if len(normalized) != 10
            else f"PAN does not match the required structure (5 letters, "
            f"4 digits, 1 letter): {normalized!r}"
        )
        return PANValidationResult(input_value=raw, valid=False, reasons=[reason])

    return PANValidationResult(
        input_value=raw,
        valid=True,
        normalized=normalized,
        holder_type=PAN_HOLDER_TYPES.get(normalized[3]),
    )