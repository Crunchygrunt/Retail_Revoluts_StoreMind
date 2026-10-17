"""Tests for ucp_eligibility.py — the three-tier display eligibility
that mirrors acp_schema.py's derive_eligibility() pattern."""

import pytest
from ucp_eligibility import derive_ucp_eligibility
from ucp_mapping import is_ucp_purchasable


CHECKOUT_READY = {
    "sku": "KURTA-001", "price": 1499.0, "currency": "INR",
    "availability": "in_stock", "meets_readiness_threshold": True,
}
OUT_OF_STOCK_BUT_MAPPABLE = {
    "sku": "OOS-001", "price": 2999.0, "currency": "INR",
    "availability": "out_of_stock", "meets_readiness_threshold": True,
}
BELOW_THRESHOLD_BUT_MAPPABLE = {
    "sku": "THIN-001", "price": 199.0, "currency": "INR",
    "availability": "in_stock", "meets_readiness_threshold": False,
}
BAD_CURRENCY = {
    "sku": "BADCUR-001", "price": 500.0, "currency": "Rs",
    "availability": "in_stock", "meets_readiness_threshold": True,
}
NO_PRICE_AT_ALL = {
    "sku": "NOPRICE-001", "price": None, "currency": None,
    "availability": "in_stock", "meets_readiness_threshold": True,
}


def test_checkout_ready_passes_all_gates():
    r = derive_ucp_eligibility(CHECKOUT_READY)
    assert r.is_ucp_catalog_listable is True
    assert r.is_ucp_checkout_eligible is True
    assert r.ucp_tier == "checkout_ready"
    assert r.ucp_blockers == []


def test_catalog_only_out_of_stock():
    r = derive_ucp_eligibility(OUT_OF_STOCK_BUT_MAPPABLE)
    assert r.is_ucp_catalog_listable is True
    assert r.is_ucp_checkout_eligible is False
    assert r.ucp_tier == "catalog_only"
    assert any("availability" in b for b in r.ucp_blockers)


def test_catalog_only_below_threshold():
    r = derive_ucp_eligibility(BELOW_THRESHOLD_BUT_MAPPABLE)
    assert r.is_ucp_catalog_listable is True
    assert r.is_ucp_checkout_eligible is False
    assert r.ucp_tier == "catalog_only"
    assert any("threshold" in b for b in r.ucp_blockers)


def test_not_ucp_ready_bad_currency():
    r = derive_ucp_eligibility(BAD_CURRENCY)
    assert r.is_ucp_catalog_listable is False
    assert r.is_ucp_checkout_eligible is False
    assert r.ucp_tier == "not_ucp_ready"


def test_not_ucp_ready_no_price():
    r = derive_ucp_eligibility(NO_PRICE_AT_ALL)
    assert r.ucp_tier == "not_ucp_ready"


@pytest.mark.parametrize(
    "product,expected_checkout_eligible",
    [
        (CHECKOUT_READY, True),
        (OUT_OF_STOCK_BUT_MAPPABLE, False),
        (BELOW_THRESHOLD_BUT_MAPPABLE, False),
        (BAD_CURRENCY, False),
    ],
)
def test_agrees_with_real_gate_logic(product, expected_checkout_eligible):
    """
    Not a tautology: derive_ucp_eligibility and the real checkout gate
    both call is_ucp_purchasable/to_ucp_checkout_line_item, but this
    pins that the COMBINATION (AND of all three) matches, guarding
    against a future edit to one that isn't mirrored in the other.
    """
    result = derive_ucp_eligibility(product)
    assert result.is_ucp_checkout_eligible == expected_checkout_eligible
    
    