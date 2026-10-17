"""
Tests for ondc_eligibility.py — a thin wrapper around
ondc_mapping.to_ondc_catalog_item (see that module's own tests for the
detailed gap scenarios). This file only confirms the wrapper itself:
never raises, and correctly translates success/failure into the result
shape.
"""

from dataclasses import dataclass

from ondc_eligibility import derive_ondc_eligibility


@dataclass
class FakeSeller:
    resolved_packer_name: str = None
    packer_address: str = None
    consumer_care_contact: str = None
    returnable: bool = None
    cancellable: bool = None
    return_window_days: int = None
    time_to_ship_hours: int = None


FULLY_CONFIGURED_SELLER = FakeSeller(
    resolved_packer_name="Kurta House",
    packer_address="123 MG Road, Bengaluru, Karnataka 560001",
    consumer_care_contact="Ramesh,ramesh@abc.com,18004254444",
    returnable=True, cancellable=True, return_window_days=7, time_to_ship_hours=24,
)
GOOD_PRODUCT = {
    "sku": "K1", "name": "Cotton Kurta", "category": "Fashion", "description": "A kurta.",
    "price": "899", "currency": "INR", "availability": "in_stock",
    "attributes": {"net_quantity": "1 unit", "manufacture_date": "09/2026"},
}


def test_eligible_product_reports_listable_with_no_blockers():
    r = derive_ondc_eligibility(GOOD_PRODUCT, FULLY_CONFIGURED_SELLER)
    assert r.is_ondc_listable is True
    assert r.ondc_blockers == []
    assert r.ondc_domain == "ONDC:RET12"


def test_ineligible_product_never_raises_and_lists_every_blocker():
    r = derive_ondc_eligibility({"sku": "X", "name": "Y"}, FakeSeller())
    assert r.is_ondc_listable is False
    assert len(r.ondc_blockers) >= 5
    assert r.ondc_domain is None


def test_domain_is_still_reported_for_a_product_blocked_for_other_reasons():
    """A product with a real, mappable category but missing packer info
    should still show WHICH domain it would list under once fixed —
    same remediation-hint instinct as acp_schema's own reports."""
    r = derive_ondc_eligibility(GOOD_PRODUCT, FakeSeller())
    assert r.is_ondc_listable is False
    assert r.ondc_domain == "ONDC:RET12"
    