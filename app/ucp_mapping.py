"""
Maps a StoreMind structured product (the same dict catalog_agent.py
produces and firestore_client.py stores under products/{sku}) to UCP's
two distinct product shapes:

  1. Catalog Product entity  — used by /catalog/search, /catalog/lookup,
     /catalog/product. Price is a nested {"amount": int, "currency": str}
     object (a "Price" object per the REST catalog spec).
  2. Checkout line item      — used inside a checkout session. Price is
     a BARE INT in minor units; currency is NOT repeated per-line, it's
     the session's own top-level `currency` field.

These are genuinely different shapes in the real spec, confirmed by
reading the actual request/response examples at ucp.dev/specification/
shopping/catalog/rest/ and developers.google.com/merchant/ucp/
implementation/2026-04-08/native-checkout-api — not assumed identical.
Conflating them (e.g. nesting {amount,currency} inside a checkout line
item, or vice versa) would silently produce a non-conformant server.

SANDBOX NOTE, same pattern as catalog_agent.py's own SANDBOX NOTE
comments: the catalog REST binding was read from ucp.dev's live docs,
which default to the newest spec (2026-08-25), not the 2026-04-08 this
project targets. The DEV.to community writeup of the 2026-04-08 release
confirms catalog_search/catalog_lookup were INTRODUCED in that release
(not present before), and the breaking changes documented between
0.4.x and 0.5.x of the official ucp-sdk are checkout/fulfillment-
specific (import path moves, a new ShippingDestination discriminator),
not catalog-specific. So this shape is very likely stable at
2026-04-08, but that's an inference, not a confirmed read of a
version-pinned page. Before the demo: pip install ucp-sdk==0.4.4 and
diff this module's output against ucp_sdk.models.schemas.shopping's
actual Product/Variant/Price models for the pinned version — that's
the authoritative check this module's docstring can't substitute for.

Variant policy: this project's SME/D2C sellers overwhelmingly have no
real product variants (per UCP_CHECKLIST.md's own research finding,
confirmed against the spec: "Implementations MUST support product ID
and variant ID; MAY support secondary identifiers"). Every product is
mapped to a single synthetic variant carrying the product's own price
and availability — this is spec-legitimate, not a workaround: nothing
in the spec requires more than one variant per product.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ucp_money import to_minor_units, UnknownCurrencyPrecisionError

# availability values this project actually produces — see acp_schema.py
# AVAILABILITY_VALUES. Only "in_stock" is mapped to UCP available=True.
# pre_order/backorder are arguably "available" in a looser sense, but a
# hackathon demo should never overstate purchasability; this is a
# deliberate, documented choice, not a spec requirement — revisit if a
# judge or a real seller pushes back on pre-order handling specifically.
_UCP_AVAILABLE_VALUES = {"in_stock"}


class UCPMappingError(ValueError):
    """
    Raised when a structured product cannot be honestly represented in
    UCP — currently only when price+currency can't form a valid UCP
    Price object. Callers (ucp_catalog.py) must catch this per-product
    and report it via the UCP `messages` array, never let it 500 the
    whole batch — same "one bad row must not kill the request" instinct
    already applied in catalog_agent.py's structure_batch() and
    storefront.py's per-sku handling.
    """


def is_ucp_purchasable(structured: dict[str, Any]) -> bool:
    """
    Whether this product's stated availability allows it into a UCP
    checkout — the SAME policy _build_variant already uses to set the
    catalog variant's availability.available flag for DISPLAY.

    Exposed as a standalone predicate, not left inlined inside
    _build_variant, specifically so ucp_session.py's checkout GATE can
    call this exact function rather than re-deciding the policy itself.
    Two independent implementations of "is this in stock" is exactly
    how a catalog page and a checkout gate drift apart — found the hard
    way: this function didn't exist until a conformance-test fixture
    (an out_of_stock_id) exposed that the checkout gate wasn't checking
    availability AT ALL, only readiness threshold and price/currency
    mappability. Same reuse principle already applied to
    storefront.checkout() for the readiness gate itself.
    """
    return structured.get("availability") in _UCP_AVAILABLE_VALUES


def _require_ucp_price(structured: dict[str, Any]) -> dict[str, Any]:
    """
    Builds a UCP Price object: {"amount": <int minor units>, "currency":
    "XXX"}. The REST catalog spec's own conformance requirement #2 is
    blunt about this: "Return products with valid Price objects (amount
    + currency)" — there's no partial-credit path, a product without a
    usable price/currency simply cannot be returned by catalog endpoints.

    Raises UCPMappingError (not a silent fallback) if price is missing
    or currency can't be converted — including the case where currency
    is an unmapped raw token from currency_inference.py's fallback path
    (ucp_money.to_minor_units already refuses to guess a precision for
    those; this function lets that refusal propagate rather than
    catching and hiding it).
    """
    price = structured.get("price")
    currency = structured.get("currency")
    if price is None:
        raise UCPMappingError(f"sku {structured.get('sku')!r}: no price")
    if not currency:
        raise UCPMappingError(f"sku {structured.get('sku')!r}: no currency")
    try:
        decimal_price = Decimal(str(price))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise UCPMappingError(
            f"sku {structured.get('sku')!r}: price {price!r} is not a valid number"
        ) from exc
    try:
        minor = to_minor_units(decimal_price, currency)
    except UnknownCurrencyPrecisionError as exc:
        raise UCPMappingError(
            f"sku {structured.get('sku')!r}: currency {currency!r} is not usable in UCP — {exc}"
        ) from exc
    return {"amount": minor, "currency": currency.upper()}


def _build_variant(
    structured: dict[str, Any],
    context: str,
) -> dict[str, Any]:
    """
    The single synthetic variant for a no-variant SME product.

    `context` picks which of the two mutually-exclusive fields the real
    spec examples show on a variant, confirmed by reading actual
    response bodies rather than assuming symmetry:
      - "search" / "product"  -> include `options` (empty list — the
        Detail Product entity docs explicitly allow "empty or omitted
        for products with no option axes")
      - "lookup"              -> include `inputs` instead of `options`,
        recording which product ID this variant resolves from and how
        it matched. "featured" is the correct match type for a
        single-variant product's only variant (spec's own worked
        example uses "featured" for exactly this shape).
    """
    sku = str(structured.get("sku") or "")
    price = _require_ucp_price(structured)
    variant: dict[str, Any] = {
        "id": sku,  # no separate variant-id concept — product IS the variant
        "sku": sku,
        "title": str(structured.get("name") or ""),
        "description": {"plain": str(structured.get("description") or "")},
        "price": price,
        "availability": {
            "available": is_ucp_purchasable(structured)
        },
    }
    if context in ("search", "product"):
        variant["options"] = []
    elif context == "lookup":
        variant["inputs"] = [{"id": sku, "match": "featured"}]
    else:
        raise ValueError(f"unknown variant context {context!r}")
    return variant


def to_ucp_catalog_product(
    structured: dict[str, Any],
    context: str = "search",
) -> dict[str, Any]:
    """
    Maps a structured product to a UCP Catalog Product entity, for use
    in /catalog/search, /catalog/lookup, or /catalog/product responses.

    context: "search" | "lookup" | "product" — selects the variant shape
    (see _build_variant) since the three catalog endpoints use slightly
    different variant fields in the real spec examples.

    Raises UCPMappingError if the product can't form a valid UCP Price
    (see _require_ucp_price) — callers must catch this per-product.

    Deliberately omits fields this project has no honest data for
    rather than inventing them: `handle` (no distinct slug concept from
    `sku`), `categories` (no taxonomy mapping built), `rating`,
    `metadata`. Matches acp_schema.py's own philosophy — report or omit,
    never guess. `media` is included only when image_url is actually
    present, never a placeholder image.
    """
    price = _require_ucp_price(structured)
    product: dict[str, Any] = {
        "id": str(structured.get("sku") or ""),
        "title": str(structured.get("name") or ""),
        "description": {"plain": str(structured.get("description") or "")},
        "price_range": {"min": price, "max": price},  # single variant -> min == max
        "variants": [_build_variant(structured, context)],
    }
    url = structured.get("url")
    if url:
        product["url"] = str(url)
    image_url = structured.get("image_url")
    if image_url:
        product["media"] = [
            {
                "type": "image",
                "url": str(image_url),
                "alt_text": str(structured.get("name") or ""),
            }
        ]
    if context in ("search", "product"):
        product["options"] = []
    return product


def to_ucp_checkout_line_item(structured: dict[str, Any]) -> dict[str, Any]:
    """
    Maps a structured product to a UCP checkout session line item's
    `item` object: {"id", "title", "price", "image_url"}.

    IMPORTANT — confirmed by reading the actual create-checkout-session
    example in Google's spec, not assumed from the catalog shape above:
    `price` here is a BARE INT in minor units. There is no nested
    {"amount", "currency"} object at this level — the session carries
    one top-level `currency` field for the whole session instead. Do
    not reuse _require_ucp_price's dict output directly here.

    Raises UCPMappingError under the same conditions as the catalog
    mapping (see _require_ucp_price) — a product that can't checkout-
    price also can't appear in a checkout session line item, which is
    consistent with it never having been checkout-eligible in the first
    place (see ucp_eligibility.py).
    """
    price_obj = _require_ucp_price(structured)  # validates + gets minor units
    item: dict[str, Any] = {
        "id": str(structured.get("sku") or ""),
        "title": str(structured.get("name") or ""),
        "price": price_obj["amount"],
    }
    image_url = structured.get("image_url")
    if image_url:
        item["image_url"] = str(image_url)
    return item


def checkout_session_currency(structured: dict[str, Any]) -> str:
    """
    The session-level `currency` field a checkout session carrying this
    product must declare. Thin wrapper, but exists so ucp_session.py
    never reads structured["currency"] directly and risks skipping the
    same validation _require_ucp_price already does.
    """
    return _require_ucp_price(structured)["currency"]

