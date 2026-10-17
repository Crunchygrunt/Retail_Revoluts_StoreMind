"""
UPI VPA and Indian-mobile-number format checks — DELIBERATELY WEAK
SIGNALS, not identity verification. Read this before using either.

Neither of these has anything resembling GSTIN's checksum. A UPI VPA
and a phone number are just plausible-looking strings anyone could
type, whether they own the account/number or not — unlike a
checksummed ID, there's no mathematical self-consistency to catch a
fabricated one. That's why every result field here is named
`*_format_valid`, never `*_verified`: format-valid means "this could
plausibly be real," not "this was confirmed to be real."

Real verification of either requires a LIVE call this module
deliberately does not make:
  - UPI: an actual penny-drop / VPA-validate API against a licensed
    Payment Aggregator or bank. Worth flagging: NPCI is deprecating the
    "UPI Collect" flow entirely, effective Feb 28 2026, per Razorpay's
    own current docs — several VPA-validation vendor APIs are built on
    that exact flow. That's a live rail in flux, independent of
    whether StoreMind calls it today.
  - WhatsApp: Meta's Graph API / WhatsApp Business Platform, which
    needs developer app registration and review, and returns a fuzzy
    "verified badge present" signal rather than a deterministic match.

VPA format (`identifier@psp-handle`) is NOT a government-published
spec — there's no equivalent of GSTIN's CLDR/ISO-7064 authority here.
It's the de facto convention used across the UPI ecosystem (NPCI's own
"9999999999@upi", Razorpay/PayU's own docs use the identical shape).
Treat the regex below as a reasonable practical pattern, not an
authoritative rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# identifier: 2-256 chars, letters/digits/./-/_ (covers both phone-number-
# style and name-style VPAs seen across NPCI/PayU/Razorpay examples).
# handle: letters only, matching real-world PSP handles (upi, ybl, oksbi,
# okhdfcbank, paytm, ibl, axl, ...) - never validated against an
# enumerated handle list, since PSP handles are added/retired over time
# and no authoritative current list was found to source one from.
_VPA_PATTERN = re.compile(r"^[\w.\-]{2,256}@[a-zA-Z]{2,64}$")

# E.164-style Indian mobile: optional +91/91 prefix, then a 10-digit
# number starting 6-9 (the range TRAI allocates to mobile numbers).
_INDIAN_MOBILE_PATTERN = re.compile(r"^(?:\+?91[\-\s]?)?([6-9]\d{9})$")


@dataclass
class FormatCheckResult:
    input_value: str
    format_valid: bool
    reasons: list[str] = field(default_factory=list)
    normalized: str | None = None


def check_upi_vpa_format(raw: str | None) -> FormatCheckResult:
    """Format-only check for a UPI VPA. See module docstring — this is
    NOT payment-rail verification."""
    if not raw or not isinstance(raw, str):
        return FormatCheckResult(
            input_value=str(raw), format_valid=False, reasons=["UPI ID is missing or not a string"]
        )
    normalized = raw.strip().lower()
    if not _VPA_PATTERN.match(normalized):
        return FormatCheckResult(
            input_value=raw,
            format_valid=False,
            reasons=[f"UPI ID does not match the identifier@handle format: {normalized!r}"],
        )
    return FormatCheckResult(input_value=raw, format_valid=True, normalized=normalized)


def check_whatsapp_phone_format(raw: str | None) -> FormatCheckResult:
    """Format-only check that a string is a plausible Indian mobile
    number. See module docstring — this does NOT confirm a WhatsApp
    Business account exists, is active, or is verified by Meta."""
    if not raw or not isinstance(raw, str):
        return FormatCheckResult(
            input_value=str(raw), format_valid=False, reasons=["Phone number is missing or not a string"]
        )
    stripped = raw.strip()
    match = _INDIAN_MOBILE_PATTERN.match(stripped.replace(" ", ""))
    if not match:
        return FormatCheckResult(
            input_value=raw,
            format_valid=False,
            reasons=[f"Not a plausible Indian mobile number: {stripped!r}"],
        )
    return FormatCheckResult(input_value=raw, format_valid=True, normalized=f"+91{match.group(1)}")
