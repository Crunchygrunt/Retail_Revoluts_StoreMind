"""
Seller profile — the merchant-level facts ACP requires that aren't
per-product, PLUS seller-identity verification for the fragmented,
often-GSTIN-less India segment this product targets.

Why this exists: the ACP feed spec requires `seller_name` on every row,
and requires `seller_privacy_policy` / `seller_tos` for checkout
eligibility. None of these are catalog data — they're the same for every
product a given seller uploads — so they belong in configuration, not in
each row.

This module also supplies the fix for a real spec violation in the
earlier build. The ACP spec states plainly: "Use a real brand and seller
name, not placeholders" and "Do not use placeholder strings such as
`null`, `unknown`, or `n/a`". The previous brand fallback emitted the
literal string "Unbranded", which is exactly that kind of placeholder and
would be rejected or down-ranked as a generic value. The correct
fallback for a seller with no manufacturer brand is the seller's OWN
store or business name — a real, specific, verifiable string. That is
standard practice for private-label, handmade and long-tail sellers,
where the seller genuinely IS the brand.

Config comes from the environment, matching this project's existing
convention (STOREMIND_LLM_BACKEND, STOREMIND_STORE_BACKEND, ...):

    STOREMIND_SELLER_NAME            required for ACP conformance
    STOREMIND_SELLER_URL             optional; seller storefront/profile page
    STOREMIND_SELLER_PRIVACY_POLICY  required for checkout eligibility
    STOREMIND_SELLER_TOS             required for checkout eligibility
    STOREMIND_SELLER_GSTIN           optional — identity verification, see below
    STOREMIND_SELLER_PAN             optional — identity verification, see below
    STOREMIND_SELLER_UDYAM           optional — identity verification, see below
    STOREMIND_SELLER_UPI_ID          optional — identity verification, see below
    STOREMIND_SELLER_WHATSAPP_PHONE  optional — identity verification, see below

    -- ONDC (ondc_mapping.py / ondc_eligibility.py) --
    STOREMIND_SELLER_IS_PACKER          "true" if the seller packs their
                                         own goods (handmade, home-packed) —
                                         see resolved_packer_name below
    STOREMIND_SELLER_PACKER_NAME        optional override; defaults to
                                         seller name when is_packer is true
    STOREMIND_SELLER_PACKER_ADDRESS     required for ONDC statutory
                                         declarations when is_packer is
                                         true — no honest fallback exists
    STOREMIND_SELLER_CONSUMER_CARE_NAME/_EMAIL/_PHONE
                                         all three required together for
                                         @ondc/org/contact_details_consumer_care
    STOREMIND_SELLER_RETURNABLE         "true"/"false" — store-wide policy
    STOREMIND_SELLER_CANCELLABLE        "true"/"false" — store-wide policy
    STOREMIND_SELLER_RETURN_WINDOW_DAYS integer, e.g. 7
    STOREMIND_SELLER_TIME_TO_SHIP_HOURS integer, e.g. 24

Deliberately NOT defaulted to anything invented. If the operator hasn't
configured a seller name, the honest outcome is an unresolved field
reported as a gap with a specific remediation message — not a fabricated
one. That's the same principle already applied to `image_url`.

--- Seller identity verification: EITHER-OR, not GSTIN-only ---

GSTIN-only verification would exclude the exact segment this product
targets. Confirmed: mandatory GST registration is ₹40 lakh/yr (goods) or
₹20 lakh/yr (services) turnover in most states — well above what a
typical WhatsApp/Instagram micro-seller does. RBI's own definition of an
"Informal Micro Enterprise" (for the Udyam Assist Platform) is an
enterprise "unable to get registered... due to lack of... PAN or GSTIN"
— i.e. even PAN isn't universal at the bottom of this segment.

So verification accepts ANY ONE of four paths (see _resolve_verification
below): GSTIN alone, Udyam alone, UPI+PAN together, or UPI+WhatsApp-phone
together. These are NOT equally strong signals:
    - GSTIN: structure + a real Luhn-mod-36 checksum (gstin_validator.py)
    - Udyam: structure only, no checksum exists in the published format,
      no public verification API — but it's the government's own
      program for the GSTIN/PAN-exempt segment (see RBI circular above)
    - PAN: structure only — checked whether a checksum exists before
      claiming one; python-stdnum, a maintained open-source library
      that DOES implement checksums for hundreds of ID formats, calls
      PAN's own check digit "an undocumented checksum algorithm" and
      validates structure only. So do we.
    - UPI ID / WhatsApp phone: format-only checks with NO checksum at
      all (see seller_contact_validators.py) — these are the weakest
      signals here, which is exactly why they're only ever accepted as
      a PAIR, never alone.

No live API is called anywhere in this module — no GST portal lookup, no
UPI penny-drop, no Meta Graph API call. Every check above is offline,
free, and deterministic. Real live verification (if ever added) is a
separate, explicit opt-in — see the "gstin verify backend" discussion
elsewhere in this project — and was deliberately left out of THIS
module's scope, not overlooked.

Unset is always fine (no field configured at all never blocks
anything — same graceful degradation as a missing seller_name). SET but
not adding up to a verified identity DOES block — see
SellerProfile.blocks_ingestion. That distinction (missing vs. wrong)
matters: an invalid identity field is the same severity class as a
malformed price string, not a missing optional field.

--- ONDC: seller-as-packer, consumer care, fulfillment policy ---

ONDC's statutory declarations (@ondc/org/statutory_reqs_packaged_commodities)
require a manufacturer_or_packer_name and address on every packaged-
commodities item — confirmed mandatory from real ONDC certification
feedback, not guessed. For a reseller of branded goods (Nike shoes, a
Bru coffee pouch) the seller is NOT the packer — the actual
manufacturer/importer must be configured, and StoreMind has no way to
know that, so it's a per-product gap, never a guess. But for the
handmade/home-packed segment this product targets (a home-stitched
kurta, home-packed spices), the seller genuinely IS the packer — same
reasoning as the brand -> store-name fallback above. is_packer is an
explicit, store-wide opt-in for exactly that case:
STOREMIND_SELLER_IS_PACKER=true makes resolved_packer_name fall back to
the seller's own name, honestly, because that's who actually packed it
— it is NEVER assumed by default, since assuming it for a branded
reseller would put a false manufacturer claim into a legal declaration.

Consumer-care contact and fulfillment policy (returnable/cancellable/
return_window/time_to_ship) are store-wide config for the same reason
privacy_policy/tos already are: a seller sets one return policy for
their whole store, not per product. Genuinely required by ONDC's
schema, not optional extras — an item missing them fails real ONDC
validation, so ondc_eligibility.py's is_ondc_listable depends on these
being configured, exactly mirroring how UCP's own eligibility depends
on a valid Price object existing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from gstin_validator import validate_gstin
from pan_validator import validate_pan
from seller_contact_validators import check_upi_vpa_format, check_whatsapp_phone_format
from udyam_validator import validate_udyam


def _resolve_verification(
    gstin_valid: bool | None,
    udyam_valid: bool | None,
    pan_valid: bool | None,
    upi_format_valid: bool | None,
    whatsapp_format_valid: bool | None,
) -> tuple[bool, str | None]:
    """Either-or seller verification. Returns (verified, method).

    Checked strongest-first so `method` reports the strongest path that
    actually applied when more than one would have worked — NOT because
    a later path is invalid on its own terms, they're just weaker
    evidence (see module docstring for exactly how much weaker each is).
    """
    if gstin_valid is True:
        return True, "gstin"
    if udyam_valid is True:
        return True, "udyam"
    if upi_format_valid is True and pan_valid is True:
        return True, "upi+pan"
    if upi_format_valid is True and whatsapp_format_valid is True:
        return True, "upi+whatsapp"
    return False, None


@dataclass(frozen=True)
class SellerProfile:
    name: str | None
    url: str | None
    privacy_policy: str | None
    tos: str | None

    # -- GSTIN: structure + checksum, the strongest single signal --
    gstin: str | None = None
    gstin_valid: bool | None = None
    gstin_reasons: tuple[str, ...] = ()
    gstin_holder_type: str | None = None

    # -- Udyam: structure only, but the best-fit program for the
    #    GSTIN/PAN-exempt segment (see module docstring) --
    udyam: str | None = None
    udyam_valid: bool | None = None
    udyam_reasons: tuple[str, ...] = ()

    # -- PAN: structure only, no confirmed checksum exists --
    pan: str | None = None
    pan_valid: bool | None = None
    pan_reasons: tuple[str, ...] = ()
    pan_holder_type: str | None = None

    # -- UPI ID / WhatsApp phone: format-only, weakest signals, only
    #    ever accepted as a PAIR (see _resolve_verification) --
    upi_id: str | None = None
    upi_format_valid: bool | None = None
    upi_reasons: tuple[str, ...] = ()
    whatsapp_phone: str | None = None
    whatsapp_format_valid: bool | None = None
    whatsapp_reasons: tuple[str, ...] = ()

    # -- resolved outcome --
    verified: bool = False
    verification_method: str | None = None  # "gstin" | "udyam" | "upi+pan" | "upi+whatsapp" | None

    # -- ONDC: packer identity, consumer care, fulfillment policy.
    #    Store-wide, never fabricated — see module docstring. --
    is_packer: bool = False
    packer_name: str | None = None
    packer_address: str | None = None
    consumer_care_name: str | None = None
    consumer_care_email: str | None = None
    consumer_care_phone: str | None = None
    returnable: bool | None = None
    cancellable: bool | None = None
    return_window_days: int | None = None
    time_to_ship_hours: int | None = None

    @property
    def resolved_packer_name(self) -> str | None:
        """Explicit packer name wins; otherwise the seller's own store
        name IS the packer name when they've confirmed (is_packer) they
        pack their own goods — same fallback logic as brand -> store
        name, for the same reason: for handmade/home-packed goods the
        seller genuinely IS the packer, so this isn't a guess. Only
        applies when is_packer is explicitly set — never assumed for a
        reseller of goods they didn't pack themselves."""
        if self.packer_name:
            return self.packer_name
        if self.is_packer:
            return self.name
        return None

    @property
    def consumer_care_contact(self) -> str | None:
        """"name,email,phone" — the exact format ONDC's own worked
        examples use for @ondc/org/contact_details_consumer_care. Only
        returns a value when ALL THREE are configured; a partial
        contact (email but no phone) isn't usable and is treated as
        unconfigured rather than emitting an incomplete one."""
        if self.consumer_care_name and self.consumer_care_email and self.consumer_care_phone:
            return f"{self.consumer_care_name},{self.consumer_care_email},{self.consumer_care_phone}"
        return None

    @property
    def is_checkout_ready(self) -> bool:
        """ACP requires both policy URLs for checkout eligibility. They
        don't affect discovery/search."""
        return bool(self.name and self.privacy_policy and self.tos)

    @property
    def any_verification_field_configured(self) -> bool:
        return any(
            v is not None
            for v in (
                self.gstin_valid,
                self.udyam_valid,
                self.pan_valid,
                self.upi_format_valid,
                self.whatsapp_format_valid,
            )
        )

    @property
    def verification_reasons(self) -> tuple[str, ...]:
        """Only populated when NOT verified — the specific reason(s)
        each configured field failed to contribute, for the decision log
        and (eventually) the frontend tooltip. Empty when verified: the
        method alone (verification_method) is the whole story then.

        Two distinct failure shapes, both covered: a field that's
        configured but structurally INVALID (wrong GSTIN checksum, a
        malformed PAN, ...), and a field that's configured and VALID but
        left INCOMPLETE — a lone UPI ID with no paired PAN or WhatsApp
        number, which is by design not enough on its own (see module
        docstring). Reporting only the first kind was tested and found
        to leave the second kind silently unexplained — a valid-but-
        unpaired UPI ID blocked ingestion with an empty reasons tuple,
        which is a real gap for demo narration, not just an edge case.
        """
        if self.verified:
            return ()
        reasons: list[str] = []
        for label, valid, field_reasons in (
            ("GSTIN", self.gstin_valid, self.gstin_reasons),
            ("Udyam", self.udyam_valid, self.udyam_reasons),
            ("PAN", self.pan_valid, self.pan_reasons),
            ("UPI ID", self.upi_format_valid, self.upi_reasons),
            ("WhatsApp phone", self.whatsapp_format_valid, self.whatsapp_reasons),
        ):
            if valid is False:
                reasons.extend(f"{label}: {r}" for r in field_reasons)

        if self.upi_format_valid is True and self.pan_valid is not True and self.whatsapp_format_valid is not True:
            reasons.append(
                "UPI ID is valid but needs a paired PAN or WhatsApp number "
                "configured to count as verification (UPI alone isn't enough)"
            )
        if self.pan_valid is True and self.upi_format_valid is not True:
            reasons.append("PAN is valid but needs a paired UPI ID configured to count as verification")
        if self.whatsapp_format_valid is True and self.upi_format_valid is not True:
            reasons.append("WhatsApp phone is valid but needs a paired UPI ID configured to count as verification")

        return tuple(reasons)

    @property
    def blocks_ingestion(self) -> bool:
        """True only when at least one identity field was actually
        configured but the combination doesn't add up to a verified
        seller. Nothing configured at all never blocks — same graceful
        degradation as every other optional seller_profile field."""
        return self.any_verification_field_configured and not self.verified


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    # Guard against an operator literally configuring a placeholder —
    # the spec rejects these by name, so silently passing one through
    # would just move the failure downstream to feed ingestion.
    if stripped.lower() in {"null", "none", "unknown", "n/a", "na", "-", "unbranded"}:
        return None
    return stripped


