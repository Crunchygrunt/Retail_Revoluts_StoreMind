"""
UCP Catalog capability: search_catalog, lookup_catalog, get_product.

REST bindings this backs (ucp_routes.py mounts these):
    POST /catalog/search   -> search_catalog()
    POST /catalog/lookup   -> lookup_catalog()
    POST /catalog/product  -> get_product()

Store-agnostic like storefront.py and currency_inference.py: `store` is
whatever module (local_store or firestore_client) main.py has already
selected, duck-typed to list_products(limit)/get_product(sku).
search_catalog wraps storefront.search_products() rather than
reimplementing catalog querying — one search implementation, ACP-facing
UI and UCP-facing agents both go through it.

Two response-shape asymmetries confirmed by reading the actual ucp.dev
spec examples, not assumed from symmetry between the three endpoints:
  - search / lookup: a not-found or zero-result outcome is still overall
    "success" (no ucp.status field) — only per-item `messages` note it.
  - get_product / product: a not-found id gets an explicit top-level
    ucp.status="error" response, since it's a single-resource operation
    with no partial-success shape to fall back to.
"""

from __future__ import annotations

import base64
from typing import Any

from storefront import search_products as _search_products
from ucp_mapping import to_ucp_catalog_product, UCPMappingError

UCP_VERSION = "2026-04-08"

# Same simplification precedent already set by currency_inference.py's
# QUERY_LIMIT=500 (documented there as "plenty for a stable majority
# vote, not trying to be exhaustive"). storefront.search_products has no
# cursor concept of its own — it takes a flat `limit` and loads that many
# via store.list_products(). Real cursor-based pagination against a
# store that supports server-side cursors (Firestore does) is Tier B
# work, not built here; this offset-in-a-cursor approach is correct and
# testable for a hackathon-scale catalog, not for a 50,000-SKU one.
_MAX_CATALOG_SCAN = 500
_DEFAULT_PAGE_SIZE = 20


def _envelope(capability: str, status: str | None = None) -> dict[str, Any]:
    ucp: dict[str, Any] = {
        "version": UCP_VERSION,
        "capabilities": {capability: [{"version": UCP_VERSION}]},
    }
    if status:
        ucp["status"] = status
    return {"ucp": ucp}


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor.encode()).decode()))
    except Exception:  # noqa: BLE001 — a bad/foreign cursor must not crash search
        return 0


def _mappable_products(structured_list: list[dict[str, Any]], context: str) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]]
]:
    """
    Maps each structured product, separating what succeeded from what
    didn't — same "one bad row doesn't kill the batch" instinct as
    catalog_agent.py's structure_batch(). A product that fails UCP
    mapping (no usable price/currency) is reported via a `messages`
    entry, never silently dropped and never allowed to 500 the request.
    """
    products: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for structured in structured_list:
        try:
            products.append(to_ucp_catalog_product(structured, context=context))
        except UCPMappingError as exc:
            messages.append(
                {
                    "type": "info",
                    "code": "not_ucp_ready",
                    "content": f"{structured.get('sku', '<unknown>')}: {exc}",
                }
            )
    return products, messages


