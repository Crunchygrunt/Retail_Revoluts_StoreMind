"""
Tests for ucp_mcp_server.py's tool-wrapping logic ONLY.

SCOPE, stated plainly: this covers the low-risk part of that file —
argument unwrapping and pass-through to ucp_catalog.py's existing,
tested functions. It does NOT cover the MCP transport/mounting layer
(FastMCP's Streamable HTTP app, the app.mount() integration in
main.py) — that requires the real `mcp` package and a real client to
test, neither of which are available where this was written. See
ucp_mcp_server.py's own module docstring for the manual testing steps
that actually exercise the transport layer.
"""

import pytest
from ucp_mcp_server import mcp_search_catalog, mcp_lookup_catalog, mcp_get_product, configure_mcp_store


class FakeStore:
    def __init__(self, products):
        self._products = {p["sku"]: p for p in products}

    def list_products(self, limit=50):
        return list(self._products.values())[:limit]

    def get_product(self, sku):
        return self._products.get(sku)


PRODUCTS = [
    {
        "sku": "KURTA-001",
        "name": "Premium Cotton Kurta",
        "category": "Apparel",
        "description": "Hand-stitched cotton kurta.",
        "price": 1499.0,
        "currency": "INR",
        "availability": "in_stock",
        "keywords": ["kurta", "cotton"],
    },
]


@pytest.fixture(autouse=True)
def store():
    s = FakeStore(PRODUCTS)
    configure_mcp_store(s)
    return s


def test_search_catalog_real_ucp_mcp_request_shape():
    result = mcp_search_catalog(
        catalog={"query": "kurta"},
        meta={"ucp-agent": {"profile": "https://example.com/agent-profile.json"}},
    )
    assert len(result["products"]) == 1
    assert result["products"][0]["id"] == "KURTA-001"
    assert "ucp" in result  # same envelope shape as the REST response


def test_search_catalog_missing_catalog_key_does_not_crash():
    result = mcp_search_catalog(meta={"ucp-agent": {"profile": "x"}})
    assert isinstance(result, dict)


def test_lookup_catalog_wrapped_shape():
    result = mcp_lookup_catalog(catalog={"ids": ["KURTA-001", "GHOST"]})
    assert len(result["products"]) == 1
    assert any(m["code"] == "not_found" for m in result["messages"])


def test_get_product_found():
    result = mcp_get_product(catalog={"id": "KURTA-001"})
    assert "product" in result


def test_get_product_not_found_is_business_outcome():
    result = mcp_get_product(catalog={"id": "GHOST"})
    assert result["ucp"]["status"] == "error"
    
    