"""
Tests for acp_schema.py — the Pydantic-backed ACP/UCP schema and its
conformance REPORT (not a gate: see the module docstring for why url
and image_url can't be enforced yet).
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acp_schema import (
    ACP_LENGTH_LIMITS,
    AVAILABILITY_VALUES,
    TIER_1_COMPLIANT,
    TIER_2_ENRICHED,
    TIER_3_DISCOVERY,
    check_acp_conformance,
    derive_eligibility,
    format_acp_price,
    is_placeholder,
)
from seller_profile import SellerProfile


CHECKOUT_READY_SELLER = SellerProfile(
    name="Kurta House",
    url="https://example.com/store",
    privacy_policy="https://example.com/privacy",
    tos="https://example.com/terms",
)
SEARCH_ONLY_SELLER = SellerProfile(
    name="Kurta House", url=None, privacy_policy=None, tos=None
)


def _conformant_product(**overrides) -> dict:
    base = {
        "sku": "SKU-1",
        "name": "Premium Handwoven Cotton Kurta",
        "description": "A handwoven cotton kurta with traditional embroidery.",
        "brand": "Fabindia",
        "url": "https://example.com/p/SKU-1",
        "image_url": "https://example.com/i/SKU-1.jpg",
        # Required by the real ACP spec — both were missing from the
        # earlier (third-party-sourced) model.
        "seller_name": "Kurta House",
        "availability": "in_stock",
        # Always present in a real pipeline product — Layer-0 rejects
        # rows without a parseable price before they ever reach here.
        "price": 1499.0,
        "currency": "INR",
    }
    base.update(overrides)
    return base


def test_a_fully_populated_product_is_conformant():
    result = check_acp_conformance(_conformant_product())
    assert result.conformant is True
    assert result.missing_fields == []
    assert result.oversized_fields == []


def test_missing_brand_is_reported_as_a_missing_field():
    product = _conformant_product()
    del product["brand"]
    result = check_acp_conformance(product)
    assert result.conformant is False
    assert "brand" in result.missing_fields


def test_missing_url_is_now_a_normal_missing_field_since_storemind_generates_it():
    """`url` used to be a known gap (no generation path). Now
    product_page.build_product_url always supplies one, so its absence
    from a structured product is a genuine failure of the pipeline to
    populate it, not an acknowledged limitation."""
    product = _conformant_product()
    del product["url"]
    result = check_acp_conformance(product)
    assert result.conformant is False
    assert "url" in result.missing_fields


def test_missing_image_url_names_the_one_action_the_seller_must_take():
    """A missing photo is the only ACP gap StoreMind can't close on the
    seller's behalf, so the report must say what to do about it rather
    than just flagging a failure."""
    product = _conformant_product()
    del product["image_url"]
    result = check_acp_conformance(product)
    assert result.conformant is False
    assert "image_url" in result.missing_fields
    assert any("photo" in r for r in result.remediation), result.remediation


def test_remediation_is_offered_for_every_reported_gap():
    product = _conformant_product()
    del product["brand"]
    del product["description"]
    result = check_acp_conformance(product)
    assert len(result.remediation) >= 2


def test_oversized_title_is_reported_as_oversized_not_missing():
    product = _conformant_product(name="x" * (ACP_LENGTH_LIMITS["name"] + 1))
    result = check_acp_conformance(product)
    assert result.conformant is False
    assert "name" in result.oversized_fields
    assert "name" not in result.missing_fields


def test_oversized_brand_is_caught_at_the_brand_limit():
    product = _conformant_product(brand="b" * (ACP_LENGTH_LIMITS["brand"] + 1))
    result = check_acp_conformance(product)
    assert "brand" in result.oversized_fields


def test_brand_at_exactly_the_limit_is_accepted():
    product = _conformant_product(brand="b" * ACP_LENGTH_LIMITS["brand"])
    result = check_acp_conformance(product)
    assert result.conformant is True


def test_conformance_check_never_raises_on_garbage_input():
    """This runs inside /ingest — it must degrade to a report, never
    break the batch."""
    for garbage in ({}, {"sku": None}, {"name": 123, "brand": []}):
        result = check_acp_conformance(garbage)
        assert result.conformant is False  # and, critically, no exception


# ---------------------------------------------------------------------
# Corrections against the REAL spec (see acp_schema.py's source note)
# ---------------------------------------------------------------------

def test_seller_name_is_required():
    """Missing entirely from the earlier third-party-sourced model. It's
    one of the nine basic fields for a discovery feed."""
    product = _conformant_product()
    del product["seller_name"]
    result = check_acp_conformance(product)
    assert result.conformant is False
    assert "seller_name" in result.missing_fields


def test_availability_is_required():
    product = _conformant_product()
    del product["availability"]
    result = check_acp_conformance(product)
    assert result.conformant is False
    assert "availability" in result.missing_fields


def test_unrecognised_availability_is_invalid_not_merely_missing():
    """The spec: omitted, empty or unrecognised values REJECT THE ROW.
    A bad value is a different fix from an absent one, so it's reported
    separately."""
    product = _conformant_product(availability="maybe_in_stock")
    result = check_acp_conformance(product)
    assert result.conformant is False
    assert "availability" in result.invalid_fields
    assert "availability" not in result.missing_fields


def test_unknown_is_a_valid_availability_but_a_placeholder_elsewhere():
    """`unknown` is explicitly permitted for availability, and
    explicitly forbidden as a generic placeholder everywhere else."""
    assert "unknown" in AVAILABILITY_VALUES
    assert is_placeholder("unknown", "availability") is False
    assert is_placeholder("unknown", "brand") is True
    assert check_acp_conformance(_conformant_product(availability="unknown")).conformant is True


def test_placeholder_brand_is_rejected_not_accepted():
    """Regression guard for a real spec violation in the earlier build:
    the brand fallback emitted the literal string 'Unbranded'. The spec
    forbids placeholder values outright."""
    for placeholder in ("Unbranded", "unknown", "n/a", "null", "-"):
        result = check_acp_conformance(_conformant_product(brand=placeholder))
        assert result.conformant is False, f"{placeholder!r} should not pass"
        assert "brand" in result.invalid_fields


def test_acp_price_formatting_matches_the_spec():
    assert format_acp_price(79.99, "USD") == "79.99 USD"
    assert format_acp_price(1499, "inr") == "1499.00 INR"


def test_acp_price_formatting_refuses_unusable_input_instead_of_raising():
    for price, currency in [(None, "USD"), (1499, None), (1499, "RUPEES"), (0, "USD"), (-5, "USD"), ("abc", "USD")]:
        assert format_acp_price(price, currency) is None


def test_price_without_a_valid_currency_is_invalid():
    result = check_acp_conformance(_conformant_product(currency=None))
    assert "price" in result.invalid_fields


def test_brand_length_cap_is_ours_not_the_protocols():
    """No brand length limit appears in OpenAI's actual spec. The cap is
    retained as a sanity guard, but title and description are the only
    two this codebase should claim are spec-confirmed."""
    from acp_schema import SPEC_CONFIRMED_LIMITS

    assert set(SPEC_CONFIRMED_LIMITS) == {"name", "description"}
    assert ACP_LENGTH_LIMITS["name"] == 150
    assert ACP_LENGTH_LIMITS["description"] == 5000


# ---------------------------------------------------------------------
# Checkout-only requirements
# ---------------------------------------------------------------------

def test_missing_seller_policies_block_checkout_but_not_discovery():
    """seller_privacy_policy and seller_tos are conditionally required
    FOR CHECKOUT. They must not affect search eligibility."""
    product = _conformant_product()
    result = check_acp_conformance(product, seller_profile=SEARCH_ONLY_SELLER)
    assert result.conformant is True, "policy URLs are not a discovery requirement"
    assert set(result.checkout_blockers) == {"seller_privacy_policy", "seller_tos"}

    eligibility = derive_eligibility(product, result, meets_readiness_threshold=True)
    assert eligibility.is_eligible_search is True
    assert eligibility.is_eligible_checkout is False
    assert eligibility.tier == TIER_3_DISCOVERY


def test_full_seller_profile_allows_checkout():
    product = _conformant_product()
    result = check_acp_conformance(product, seller_profile=CHECKOUT_READY_SELLER)
    assert result.checkout_blockers == []
    eligibility = derive_eligibility(product, result, meets_readiness_threshold=True)
    assert eligibility.is_eligible_checkout is True


def test_checkout_can_never_be_true_while_search_is_false():
    """A hard spec dependency, not a StoreMind choice: search set to
    false overrides the checkout flag. Enforced structurally so a caller
    can't produce the impossible combination."""
    nameless = {"price": 999, "availability": "in_stock"}
    conformance = check_acp_conformance(nameless, seller_profile=CHECKOUT_READY_SELLER)
    result = derive_eligibility(nameless, conformance, meets_readiness_threshold=True)
    assert result.is_eligible_search is False
    assert result.is_eligible_checkout is False


