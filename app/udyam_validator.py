"""
Udyam Registration Number validator — STRUCTURAL ONLY, no checksum, no
public verification API.

Why Udyam matters here specifically: RBI's own circular on the Udyam
Assist Platform (UAP) defines an "Informal Micro Enterprise" as one
"unable to get registered on the Udyam Registration Portal... due to
lack of... PAN or GSTIN" — i.e. this is the Indian government's own
program built for exactly the segment neither GSTIN nor PAN can
verify. UAP itself has no self-serve public API either — registration
and lookup both go through Aadhaar-linked OTP flows on
udyamregistration.gov.in, via a bank/NBFC/MFI acting as a "Designated
Agency." So like GSTIN's live-lookup problem, there's no free official
API here — only the format is checkable offline.

Format confirmed directly from the official portal's own login-form
field description (udyamregistration.gov.in): "Udyam should have first
5 letters, followed by one (-) after that 2 letters, followed by one
(-) after that 2 digits, followed by one (-) and last 7 of numeric."

    UDYAM-XX-00-0000000
    ^^^^^                 fixed literal prefix
          ^^               2-letter state code
             ^^            2-digit district code
                ^^^^^^^    7-digit sequence number

No checksum digit exists in the published format at all — this is a
strictly weaker guarantee than GSTIN's. It confirms an input LOOKS like
a real Udyam number; it says nothing about whether that specific number
was actually issued.

State code is validated as 2 letters only, NOT checked against an
enumerated list — same reasoning as gstin_validator.py's state-code
caveat: a table risks going stale without an authoritative, current
source, and Udyam's letter-based state codes are a different encoding
than GSTIN's numeric ones, so GSTIN's table (even the one now sourced
from python-stdnum) doesn't transfer here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_UDYAM_STRUCTURE_PATTERN = re.compile(
    r"^UDYAM-(?P<state_code>[A-Z]{2})-(?P<district_code>\d{2})-(?P<sequence>\d{7})$"
)


@dataclass
class UdyamValidationResult:
    input_value: str
    valid: bool
    reasons: list[str] = field(default_factory=list)
    normalized: str | None = None
    state_code: str | None = None
    district_code: str | None = None


def validate_udyam(raw: str | None) -> UdyamValidationResult:
    """Validates Udyam Registration Number STRUCTURE only. Never raises.
    No checksum exists to verify, and no public API confirms real
    issuance — see module docstring."""
    if not raw or not isinstance(raw, str):
        return UdyamValidationResult(
            input_value=str(raw), valid=False, reasons=["Udyam number is missing or not a string"]
        )

    normalized = raw.strip().upper()
    match = _UDYAM_STRUCTURE_PATTERN.match(normalized)
    if not match:
        return UdyamValidationResult(
            input_value=raw,
            valid=False,
            reasons=[
                f"Udyam Registration Number must match UDYAM-XX-00-0000000 "
                f"(2-letter state code, 2-digit district code, 7-digit "
                f"sequence): got {normalized!r}"
            ],
        )

    return UdyamValidationResult(
        input_value=raw,
        valid=True,
        normalized=normalized,
        state_code=match.group("state_code"),
        district_code=match.group("district_code"),
    )
    