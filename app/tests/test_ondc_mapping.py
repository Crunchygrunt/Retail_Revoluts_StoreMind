"""
Tests for ondc_mapping.py — the ONDC:RET10-16 on_search item mapper and
its statutory/policy gates. See the module's own docstring for what's
confirmed from real ONDC certification feedback vs. deliberately not
built (prepackaged_food/FSSAI).
"""

from dataclasses import dataclass

import pytest

from ondc_mapping import ONDCMappingError, to_ondc_catalog_item


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
    "sku": "KURTA-01", "name": "Handwoven Cotton Kurta", "category": "Fashion",
    "description": "A handwoven cotton kurta with block print detailing.",
    "price": "899", "currency": "INR", "availability": "in_stock",
    "image_url": "https://example.com/kurta.jpg",
    "attributes": {"material": "cotton", "net_quantity": "1 unit", "manufacture_date": "09/2026"},
}


def test_fully_configured_product_and_seller_produces_a_valid_item():
    item = to_ondc_catalog_item(GOOD_PRODUCT, FULLY_CONFIGURED_SELLER)
    assert item["id"] == "KURTA-01"
    assert item["price"] == {"currency": "INR", "value": "899.00"}
    assert item["@ondc/org/return_window"] == "P7D"
    assert item["@ondc/org/time_to_ship"] == "PT24H"
    statutory = item["@ondc/org/statutory_reqs_packaged_commodities"]
    assert statutory["manufacturer_or_packer_name"] == "Kurta House"
    assert statutory["month_year_of_manufacture_packing_import"] == "09/2026"


def test_in_stock_maps_to_99_out_of_stock_maps_to_0():
    """Confirmed real ONDC convention (not a fabricated inventory
    count) — see module docstring."""
    in_stock = to_ondc_catalog_item(GOOD_PRODUCT, FULLY_CONFIGURED_SELLER)
    assert in_stock["quantity"]["maximum"]["count"] == "99"
    out = to_ondc_catalog_item(dict(GOOD_PRODUCT, availability="out_of_stock"), FULLY_CONFIGURED_SELLER)
    assert out["quantity"]["maximum"]["count"] == "0"


def test_returnable_false_omits_return_window_entirely():
    seller = FakeSeller(**{**FULLY_CONFIGURED_SELLER.__dict__, "returnable": False})
    item = to_ondc_catalog_item(GOOD_PRODUCT, seller)
    assert "@ondc/org/return_window" not in item


def test_nothing_configured_raises_with_every_gap_at_once():
    """ONDCMappingError collects ALL reasons together (unlike UCP's
    one-at-a-time UCPMappingError) — confirmed here as more than a
    single reason, not just that it raises at all."""
    with pytest.raises(ONDCMappingError) as exc_info:
        to_ondc_catalog_item({"sku": "X", "name": "Y"}, FakeSeller())
    assert len(exc_info.value.reasons) >= 5


def test_food_category_is_explicitly_refused_not_silently_mismapped():
    spice = dict(GOOD_PRODUCT, category="Spices & Seasoning")
    with pytest.raises(ONDCMappingError) as exc_info:
        to_ondc_catalog_item(spice, FULLY_CONFIGURED_SELLER)
    assert any("FSSAI" in r for r in exc_info.value.reasons)


def test_packer_address_under_three_alpha_chars_is_rejected():
    """Confirmed real ONDC validation rule, not invented."""
    seller = FakeSeller(**{**FULLY_CONFIGURED_SELLER.__dict__, "packer_address": "12/3"})
    with pytest.raises(ONDCMappingError) as exc_info:
        to_ondc_catalog_item(GOOD_PRODUCT, seller)
    assert any("3 alphabetic" in r for r in exc_info.value.reasons)


def test_unmapped_category_is_reported_not_defaulted():
    weird = dict(GOOD_PRODUCT, category="Vintage Vinyl Records")
    with pytest.raises(ONDCMappingError) as exc_info:
        to_ondc_catalog_item(weird, FULLY_CONFIGURED_SELLER)
    assert any("did not map" in r for r in exc_info.value.reasons)


def test_missing_net_quantity_is_a_reported_gap_not_a_fabricated_default():
    no_qty = dict(GOOD_PRODUCT, attributes={"material": "cotton", "manufacture_date": "09/2026"})
    with pytest.raises(ONDCMappingError) as exc_info:
        to_ondc_catalog_item(no_qty, FULLY_CONFIGURED_SELLER)
    assert any("net quantity" in r for r in exc_info.value.reasons)


def test_missing_manufacture_date_is_a_reported_gap_not_a_placeholder():
    """Regression test for a real bug caught during development: this
    field originally got a hardcoded placeholder string instead of
    being treated as a gap like every other statutory field."""
    no_date = dict(GOOD_PRODUCT, attributes={"material": "cotton", "net_quantity": "1 unit"})
    with pytest.raises(ONDCMappingError) as exc_info:
        to_ondc_catalog_item(no_date, FULLY_CONFIGURED_SELLER)
    assert any("manufacture/packing date" in r for r in exc_info.value.reasons)


def test_top_level_net_quantity_and_manufacture_date_work_without_attributes():
    """The new, reliable path from catalog_agent.py's prompt update — no
    attributes dict involved at all."""
    product = dict(GOOD_PRODUCT, attributes={}, net_quantity="250g", manufacture_date="09/2026")
    item = to_ondc_catalog_item(product, FULLY_CONFIGURED_SELLER)
    statutory = item["@ondc/org/statutory_reqs_packaged_commodities"]
    assert statutory["net_quantity_or_measure_of_commodity_in_pkg"] == "250g"
    assert statutory["month_year_of_manufacture_packing_import"] == "09/2026"


def test_top_level_fields_take_priority_over_the_attribute_dict_fallback():
    product = dict(
        GOOD_PRODUCT,
        net_quantity="250g",
        manufacture_date="09/2026",
        attributes={"net_quantity": "WRONG", "manufacture_date": "WRONG"},
    )
    item = to_ondc_catalog_item(product, FULLY_CONFIGURED_SELLER)
    statutory = item["@ondc/org/statutory_reqs_packaged_commodities"]
    assert statutory["net_quantity_or_measure_of_commodity_in_pkg"] == "250g"
    assert statutory["month_year_of_manufacture_packing_import"] == "09/2026"


def test_attribute_dict_fallback_still_works_for_products_ingested_before_the_prompt_change():
    """Regression guard: must not break existing/older-ingested products
    that only ever had these values inside attributes."""
    product = dict(GOOD_PRODUCT)
    product.pop("net_quantity", None)
    product.pop("manufacture_date", None)
    item = to_ondc_catalog_item(product, FULLY_CONFIGURED_SELLER)
    assert item["@ondc/org/statutory_reqs_packaged_commodities"]["net_quantity_or_measure_of_commodity_in_pkg"] == "1 unit"
    