# ---------------------------------------------------------------------
# Eligibility / tiering
# ---------------------------------------------------------------------

def test_fully_conformant_and_ready_product_is_tier_1_and_fully_eligible():
    product = _conformant_product()
    conformance = check_acp_conformance(product)
    result = derive_eligibility(product, conformance, meets_readiness_threshold=True)
    assert result.tier == TIER_1_COMPLIANT
    assert result.is_eligible_search is True
    assert result.is_eligible_checkout is True


def test_product_made_conformant_by_derived_values_is_tier_2_but_still_fully_eligible():
    """A seller whose brand was inferred and whose page StoreMind
    generated must NOT be penalised for it — Tier 2 has the same
    capabilities as Tier 1."""
    product = _conformant_product(brand_source="inferred", url_source="generated")
    conformance = check_acp_conformance(product)
    result = derive_eligibility(product, conformance, meets_readiness_threshold=True)
    assert result.tier == TIER_2_ENRICHED
    assert result.is_eligible_checkout is True
    assert result.is_eligible_search is True


def test_product_with_no_photo_stays_searchable_instead_of_being_excluded():
    """The central guarantee of this whole design: a missing photo costs
    the ability to TRANSACT, never the ability to be FOUND."""
    product = _conformant_product()
    del product["image_url"]
    conformance = check_acp_conformance(product)
    result = derive_eligibility(product, conformance, meets_readiness_threshold=True)
    assert result.tier == TIER_3_DISCOVERY
    assert result.is_eligible_checkout is False
    assert result.is_eligible_search is True, "a product without a photo must still be discoverable"


def test_below_readiness_threshold_is_discovery_only_not_excluded():
    product = _conformant_product()
    conformance = check_acp_conformance(product)
    result = derive_eligibility(product, conformance, meets_readiness_threshold=False)
    assert result.tier == TIER_3_DISCOVERY
    assert result.is_eligible_checkout is False
    assert result.is_eligible_search is True


def test_search_eligibility_needs_only_a_name_and_a_price():
    sparse = {"name": "Cotton Kurta", "price": 999}
    conformance = check_acp_conformance(sparse)
    result = derive_eligibility(sparse, conformance, meets_readiness_threshold=False)
    assert result.is_eligible_search is True
    assert result.is_eligible_checkout is False


def test_a_product_with_no_name_is_not_even_searchable():
    nameless = {"price": 999}
    conformance = check_acp_conformance(nameless)
    result = derive_eligibility(nameless, conformance, meets_readiness_threshold=False)
    assert result.is_eligible_search is False


def test_no_tier_means_excluded():
    """Guards the design intent: every tier must leave the product
    reachable by at least one surface."""
    for tier in (TIER_1_COMPLIANT, TIER_2_ENRICHED, TIER_3_DISCOVERY):
        assert "exclude" not in tier and "reject" not in tier
        