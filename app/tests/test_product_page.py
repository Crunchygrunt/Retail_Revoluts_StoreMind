"""
Tests for product_page.py — the generated canonical product page and
its schema.org Product JSON-LD.
"""

from __future__ import annotations

import json
import re

from product_page import (
    build_product_jsonld,
    build_product_url,
    render_product_page,
    validate_product_jsonld,
)


def _product(**overrides) -> dict:
    base = {
        "sku": "SKU-1",
        "name": "Premium Handwoven Cotton Kurta",
        "description": "A handwoven cotton kurta with traditional embroidery.",
        "brand": "Fabindia",
        "category": "apparel",
        "price": 1499.0,
        "currency": "INR",
        "image_url": "https://cdn.example.com/abc.jpg",
        "attributes": {"material": "cotton", "color": "indigo"},
        "availability": "in_stock",
    }
    base.update(overrides)
    return base


def _extract_jsonld(html_text: str) -> dict:
    match = re.search(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
        html_text,
        re.DOTALL,
    )
    assert match, "page must embed a JSON-LD block"
    return json.loads(match.group(1))

# ---------------------------------------------------------------------
# build_product_url
# ---------------------------------------------------------------------

def test_seller_supplied_url_always_wins():
    result = build_product_url({"url": "https://seller.example/kurta"}, "SKU-1")
    assert result.source == "source"
    assert result.url == "https://seller.example/kurta"


def test_url_is_generated_when_the_seller_has_none():
    result = build_product_url({}, "SKU-1")
    assert result.source == "generated"
    assert result.url.endswith("/p/SKU-1")


def test_a_localhost_base_url_is_flagged_as_unreachable():
    """A generated URL pointing at localhost would silently ship an
    unreachable link into a real ACP feed."""
    result = build_product_url({}, "SKU-1")
    assert any("not reachable" in n for n in result.notes)


def test_a_non_http_url_field_is_not_trusted_as_a_url():
    result = build_product_url({"url": "see our instagram"}, "SKU-1")
    assert result.source == "generated"


# ---------------------------------------------------------------------
# build_product_jsonld
# ---------------------------------------------------------------------

def test_jsonld_has_the_required_schema_org_envelope():
    doc = build_product_jsonld(_product(), "https://x/p/SKU-1")
    assert doc["@context"] == "https://schema.org/"
    assert doc["@type"] == "Product"


def test_jsonld_carries_the_properties_agents_actually_match_on():
    doc = build_product_jsonld(_product(), "https://x/p/SKU-1")
    for key in ("name", "sku", "description", "image", "brand", "offers"):
        assert key in doc, f"missing agent-relevant property: {key}"
    assert doc["brand"] == {"@type": "Brand", "name": "Fabindia"}
    assert doc["offers"]["price"] == "1499.00"
    assert doc["offers"]["priceCurrency"] == "INR"
    assert doc["offers"]["availability"] == "https://schema.org/InStock"


def test_placeholder_brand_is_not_asserted_as_a_real_brand():
    """A placeholder brand must never be emitted as a schema.org Brand —
    it would assert something false to an agent. (The pipeline no longer
    produces 'Unbranded' at all, but a hand-edited record still could.)"""
    doc = build_product_jsonld(_product(brand="Unbranded"), "https://x/p/SKU-1")
    assert "brand" not in doc


# ---------------------------------------------------------------------
# Availability — corrected from a hardcoded InStock
# ---------------------------------------------------------------------

def test_availability_reflects_the_products_real_status():
    """Previously hardcoded to InStock on every page regardless of
    reality. An agent crawling `url` to verify stock against the feed
    would have found the two disagreeing."""
    cases = {
        "in_stock": "https://schema.org/InStock",
        "out_of_stock": "https://schema.org/OutOfStock",
        "pre_order": "https://schema.org/PreOrder",
        "backorder": "https://schema.org/BackOrder",
    }
    for value, expected in cases.items():
        doc = build_product_jsonld(_product(availability=value), "https://x/p/1")
        assert doc["offers"]["availability"] == expected, value


def test_unknown_availability_emits_no_offer_at_all():
    """The spec notes `unknown` does not assert the item is in stock, so
    claiming an offer while admitting we don't know would be
    unsupportable."""
    doc = build_product_jsonld(_product(availability="unknown"), "https://x/p/1")
    assert "offers" not in doc


def test_missing_availability_emits_no_offer():
    product = _product()
    del product["availability"]
    assert "offers" not in build_product_jsonld(product, "https://x/p/1")


def test_a_checkout_blocked_product_never_advertises_itself_as_buyable():
    """A product StoreMind's own readiness gate blocked must not tell an
    agent it can be purchased, whatever its stock status says."""
    doc = build_product_jsonld(
        _product(availability="in_stock", is_eligible_checkout=False), "https://x/p/1"
    )
    assert doc["offers"]["availability"] == "https://schema.org/OutOfStock"


