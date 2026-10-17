"""
ACP feed schema — the single source of truth for protocol field
requirements, character limits and eligibility rules.

SOURCE CORRECTION (important for anyone reading the git history): an
earlier version of this file was written against a third-party blog
summarising the spec, and got several things wrong. This version is
written against OpenAI's own live specification at
https://developers.openai.com/commerce/specs/file-upload/products
(stable schema). What changed:

  - `seller_name` is REQUIRED and was missing entirely. It's one of the
    nine basic fields needed for a useful discovery feed.
  - `availability` is REQUIRED and was missing entirely. The spec is
    explicit that omitted, empty or unrecognised values REJECT THE ROW —
    it is not a soft signal.
  - `price` is required, and its format is `"amount CURRENCY"`
    (e.g. `"79.99 USD"`), not a bare number.
  - `is_eligible_search` is OPTIONAL, defaulting to true — it was
    modelled as required.
  - `is_eligible_checkout` requires `is_eligible_search=true`. The spec
    states search set to false OVERRIDES the checkout flag. This is a
    hard dependency, encoded in derive_eligibility below.
  - `seller_privacy_policy` and `seller_tos` are conditionally required
    FOR CHECKOUT. Both were missing.
  - The 70-character `brand` limit could NOT be found anywhere in the
    real spec — it appears to have been the third-party summary's own
    approximation. Title (150) and description (5,000) ARE confirmed.
    A brand cap is retained below purely as a sanity guard, and is
    explicitly labelled as ours rather than the protocol's.
  - The spec forbids placeholder strings: "Do not use placeholder
    strings such as `null`, `unknown`, or `n/a`" and "Use a real brand
    and seller name, not placeholders." The previous `"Unbranded"` brand
    fallback violated this directly. See seller_profile.py for the fix.

Field-name note: this codebase's internal field for ACP's `title` is
`name`, and for `item_id` is `sku`. No rename was done — those names are
threaded through Layer-0, all three catalog agents, the store and the
frontend. ACP_FIELD_NAMES maps them at the feed boundary.

SCOPE: check_acp_conformance() REPORTS; it never rejects a product.
Enforcement is in derive_eligibility(), and even there the worst outcome
is discovery-only. Nothing is ever excluded outright — losing a retailer
their reach because their spreadsheet lacked a column is the exact
failure this system exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

# Confirmed against the real spec: "aim for at most 150 and 5,000
# characters" for title and description respectively.
#
# `brand` is OURS, not the protocol's — no brand length limit appears in
# OpenAI's spec. Kept as a defensive sanity cap so a pathological value
# can't land in a feed; do not cite it as a protocol requirement.
ACP_LENGTH_LIMITS: dict[str, int] = {
    "name": 150,          # ACP "title" — spec-confirmed
    "description": 5000,  # spec-confirmed
    "brand": 70,          # StoreMind sanity cap, NOT in the ACP spec
}

SPEC_CONFIRMED_LIMITS = ("name", "description")

# Internal field name -> the name used in an ACP feed row.
ACP_FIELD_NAMES: dict[str, str] = {
    "sku": "item_id",
    "name": "title",
    "description": "description",
    "url": "url",
    "brand": "brand",
    "seller_name": "seller_name",
    "image_url": "image_url",
    "availability": "availability",
    "price": "price",
}

# Exact permitted values. The spec: "Omitted, empty, or unrecognized
# values reject the row. Use `unknown` explicitly when stock status is
# unavailable." Note `unknown` IS valid here — it's one of the few
# places the spec permits it, which is why it isn't treated as a
# forbidden placeholder.
AVAILABILITY_VALUES = ("in_stock", "out_of_stock", "pre_order", "backorder", "unknown")
DEFAULT_AVAILABILITY = "in_stock"

# Google-compatible feeds use a different spelling for one value and
# don't accept `unknown` at all. Kept here so a future feed exporter
# doesn't have to rediscover it.
GOOGLE_COMPATIBLE_AVAILABILITY = {
    "in_stock": "in_stock",
    "out_of_stock": "out_of_stock",
    "pre_order": "preorder",  # NOT "pre_order" on this path
    "backorder": "backorder",
}

# Strings the spec explicitly rejects as values. `unknown` is excluded
# from this set for `availability` only, handled above.
FORBIDDEN_PLACEHOLDERS = {"null", "none", "unknown", "n/a", "na", "-", "unbranded", "tbd"}

# Enough for a product to be FOUND — deliberately a lower bar than
# being bought from.
REQUIRED_FOR_SEARCH = ("name", "price")


class ACPDiscoveryProduct(BaseModel):
    """The nine basic fields the spec requires for a useful discovery
    feed.

    Deliberately excludes the eligibility flags: those are DERIVED from
    conformance (see derive_eligibility), so requiring them here would
    be circular and nothing could ever pass. This is the model
    check_acp_conformance validates against."""

    sku: str                                                        # -> item_id
    name: str = Field(max_length=ACP_LENGTH_LIMITS["name"])         # -> title
    description: str = Field(max_length=ACP_LENGTH_LIMITS["description"])
    url: str
    brand: str = Field(max_length=ACP_LENGTH_LIMITS["brand"])
    seller_name: str
    image_url: str
    availability: str
    price: float


@dataclass
class ConformanceResult:
    conformant: bool
    missing_fields: list[str] = field(default_factory=list)
    oversized_fields: list[str] = field(default_factory=list)
    invalid_fields: list[str] = field(default_factory=list)
    checkout_blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)


# What the seller (or operator) would actually have to DO. A report that
# only names a missing field tells a retailer they've failed; one that
# names the fix tells them how to pass.
REMEDIATION: dict[str, str] = {
    "image_url": "upload at least one product photo — the only field StoreMind can't derive for you",
    "name": "give the product a specific name (not 'Item' or 'Product')",
    "description": "add a short description of the product",
    "brand": "set STOREMIND_SELLER_NAME so unbranded products can carry your store name as the brand",
    "seller_name": "set STOREMIND_SELLER_NAME — ACP requires a real seller name on every row",
    "sku": "give the product a unique SKU or ID",
    "url": "no action needed — StoreMind serves a canonical product page automatically",
    "availability": "set stock status (in_stock / out_of_stock / pre_order / backorder / unknown)",
    "price": "add a price — Layer-0 normally rejects rows without one",
    "seller_privacy_policy": "set STOREMIND_SELLER_PRIVACY_POLICY — required by ACP for checkout, not for search",
    "seller_tos": "set STOREMIND_SELLER_TOS — required by ACP for checkout, not for search",
}


def is_placeholder(value: Any, field_name: str = "") -> bool:
    """True if `value` is one of the strings the spec rejects.
    `unknown` is legitimate for `availability` specifically."""
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    if field_name == "availability" and lowered == "unknown":
        return False
    return lowered in FORBIDDEN_PLACEHOLDERS


def format_acp_price(price: Any, currency: Any) -> str | None:
    """Formats price as the spec requires: `"amount CURRENCY"`, e.g.
    `"79.99 USD"` — a decimal amount in MAJOR units, a space, then an
    uppercase ISO 4217 code.

    Returns None rather than raising if either part is unusable; a feed
    row with no price is a reportable gap, not a crash. Currency comes
    from Layer-0's own ISO 4217 detection (see layer0_validator.py), so
    it's already the right shape.
    """
    if price is None or not currency:
        return None
    try:
        amount = float(price)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    code = str(currency).strip().upper()
    if len(code) != 3 or not code.isalpha():
        return None
    return f"{amount:.2f} {code}"


def check_acp_conformance(
    structured: dict[str, Any],
    seller_profile=None,
) -> ConformanceResult:
    """Reports how far a structured product is from ACP conformance.

    Never raises, never rejects. Separates four kinds of problem:
      - missing_fields     : required value absent
      - oversized_fields   : over a character limit
      - invalid_fields     : present but not a permitted value
      - checkout_blockers  : fine for discovery, blocks checkout only
    """
    missing: list[str] = []
    oversized: list[str] = []
    invalid: list[str] = []
    checkout_blockers: list[str] = []
    notes: list[str] = []

    payload = {k: structured.get(k) for k in ACPDiscoveryProduct.model_fields}

    # Placeholders are "present but invalid", not missing — a different
    # fix for the retailer, so reported separately.
    for field_name, value in list(payload.items()):
        if is_placeholder(value, field_name):
            invalid.append(field_name)
            payload[field_name] = None
            notes.append(
                f"{field_name} is the placeholder {value!r}; the ACP spec rejects placeholder values"
            )

    try:
        ACPDiscoveryProduct(**payload)
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc") or ()
            field_name = str(loc[0]) if loc else "<unknown>"
            if field_name in invalid:
                continue
            err_type = str(err.get("type", ""))
            if "too_long" in err_type or "max_length" in err_type:
                oversized.append(field_name)
            else:
                missing.append(field_name)
    except Exception as exc:  # noqa: BLE001 — runs inside /ingest; must degrade, not break
        notes.append(f"conformance check could not run: {exc}")
        return ConformanceResult(conformant=False, notes=notes)

    # availability must be one of the exact permitted values — the spec
    # rejects the row outright otherwise, so this is not a soft warning.
    availability = structured.get("availability")
    if availability and availability not in AVAILABILITY_VALUES and "availability" not in invalid:
        invalid.append("availability")
        notes.append(
            f"availability {availability!r} is not one of {', '.join(AVAILABILITY_VALUES)} — "
            "an unrecognised value rejects the whole row"
        )

    # price must be formattable as "amount CURRENCY"
    if structured.get("price") is not None and "price" not in missing:
        if format_acp_price(structured.get("price"), structured.get("currency")) is None:
            invalid.append("price")
            notes.append(
                "price cannot be formatted as 'amount CURRENCY' — a valid ISO 4217 currency is required"
            )

    # Checkout-only requirements. These do NOT affect discovery, so they
    # are tracked apart from the fields above.
    if seller_profile is not None:
        if not seller_profile.privacy_policy:
            checkout_blockers.append("seller_privacy_policy")
        if not seller_profile.tos:
            checkout_blockers.append("seller_tos")

    if oversized:
        notes.append(
            f"over length limit: {', '.join(sorted(set(oversized)))} "
            "(length_repair.py should have caught this — worth investigating)"
        )

    all_gaps = sorted(set(missing) | set(oversized) | set(invalid) | set(checkout_blockers))
    remediation = [REMEDIATION[f] for f in all_gaps if f in REMEDIATION]

    conformant = not (missing or oversized or invalid)
    return ConformanceResult(
        conformant=conformant,
        missing_fields=sorted(set(missing)),
        oversized_fields=sorted(set(oversized)),
        invalid_fields=sorted(set(invalid)),
        checkout_blockers=sorted(set(checkout_blockers)),
        notes=notes,
        remediation=remediation,
    )


# ---------------------------------------------------------------------
# Eligibility / tiering
# ---------------------------------------------------------------------

# Graceful degradation instead of hard rejection. A retailer must never
# lose reach purely because their data didn't meet a spec, so a product
# that can't transact stays discoverable rather than disappearing.
#
#   TIER_1_COMPLIANT  — conformant, over the readiness threshold, and
#                       every value came from the seller.
#   TIER_2_ENRICHED   — same capabilities as Tier 1, but reached
#                       conformance using StoreMind-derived values
#                       (inferred brand, generated url, hosted image).
#                       Recorded for honesty, never as a penalty.
#   TIER_3_DISCOVERY  — missing something checkout requires. Still
#                       searchable, still ad-eligible; not transactable.
#
# There is no tier that means "excluded".
TIER_1_COMPLIANT = "tier_1_compliant"
TIER_2_ENRICHED = "tier_2_enriched"
TIER_3_DISCOVERY = "tier_3_discovery"

_DERIVED_MARKERS = (
    ("brand_source", ("inferred", "fallback", "vision", "seller_name")),
    ("url_source", ("generated",)),
    ("image_source", ("hosted",)),
    ("currency_inferred", (True,)),
)


@dataclass
class EligibilityResult:
    tier: str
    is_eligible_search: bool
    is_eligible_checkout: bool
    reasons: list[str] = field(default_factory=list)


def derive_eligibility(
    structured: dict[str, Any],
    conformance: ConformanceResult,
    meets_readiness_threshold: bool,
) -> EligibilityResult:
    """Derives ACP's two eligibility flags plus the capability tier.

    The search/checkout dependency is a HARD spec rule, not a StoreMind
    choice: `is_eligible_checkout` is true "only when search
    eligibility is also true", and search set to false overrides the
    checkout flag. So checkout can never be true while search is false,
    and that's enforced structurally below rather than left to the
    caller.
    """
    reasons: list[str] = []

    missing_for_search = [f for f in REQUIRED_FOR_SEARCH if structured.get(f) in (None, "", [])]
    is_eligible_search = not missing_for_search
    if missing_for_search:
        reasons.append(f"not searchable: missing {', '.join(missing_for_search)}")

    is_eligible_checkout = bool(
        is_eligible_search              # hard spec dependency — see docstring
        and conformance.conformant
        and not conformance.checkout_blockers
        and meets_readiness_threshold
    )
    if not conformance.conformant:
        reasons.append("not checkout-eligible: incomplete ACP feed data")
    if conformance.checkout_blockers:
        reasons.append(
            "not checkout-eligible: missing seller policy URLs "
            f"({', '.join(conformance.checkout_blockers)})"
        )
    if not meets_readiness_threshold:
        reasons.append("not checkout-eligible: readiness score below threshold")

    if not is_eligible_checkout:
        tier = TIER_3_DISCOVERY
        if is_eligible_search:
            reasons.append("listed as discovery-only — still searchable, still ad-eligible")
    elif any(structured.get(k) in vals for k, vals in _DERIVED_MARKERS):
        tier = TIER_2_ENRICHED
        reasons.append("conformant using StoreMind-derived values")
    else:
        tier = TIER_1_COMPLIANT

    return EligibilityResult(
        tier=tier,
        is_eligible_search=is_eligible_search,
        is_eligible_checkout=is_eligible_checkout,
        reasons=reasons,
    )
    