"""
Tests for ucp_catalog.py, run against the real storefront.search_products
(not a mock of it) and a FakeStore whose interface matches
firestore_client.py's real function signatures exactly —
list_products(limit), get_product(sku) — so this is a genuine stand-in
for local_store.py / firestore_client.py, not an invented mock.
"""

import pytest
from ucp_catalog import search_catalog, lookup_catalog, get_product, _decode_cursor, _encode_cursor


class FakeStore:
    def __init__(self, products):
        self._products = {p["sku"]: p for p in products}

    def list_products(self, limit=50):
        return list(self._products.values())[:limit]

    def get_product(self, sku):
        return self._products.get(sku)


PRODUCTS = [
    {
        "sku": "KURTA-001", "name": "Premium Cotton Kurta", "category": "Apparel",
        "description": "Hand-stitched cotton kurta in indigo blue.",
        "price": 1499.0, "currency": "INR", "availability": "in_stock",
        "image_url": "https://cdn.example.com/kurta001.jpg",
        "url": "https://storemind-508614.web.app/products/KURTA-001",
        "keywords": ["kurta", "cotton", "indigo"],
    },
    {
        "sku": "SAREE-002", "name": "Silk Banarasi Saree", "category": "Apparel",
        "description": "Traditional Banarasi silk saree with gold zari work.",
        "price": 8999.0, "currency": "INR", "availability": "in_stock",
        "image_url": "https://cdn.example.com/saree002.jpg",
        "keywords": ["saree", "silk", "banarasi"],
    },
    {
        "sku": "AODAI-003", "name": "Ao Dai Traditional Dress", "category": "Apparel",
        "description": "Vietnamese Ao Dai in red silk.",
        "price": 250000.0, "currency": "VND", "availability": "out_of_stock",
        "keywords": ["ao dai", "vietnamese", "traditional"],
    },
    {
        "sku": "NOPRICE-004", "name": "Draft Product", "category": "Apparel",
        "description": "Not yet priced.",
        "price": None, "currency": None, "availability": "unknown",
        "keywords": [],
    },
    {
        "sku": "BADCUR-005", "name": "Unmapped Currency Product", "category": "Apparel",
        "description": "Currency symbol never resolved to ISO code.",
        "price": 500.0, "currency": "Rs", "availability": "in_stock",
        "keywords": [],
    },
]


@pytest.fixture
def store():
    return FakeStore(PRODUCTS)


# --- search_catalog ---

def test_search_basic_query(store):
    status, resp = search_catalog(store, query="kurta")
    assert status == 200
    assert resp["ucp"]["version"] == "2026-04-08"
    assert "dev.ucp.shopping.catalog.search" in resp["ucp"]["capabilities"]
    assert "status" not in resp["ucp"]  # successful search omits status
    assert len(resp["products"]) == 1
    assert resp["products"][0]["id"] == "KURTA-001"
    assert "pagination" in resp
    assert not resp.get("messages")


def test_search_rejects_fully_empty_request(store):
    """
    CAT-009/CAT-010: a search with neither a query nor filters MUST be
    rejected as malformed — found via a real conformance-test deviation
    (catalog.search_requires_input). Distinct from get_product's "not
    found" business outcome: this is a transport-level 400, since
    there's no valid business question being asked at all.
    """
    status, resp = search_catalog(store, query="", filters=None)
    assert status == 400
    assert resp["messages"][0]["code"] == "missing_search_input"


def test_search_category_filter_reports_unmappable_without_dropping_silently(store):
    status, resp = search_catalog(store, query="", filters={"categories": ["Apparel"]})
    assert status == 200
    returned_ids = {p["id"] for p in resp["products"]}
    assert {"KURTA-001", "SAREE-002", "AODAI-003"} <= returned_ids
    assert "NOPRICE-004" not in returned_ids
    assert "BADCUR-005" not in returned_ids
    messages = resp.get("messages", [])
    assert any("NOPRICE-004" in m["content"] for m in messages)
    assert any("BADCUR-005" in m["content"] for m in messages)
    assert all(m["code"] == "not_ucp_ready" for m in messages)


def test_search_vnd_product_priced_correctly_through_full_stack(store):
    status, resp = search_catalog(store, query="ao dai")
    assert status == 200
    product = resp["products"][0]
    assert product["price_range"]["min"]["amount"] == 250000
    assert product["variants"][0]["availability"]["available"] is False


def test_search_pagination_cursor_round_trip(store):
    status1, page1 = search_catalog(store, query="", filters={"categories": ["Apparel"]}, pagination={"limit": 1})
    assert status1 == 200
    assert len(page1["products"]) == 1
    assert page1["pagination"]["has_next_page"] is True
    cursor = page1["pagination"]["cursor"]
    status2, page2 = search_catalog(
        store, query="", filters={"categories": ["Apparel"]}, pagination={"limit": 1, "cursor": cursor}
    )
    assert status2 == 200
    assert page2["products"][0]["id"] != page1["products"][0]["id"]


def test_decode_cursor_round_trip():
    assert _decode_cursor(_encode_cursor(7)) == 7


def test_decode_cursor_garbage_does_not_crash():
    assert _decode_cursor("not-a-real-cursor!!!") == 0


# --- lookup_catalog ---

def test_lookup_partial_success(store):
    resp = lookup_catalog(store, ["KURTA-001", "DOES-NOT-EXIST", "SAREE-002"])
    assert "status" not in resp["ucp"]  # partial success is still overall success
    assert {p["id"] for p in resp["products"]} == {"KURTA-001", "SAREE-002"}
    assert any(
        m["code"] == "not_found" and m["content"] == "DOES-NOT-EXIST" for m in resp["messages"]
    )
    assert "inputs" in resp["products"][0]["variants"][0]


def test_lookup_all_not_found(store):
    resp = lookup_catalog(store, ["GHOST-1", "GHOST-2"])
    assert resp["products"] == []
    assert "status" not in resp["ucp"]
    assert len(resp["messages"]) == 2


def test_lookup_duplicate_ids_not_double_listed(store):
    resp = lookup_catalog(store, ["KURTA-001", "KURTA-001"])
    assert len(resp["products"]) == 1


# --- get_product ---

def test_get_product_found(store):
    resp = get_product(store, "KURTA-001")
    assert "status" not in resp["ucp"]
    assert "product" in resp
    assert "products" not in resp
    assert "options" in resp["product"]["variants"][0]


def test_get_product_not_found(store):
    resp = get_product(store, "GHOST-999")
    assert resp["ucp"]["status"] == "error"
    assert resp["messages"][0]["severity"] == "unrecoverable"
    assert resp["messages"][0]["code"] == "not_found"
    assert "product" not in resp


def test_get_product_exists_but_not_ucp_ready(store):
    resp = get_product(store, "BADCUR-005")
    assert resp["ucp"]["status"] == "error"
    assert resp["messages"][0]["code"] == "not_ucp_ready"
    
    