def search_catalog(
    store,
    query: str = "",
    filters: dict[str, Any] | None = None,
    pagination: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """
    Maps to POST /catalog/search. `filters` and `pagination` follow the
    REST spec's request shapes. Returns (http_status, body) — UNLIKE
    lookup_catalog and get_product below, which always return a bare
    dict at implicit HTTP 200. This asymmetry is deliberate, not
    leftover inconsistency: search is the only one of the three catalog
    operations with its own genuine input-validation requirement
    (CAT-009/010, confirmed via a real conformance-test deviation) — a
    request with neither a query string nor filters MUST be rejected as
    malformed, which is a transport-level 400, not a business outcome
    embedded in a 200 body. lookup/get_product have no equivalent
    "at least one input" rule of their own (their input is always a
    concrete id or id list; "not found" is their business-outcome case,
    already handled at 200 per the confirmed spec pattern).

    KNOWN GAP, flagged rather than silently ignored: only `filters.
    categories` is applied (mapped to storefront.search_products'
    existing single-category substring match — first category value
    only, since storefront doesn't support multi-category OR). `filters.
    price` (min/max) is NOT applied — storefront.search_products has no
    price-filtering concept today, and building honest multi-currency
    price filtering (a catalog can hold INR, VND, THB products
    simultaneously) is real work, not a one-line addition. Tier B item,
    not Tier A. Every response still includes the full envelope so a
    caller can tell no price filter was applied by checking the
    products returned, but there's no in-band "filter ignored" message
    in the current spec vocabulary for this — worth raising as a gap if
    it matters for the demo.
    """
    filters = filters or {}
    pagination = pagination or {}

    if not query and not filters:
        return 400, {
            **_envelope("dev.ucp.shopping.catalog.search"),
            "messages": [
                {
                    "type": "error",
                    "code": "missing_search_input",
                    "content": "search requires at least a query string or one filter",
                }
            ],
        }

    category = ""
    categories = filters.get("categories")
    if categories:
        category = str(categories[0])

    offset = _decode_cursor(pagination.get("cursor"))
    limit = int(pagination.get("limit") or _DEFAULT_PAGE_SIZE)

    all_matches = _search_products(store, query=query, category=category, limit=_MAX_CATALOG_SCAN)
    page = all_matches[offset : offset + limit]
    products, messages = _mappable_products(page, context="search")

    response = _envelope("dev.ucp.shopping.catalog.search")
    response["products"] = products
    response["pagination"] = {
        "cursor": _encode_cursor(offset + limit),
        "has_next_page": (offset + limit) < len(all_matches),
        "total_count": len(all_matches),
    }
    if messages:
        response["messages"] = messages
    return 200, response


def lookup_catalog(store, ids: list[str]) -> dict[str, Any]:
    """
    Maps to POST /catalog/lookup. Batch lookup by ID — for this project,
    product ID and variant ID are the same value (sku), since every
    product maps to exactly one synthetic variant (see ucp_mapping.py).

    Confirmed against the real spec's Partial Success example: missing
    identifiers still return HTTP-200-equivalent success with `products`
    containing only what was found, plus informational `not_found`
    messages — never an error for the batch as a whole.
    """
    products: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    seen: set[str] = set()

    for product_id in ids:
        if product_id in seen:
            continue  # spec doesn't define duplicate-id handling; skip silently rather than double-list
        seen.add(product_id)
        structured = store.get_product(product_id)
        if structured is None:
            messages.append({"type": "info", "code": "not_found", "content": product_id})
            continue
        try:
            products.append(to_ucp_catalog_product(structured, context="lookup"))
        except UCPMappingError as exc:
            messages.append(
                {"type": "info", "code": "not_ucp_ready", "content": f"{product_id}: {exc}"}
            )

    response = _envelope("dev.ucp.shopping.catalog.lookup")
    response["products"] = products
    if messages:
        response["messages"] = messages
    return response


def get_product(store, product_id: str) -> dict[str, Any]:
    """
    Maps to POST /catalog/product. Single-resource operation.

    Two distinct failure modes, both returning the confirmed
    ucp.status="error" Error Response shape (never a bare 404 — that's a
    transport-layer decision for ucp_routes.py, not this function; per
    spec, "not found" here is an APPLICATION outcome, HTTP 200):
      - the id doesn't resolve to any product at all -> code "not_found"
      - the id resolves, but the product can't form a valid UCP Price
        (see UCPMappingError) -> code "not_ucp_ready", distinct from
        "not_found" because the honest answer isn't "no such product",
        it's "this product exists but isn't UCP-ready yet" — the same
        distinction ACP's tiering already draws between missing and
        not-yet-conformant.
    """
    structured = store.get_product(product_id)
    if structured is None:
        response = _envelope("dev.ucp.shopping.catalog.lookup", status="error")
        response["messages"] = [
            {
                "type": "error",
                "code": "not_found",
                "content": f"Product not found: {product_id}",
                "severity": "unrecoverable",
            }
        ]
        return response

    try:
        product = to_ucp_catalog_product(structured, context="product")
    except UCPMappingError as exc:
        response = _envelope("dev.ucp.shopping.catalog.lookup", status="error")
        response["messages"] = [
            {
                "type": "error",
                "code": "not_ucp_ready",
                "content": f"{product_id}: {exc}",
                "severity": "unrecoverable",
            }
        ]
        return response

    response = _envelope("dev.ucp.shopping.catalog.lookup")
    response["product"] = product
    return response