def test_a_malformed_price_does_not_raise_a_500():
    """An ACP crawler hitting `url` must get a valid page back even when
    the underlying data is bad — a 500 looks like an outage and risks
    the whole endpoint being down-ranked."""
    for bad_price in ("not-a-number", object(), [1, 2]):
        page = render_product_page(_product(price=bad_price), "https://x/p/1")
        assert "<h1>" in page


def test_missing_properties_are_omitted_not_emitted_empty():
    """An agent treats an absent property and a blank one differently —
    a blank one reads as a broken feed."""
    sparse = {"sku": "SKU-2", "name": "Loose sugar 1kg"}
    doc = build_product_jsonld(sparse, "https://x/p/SKU-2")
    for key in ("image", "brand", "offers", "description", "additionalProperty"):
        assert key not in doc


def test_offers_is_omitted_when_currency_is_unknown():
    """A price with no currency is ambiguous — 1499 could be rupees or
    dollars. Emitting it would misstate the price to an agent."""
    doc = build_product_jsonld(_product(currency=None), "https://x/p/SKU-1")
    assert "offers" not in doc


def test_currency_is_normalised_to_uppercase_iso():
    doc = build_product_jsonld(_product(currency="inr"), "https://x/p/1")
    assert doc["offers"]["priceCurrency"] == "INR"


def test_structured_attributes_become_additional_property():
    doc = build_product_jsonld(_product(), "https://x/p/SKU-1")
    props = {p["name"]: p["value"] for p in doc["additionalProperty"]}
    assert props == {"material": "cotton", "color": "indigo"}
    assert all(p["@type"] == "PropertyValue" for p in doc["additionalProperty"])


def test_no_invented_gtin_or_rating_is_emitted():
    """StoreMind has no GTIN source and no review data. A feed and a
    page that disagree get the product down-weighted for trust."""
    doc = build_product_jsonld(_product(), "https://x/p/SKU-1")
    for key in ("gtin", "gtin13", "mpn", "aggregateRating", "review"):
        assert key not in doc


def test_validate_reports_gaps_without_raising():
    assert validate_product_jsonld(build_product_jsonld(_product(), "https://x/p/1")) == []
    problems = validate_product_jsonld({"name": "x"})
    assert problems and isinstance(problems, list)


# ---------------------------------------------------------------------
# render_product_page
# ---------------------------------------------------------------------

def test_page_embeds_parseable_jsonld():
    doc = _extract_jsonld(render_product_page(_product(), "https://x/p/SKU-1"))
    assert doc["@type"] == "Product"
    assert doc["name"] == "Premium Handwoven Cotton Kurta"


def test_page_renders_without_an_image():
    page = render_product_page(_product(image_url=None), "https://x/p/SKU-1")
    assert "<img" not in page
    assert "og:image" not in page


def test_seller_supplied_text_is_html_escaped():
    """Catalog text is seller-supplied and reaches this unfiltered — for
    HTML purposes it's untrusted input."""
    page = render_product_page(
        _product(name='<script>alert("xss")</script>', description="a & b < c"),
        "https://x/p/SKU-1",
    )
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_a_closing_script_tag_in_catalog_text_cannot_break_out_of_the_jsonld_block():
    """The JSON-LD sits inside a <script> element. A raw json.dumps would
    let a product name containing "</script>" terminate that element and
    inject markup into the page. Regression guard for that specific
    hole — and the JSON must still parse afterwards."""
    hostile = '</script><script>alert("xss")</script>'
    page = render_product_page(_product(name=hostile), "https://x/p/SKU-1")

    # Exactly one script element on the page: the JSON-LD one.
    assert page.count("<script") == 1, "catalog text opened a second <script> element"
    assert page.count("</script>") == 1

    doc = _extract_jsonld(page)
    assert doc["name"] == hostile, "escaping must be reversible — agents have to parse this"


def test_escaping_does_not_corrupt_the_jsonld_block():
    """The JSON-LD is JSON-encoded, not HTML-escaped — escaping it would
    break the parse for the agents this page exists to serve."""
    doc = _extract_jsonld(render_product_page(_product(name="Kurta & Co"), "https://x/p/1"))
    assert doc["name"] == "Kurta & Co"


def test_page_declares_a_canonical_url():
    page = render_product_page(_product(), "https://x/p/SKU-1")
    assert '<link rel="canonical" href="https://x/p/SKU-1">' in page


def test_non_latin_product_text_survives_rendering():
    """The target segment is multilingual — Thai, Hindi and Khmer names
    must not be mangled or escaped into entities."""
    doc = _extract_jsonld(render_product_page(_product(name="ผ้าไหมไทย"), "https://x/p/1"))
    assert doc["name"] == "ผ้าไหมไทย"
    
    