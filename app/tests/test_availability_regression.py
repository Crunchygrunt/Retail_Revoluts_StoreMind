"""
Regression test for a real bug found via live testing: main.py's
/ingest used to normalise any unrecognised `availability` value to
"unknown" BEFORE check_acp_conformance ever saw it — which is a valid
value, so the conformance check's own invalid-value detection never
fired. A row with availability="maybe_in_stock" landed Tier 2 instead
of the intended Tier 3.

This reproduces the exact sequence main.py now runs (not main.py
itself, which needs fastapi and can't be imported in this sandbox) to
confirm the fix: the RAW value must reach check_acp_conformance
unchanged, so it can actually be flagged.
"""

from __future__ import annotations

from acp_schema import DEFAULT_AVAILABILITY, check_acp_conformance, derive_eligibility


def resolve_availability(raw_value):
    """The FIXED logic from main.py's /ingest — passes an unrecognised
    value through as-is rather than silently rewriting it to
    "unknown"."""
    cleaned = str(raw_value or "").strip().lower()
    return cleaned or DEFAULT_AVAILABILITY


def _conformant_product(**overrides):
    base = {
        "sku": "SKU-1", "name": "Brass Table Lamp",
        "description": "Hand-cast brass table lamp base, fabric shade.",
        "brand": "Local Weaves", "seller_name": "Kurta House",
        "url": "https://example.com/p/SKU-1", "image_url": "https://example.com/i/SKU-1.jpg",
        "price": 1299.0, "currency": "INR",
    }
    base.update(overrides)
    return base


def test_the_exact_bug_reproduction_maybe_in_stock_now_lands_tier_3():
    """This is the literal scenario that shipped incorrectly: a seller
    typo/garbage availability value ("maybe_in_stock") must be
    reported as invalid and land the product on Tier 3, not silently
    pass as if it were fine."""
    availability = resolve_availability("maybe_in_stock")
    assert availability == "maybe_in_stock", "must NOT be silently coerced to 'unknown'"

    product = _conformant_product(availability=availability)
    conformance = check_acp_conformance(product)
    assert conformance.conformant is False
    assert "availability" in conformance.invalid_fields

    eligibility = derive_eligibility(product, conformance, meets_readiness_threshold=True)
    assert eligibility.tier == "tier_3_discovery"
    assert eligibility.is_eligible_checkout is False


def test_a_genuinely_missing_availability_still_defaults_to_in_stock():
    """The fix must not regress the no-availability-given case — that's
    a real default, not an error."""
    assert resolve_availability(None) == DEFAULT_AVAILABILITY
    assert resolve_availability("") == DEFAULT_AVAILABILITY
    assert resolve_availability("   ") == DEFAULT_AVAILABILITY


def test_a_valid_availability_value_passes_through_unchanged():
    for value in ("in_stock", "out_of_stock", "pre_order", "backorder", "unknown"):
        assert resolve_availability(value) == value
        product = _conformant_product(availability=value)
        conformance = check_acp_conformance(product)
        assert conformance.conformant is True, f"{value} should be valid"


def test_mixed_case_and_whitespace_from_a_real_upload_still_normalises_correctly():
    """A CSV/xlsx cell might read "In_Stock " with stray case/whitespace
    -- must still resolve to the clean lowercase value, not get flagged
    as invalid for formatting reasons alone."""
    assert resolve_availability("  In_Stock  ") == "in_stock"
    assert resolve_availability("OUT_OF_STOCK") == "out_of_stock"
    
    