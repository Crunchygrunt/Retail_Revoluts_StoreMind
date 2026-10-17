"""
Tests for ucp_mapping.py, against realistic structured-product shapes
(the actual dict shape catalog_agent.py produces / firestore_client.py
stores), not synthetic minimal fixtures.
"""

import pytest
from ucp_mapping import (
    to_ucp_catalog_product,
    to_ucp_checkout_line_item,
    checkout_session_currency,
    is_ucp_purchasable,
    UCPMappingError,
)


GOOD_PRODUCT = {
    "sku": "KURTA-001",
    "name": "Premium Cotton Kurta",
    "category": "Apparel",
    "description": "Hand-stitched cotton kurta in indigo blue, size M-XL.",
    "attributes": {"material": "cotton", "color": "indigo"},
    "keywords": ["kurta", "cotton", "indigo", "ethnic wear"],
    "price": 1499.0,
    "currency": "INR",
    "brand": "Fabindia",
    "brand_source": "source",
    "url": "https://storemind-508614.web.app/products/KURTA-001",
    "image_url": "https://storage.googleapis.com/storemind-images/abc123.jpg",
    "availability": "in_stock",
    "seller_name": "Zara",
    "readiness_score": 82.3,
}


# --- catalog product mapping: search context ---

def test_search_context_shape():
    p = to_ucp_catalog_product(GOOD_PRODUCT, context="search")
    assert p["id"] == "KURTA-001"
    assert p["title"] == "Premium Cotton Kurta"
    assert p["description"]["plain"] == GOOD_PRODUCT["description"]
    assert p["price_range"]["min"] == p["price_range"]["max"]
    assert p["price_range"]["min"] == {"amount": 149900, "currency": "INR"}
    assert p["url"] == GOOD_PRODUCT["url"]
    assert p["media"][0]["url"] == GOOD_PRODUCT["image_url"]
    assert p["media"][0]["type"] == "image"
    assert len(p["variants"]) == 1
    assert p["variants"][0]["options"] == []
    assert "inputs" not in p["variants"][0]
    assert p["variants"][0]["availability"] == {"available": True}
    assert p.get("options") == []


def test_lookup_context_uses_inputs_not_options():
    p = to_ucp_catalog_product(GOOD_PRODUCT, context="lookup")
    assert p["variants"][0].get("inputs") == [{"id": "KURTA-001", "match": "featured"}]
    assert "options" not in p["variants"][0]
    assert "options" not in p  # top-level options only on search/product


def test_product_context_uses_options():
    p = to_ucp_catalog_product(GOOD_PRODUCT, context="product")
    assert p["variants"][0]["options"] == []


# --- availability mapping ---

@pytest.mark.parametrize(
    "availability,expected_available",
    [
        ("in_stock", True),
        ("out_of_stock", False),
        ("pre_order", False),  # deliberate conservative choice, documented in ucp_mapping.py
        ("backorder", False),
        ("unknown", False),
    ],
)
def test_availability_mapping(availability, expected_available):
    product = dict(GOOD_PRODUCT, availability=availability)
    p = to_ucp_catalog_product(product, context="search")
    assert p["variants"][0]["availability"] == {"available": expected_available}


# --- is_ucp_purchasable: the shared predicate the checkout gate reuses,
# added after a real conformance-test run exposed the checkout gate
# wasn't checking availability at all (see ucp_session.py) ---

def test_is_ucp_purchasable_in_stock():
    assert is_ucp_purchasable(GOOD_PRODUCT) is True


def test_is_ucp_purchasable_out_of_stock():
    product = dict(GOOD_PRODUCT, availability="out_of_stock")
    assert is_ucp_purchasable(product) is False


def test_is_ucp_purchasable_missing_availability():
    assert is_ucp_purchasable({}) is False


# --- optional fields omitted, never defaulted to a placeholder ---

def test_missing_image_url_omits_media_key_entirely():
    product = {k: v for k, v in GOOD_PRODUCT.items() if k != "image_url"}
    p = to_ucp_catalog_product(product, context="search")
    assert "media" not in p


def test_missing_url_omits_url_key_entirely():
    product = {k: v for k, v in GOOD_PRODUCT.items() if k != "url"}
    p = to_ucp_catalog_product(product, context="search")
    assert "url" not in p


# --- error cases: product cannot be honestly represented in UCP ---

def test_missing_price_raises():
    product = {k: v for k, v in GOOD_PRODUCT.items() if k != "price"}
    with pytest.raises(UCPMappingError):
        to_ucp_catalog_product(product)


def test_missing_currency_raises():
    product = {k: v for k, v in GOOD_PRODUCT.items() if k != "currency"}
    with pytest.raises(UCPMappingError):
        to_ucp_catalog_product(product)


def test_unmapped_raw_token_currency_raises():
    """
    Regression case for currency_inference.py's documented raw-token
    fallback: an unmapped symbol is stored as-is, not guaranteed ISO
    4217. This must surface as a reportable gap, never a silently
    wrong price.
    """
    product = dict(GOOD_PRODUCT, currency="Rs")
    with pytest.raises(UCPMappingError):
        to_ucp_catalog_product(product)


def test_vnd_product_not_inflated_through_full_mapping():
    product = dict(GOOD_PRODUCT, sku="AO-DAI-002", price=250000.0, currency="VND")
    p = to_ucp_catalog_product(product, context="search")
    assert p["price_range"]["min"]["amount"] == 250000


# --- checkout line item: the OTHER price shape ---

def test_checkout_line_item_shape():
    item = to_ucp_checkout_line_item(GOOD_PRODUCT)
    assert item["id"] == "KURTA-001"
    assert item["title"] == "Premium Cotton Kurta"
    assert item["price"] == 149900
    assert isinstance(item["price"], int)
    assert item["image_url"] == GOOD_PRODUCT["image_url"]
    assert "currency" not in item  # session-level field, not per-line-item


def test_checkout_line_item_omits_image_when_absent():
    product = {k: v for k, v in GOOD_PRODUCT.items() if k != "image_url"}
    item = to_ucp_checkout_line_item(product)
    assert "image_url" not in item


# --- session currency extraction ---

def test_checkout_session_currency():
    assert checkout_session_currency(GOOD_PRODUCT) == "INR"


def test_checkout_session_currency_vnd():
    product = dict(GOOD_PRODUCT, currency="VND")
    assert checkout_session_currency(product) == "VND"
    
    