def _parse_bool_env(raw: str | None) -> bool | None:
    """None means "not configured" (a gap to report), not "false" — a
    seller who genuinely sets no-returns policy (false) must be
    distinguishable from one who never configured it at all. An
    unrecognized value (a typo) is also treated as unconfigured rather
    than guessed either way — silently defaulting a legal return
    policy from a typo would be worse than reporting the gap."""
    cleaned = _clean(raw)
    if cleaned is None:
        return None
    if cleaned.lower() in {"true", "yes", "1"}:
        return True
    if cleaned.lower() in {"false", "no", "0"}:
        return False
    return None


def _parse_int_env(raw: str | None) -> int | None:
    cleaned = _clean(raw)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def load_seller_profile() -> SellerProfile:
    # Reuses _clean() rather than a separate check so an operator typing
    # "n/a" or "none" for any identity field means "not applicable"
    # (unconfigured), consistent with how every other field already
    # treats those values — not "invalid GSTIN: 'n/a'".
    cleaned_gstin = _clean(os.environ.get("STOREMIND_SELLER_GSTIN"))
    gstin_result = validate_gstin(cleaned_gstin) if cleaned_gstin else None

    cleaned_udyam = _clean(os.environ.get("STOREMIND_SELLER_UDYAM"))
    udyam_result = validate_udyam(cleaned_udyam) if cleaned_udyam else None

    cleaned_pan = _clean(os.environ.get("STOREMIND_SELLER_PAN"))
    pan_result = validate_pan(cleaned_pan) if cleaned_pan else None

    cleaned_upi = _clean(os.environ.get("STOREMIND_SELLER_UPI_ID"))
    upi_result = check_upi_vpa_format(cleaned_upi) if cleaned_upi else None

    cleaned_whatsapp = _clean(os.environ.get("STOREMIND_SELLER_WHATSAPP_PHONE"))
    whatsapp_result = check_whatsapp_phone_format(cleaned_whatsapp) if cleaned_whatsapp else None

    gstin_valid = gstin_result.valid if gstin_result else None
    udyam_valid = udyam_result.valid if udyam_result else None
    pan_valid = pan_result.valid if pan_result else None
    upi_format_valid = upi_result.format_valid if upi_result else None
    whatsapp_format_valid = whatsapp_result.format_valid if whatsapp_result else None

    verified, method = _resolve_verification(
        gstin_valid, udyam_valid, pan_valid, upi_format_valid, whatsapp_format_valid
    )

    return SellerProfile(
        name=_clean(os.environ.get("STOREMIND_SELLER_NAME")),
        url=_clean(os.environ.get("STOREMIND_SELLER_URL")),
        privacy_policy=_clean(os.environ.get("STOREMIND_SELLER_PRIVACY_POLICY")),
        tos=_clean(os.environ.get("STOREMIND_SELLER_TOS")),
        gstin=gstin_result.normalized if gstin_result else None,
        gstin_valid=gstin_valid,
        gstin_reasons=tuple(gstin_result.reasons) if gstin_result else (),
        gstin_holder_type=gstin_result.holder_type if gstin_result else None,
        udyam=udyam_result.normalized if udyam_result else None,
        udyam_valid=udyam_valid,
        udyam_reasons=tuple(udyam_result.reasons) if udyam_result else (),
        pan=pan_result.normalized if pan_result else None,
        pan_valid=pan_valid,
        pan_reasons=tuple(pan_result.reasons) if pan_result else (),
        pan_holder_type=pan_result.holder_type if pan_result else None,
        upi_id=upi_result.normalized if upi_result else None,
        upi_format_valid=upi_format_valid,
        upi_reasons=tuple(upi_result.reasons) if upi_result else (),
        whatsapp_phone=whatsapp_result.normalized if whatsapp_result else None,
        whatsapp_format_valid=whatsapp_format_valid,
        whatsapp_reasons=tuple(whatsapp_result.reasons) if whatsapp_result else (),
        verified=verified,
        verification_method=method,
        is_packer=bool(_parse_bool_env(os.environ.get("STOREMIND_SELLER_IS_PACKER"))),
        packer_name=_clean(os.environ.get("STOREMIND_SELLER_PACKER_NAME")),
        packer_address=_clean(os.environ.get("STOREMIND_SELLER_PACKER_ADDRESS")),
        consumer_care_name=_clean(os.environ.get("STOREMIND_SELLER_CONSUMER_CARE_NAME")),
        consumer_care_email=_clean(os.environ.get("STOREMIND_SELLER_CONSUMER_CARE_EMAIL")),
        consumer_care_phone=_clean(os.environ.get("STOREMIND_SELLER_CONSUMER_CARE_PHONE")),
        returnable=_parse_bool_env(os.environ.get("STOREMIND_SELLER_RETURNABLE")),
        cancellable=_parse_bool_env(os.environ.get("STOREMIND_SELLER_CANCELLABLE")),
        return_window_days=_parse_int_env(os.environ.get("STOREMIND_SELLER_RETURN_WINDOW_DAYS")),
        time_to_ship_hours=_parse_int_env(os.environ.get("STOREMIND_SELLER_TIME_TO_SHIP_HOURS")),
    )
    