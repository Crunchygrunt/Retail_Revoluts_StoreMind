"""
Canonical product page — closes ACP's required `url` field.

The gap: ACP requires a `url` per product, but the target segment sells
on WhatsApp, Instagram and marketplace listings and frequently has no
website at all. There is no URL to supply. Rather than excluding those
sellers, StoreMind SERVES the page — so the URL isn't invented, it's a
real, dereferenceable page backed by the structured catalog StoreMind
already built.

Why the page carries schema.org Product JSON-LD rather than being a
plain HTML card: research into how shopping agents actually consume
product pages is unambiguous that JSON-LD is the machine-readable
contract they parse — agents prefer product feeds and JSON-LD Product
schema over rendered HTML, and a product with neither is effectively
invisible to them; Google explicitly recommends JSON-LD over Microdata
or RDFa. So a generated page that merely LOOKS like a product page
would satisfy the letter of ACP's `url` requirement while delivering
none of its value. The JSON-LD block is the part that does the work.

Property selection follows the same sources: `name`, `image`,
`description`, `offers` (with `price`, `priceCurrency`, `availability`),
`brand` and `sku` are the widely-cited minimum for a product to be a
candidate at all. `gtin`/`mpn` and `aggregateRating` are recommended too
but are deliberately OMITTED here — StoreMind has no GTIN source and no
review data, and emitting empty or invented values for them would be
worse than omitting them (an agent that finds a feed and a page
disagreeing down-weights the product for trust reasons).

On NOT using a schema.org library: `pydantic2-schemaorg` and
`msgspec-schemaorg` both exist and generate the full schema.org
vocabulary as typed models. They were evaluated and skipped, with
reasons: this module emits exactly ONE type (Product), the output is a
~15-key dict rather than an algorithm, and the generated packages are
very large for that (the schemaorg-pydantic docs note an all-models file
runs to 16 MB). The hard part here was deciding WHICH properties agents
actually weigh — that came from research, and no library supplies it.
The emitted JSON-LD is instead validated against a small local Pydantic
model (below) so it isn't unchecked.
"""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

SCHEMA_CONTEXT = "https://schema.org/"

# schema.org availability URLs, keyed by the ACP availability value.
# Previously this module hardcoded InStock on every page regardless of
# the product's real status — meaning a blocked or out-of-stock product
# still advertised itself as buyable to any agent that read the page.
# An agent that crawls `url` to verify price/stock consistency against
# the feed would find the two disagreeing, which is worse for trust than
# having no structured data at all.
AVAILABILITY_TO_SCHEMA = {
    "in_stock": "https://schema.org/InStock",
    "out_of_stock": "https://schema.org/OutOfStock",
    "pre_order": "https://schema.org/PreOrder",
    "backorder": "https://schema.org/BackOrder",
}


def public_base_url() -> str:
    """Base URL this StoreMind instance is reachable at. On Cloud Run
    this is the service URL; locally it defaults to the dev server.

    A localhost default is obviously not resolvable by an external
    agent — build_product_url tags that case so it's visible rather
    than silently shipping unreachable URLs into a feed.
    """
    return os.environ.get("STOREMIND_PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")


@dataclass
class ResolvedUrl:
    url: str
    source: str  # "source" | "generated"
    notes: list[str] = field(default_factory=list)


def build_product_url(raw_row: dict[str, Any], sku: str) -> ResolvedUrl:
    """Resolves ACP's `url`. A seller-supplied product URL always wins;
    otherwise StoreMind's own canonical page is used."""
    existing = raw_row.get("url") or raw_row.get("product_url")
    if isinstance(existing, str) and existing.strip().lower().startswith(("http://", "https://")):
        return ResolvedUrl(url=existing.strip(), source="source")

    base = public_base_url()
    notes: list[str] = []
    if "localhost" in base or "127.0.0.1" in base:
        notes.append(
            "STOREMIND_PUBLIC_BASE_URL is unset or local — generated URLs "
            "are not reachable by an external agent; set it to the Cloud Run "
            "service URL before submitting a real feed"
        )
    return ResolvedUrl(url=f"{base}/p/{sku}", source="generated", notes=notes)


class _OfferModel(BaseModel):
    price: str
    priceCurrency: str
    availability: str
    url: str


class _ProductJsonLdModel(BaseModel):
    """Local validation of the emitted JSON-LD. Deliberately small — see
    the module docstring on why a full schema.org package isn't used.
    Field names match schema.org exactly, which is why they aren't
    snake_case."""

    name: str
    description: str
    image: str
    sku: str


def build_product_jsonld(product: dict[str, Any], page_url: str) -> dict[str, Any]:
    """Builds the schema.org Product JSON-LD block.

    Only emits properties backed by real data. A missing image, brand or
    price is left OUT of the block rather than emitted empty — an agent
    treats an absent property and a blank one differently, and a blank
    one reads as a broken feed.
    """
    doc: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": "Product",
        "name": product.get("name") or "",
        "sku": product.get("sku") or "",
    }

    description = product.get("description")
    if description:
        doc["description"] = description

    image_url = product.get("image_url")
    if image_url:
        doc["image"] = image_url

    brand = product.get("brand")
    # "Unbranded" is brand_resolution.py's honest fallback for "we don't
    # know", not a brand name. Emitting it as schema.org/Brand would
    # assert something false, so it's omitted from the structured data
    # while remaining present in the ACP feed field.
    if brand and brand != "Unbranded":
        doc["brand"] = {"@type": "Brand", "name": brand}

    category = product.get("category")
    if category:
        doc["category"] = category

    # Offers: only emitted when the product's real status maps to a
    # schema.org availability value. An `unknown` availability
    # deliberately produces NO offers block — asserting an offer while
    # admitting we don't know the stock status would be a claim we
    # can't back, and the spec itself notes `unknown` "does not assert
    # that the item is in stock".
    price = product.get("price")
    currency = product.get("currency")
    availability = str(product.get("availability") or "").strip()
    schema_availability = AVAILABILITY_TO_SCHEMA.get(availability)

    # A product blocked by StoreMind's own readiness gate must not
    # advertise itself as purchasable, whatever its stock status says.
    if product.get("is_eligible_checkout") is False and schema_availability:
        schema_availability = AVAILABILITY_TO_SCHEMA["out_of_stock"]

    if price is not None and currency and schema_availability:
        try:
            price_text = f"{float(price):.2f}"
        except (TypeError, ValueError):
            price_text = None
        if price_text:
            doc["offers"] = {
                "@type": "Offer",
                "url": page_url,
                "price": price_text,
                "priceCurrency": str(currency).strip().upper(),
                "availability": schema_availability,
            }

    attributes = product.get("attributes") or {}
    if isinstance(attributes, dict) and attributes:
        # additionalProperty is the schema.org-sanctioned way to carry
        # arbitrary key/value product attributes, which is precisely
        # what the Catalog Structuring Agent produces.
        doc["additionalProperty"] = [
            {"@type": "PropertyValue", "name": str(k), "value": str(v)}
            for k, v in attributes.items()
        ]

    return doc


def validate_product_jsonld(doc: dict[str, Any]) -> list[str]:
    """Returns a list of problems with the emitted JSON-LD (empty if
    fine). Reporting only — never raises, never blocks a page render."""
    try:
        _ProductJsonLdModel(
            name=doc.get("name"),
            description=doc.get("description"),
            image=doc.get("image"),
            sku=doc.get("sku"),
        )
        return []
    except ValidationError as exc:
        return [f"{e.get('loc', ('?',))[0]}: {e.get('type', 'invalid')}" for e in exc.errors()]
    except Exception as exc:  # noqa: BLE001
        return [f"validation could not run: {exc}"]


def _safe_jsonld(doc: dict[str, Any]) -> str:
    """Serializes JSON-LD for embedding inside a <script> element.

    A raw json.dumps is NOT safe here: catalog text is seller-supplied,
    and a product name containing "</script>" terminates the script
    element during HTML parsing, letting arbitrary markup escape into
    the page. HTML-escaping the block instead would break the JSON
    parse for the agents this page exists to serve.

    The standard mitigation is to emit `<`, `>` and `&` as JSON unicode
    escapes. `\\u003c` is valid JSON for "<" — a parser reads it back
    identically — but an HTML tokenizer never sees a tag delimiter, so
    the block can't be broken out of.
    """
    return (
        json.dumps(doc, indent=2, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{meta_description}">
<link rel="canonical" href="{page_url}">
<meta property="og:type" content="product">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{meta_description}">
{og_image}
<script type="application/ld+json">
{jsonld}
</script>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 42rem;
          margin: 2rem auto; padding: 0 1rem; line-height: 1.5; color: #1a1a1a; }}
  img {{ max-width: 100%; height: auto; border-radius: 8px; }}
  .price {{ font-size: 1.5rem; font-weight: 600; }}
  .meta {{ color: #666; font-size: 0.875rem; }}
  dl {{ display: grid; grid-template-columns: auto 1fr; gap: 0.25rem 1rem; }}
  dt {{ font-weight: 600; }}
  dd {{ margin: 0; }}
</style>
</head>
<body>
<main>
  <h1>{title}</h1>
  {image_tag}
  <p class="price">{price_display}</p>
  <p>{description}</p>
  {attributes_block}
  <p class="meta">SKU: {sku}{brand_display}</p>
</main>
</body>
</html>
"""


def render_product_page(product: dict[str, Any], page_url: str | None = None) -> str:
    """Renders the canonical product page. Escapes every interpolated
    value — catalog text is seller-supplied and reaches this function
    unfiltered, so it's untrusted input for HTML purposes."""
    sku = str(product.get("sku") or "")
    page_url = page_url or f"{public_base_url()}/p/{sku}"

    name = str(product.get("name") or "Product")
    description = str(product.get("description") or "")
    brand = product.get("brand")
    image_url = product.get("image_url")
    price = product.get("price")
    currency = product.get("currency")

    jsonld = build_product_jsonld(product, page_url)

    # Never let a malformed price 500 the page. An ACP crawler hitting
    # `url` to verify price/stock consistency must get a valid page back
    # even when the underlying data is bad — a 500 looks like an outage
    # and can get the whole endpoint down-ranked, which is a far worse
    # outcome than one product rendering without a price.
    price_display = ""
    if price is not None:
        try:
            price_display = (
                f"{currency} {float(price):,.2f}" if currency else f"{float(price):,.2f}"
            )
        except (TypeError, ValueError):
            price_display = ""

    attributes = product.get("attributes") or {}
    attributes_block = ""
    if isinstance(attributes, dict) and attributes:
        rows = "".join(
            f"<dt>{html.escape(str(k))}</dt><dd>{html.escape(str(v))}</dd>"
            for k, v in attributes.items()
        )
        attributes_block = f"<dl>{rows}</dl>"

    return _PAGE_TEMPLATE.format(
        title=html.escape(name),
        meta_description=html.escape(description[:160]),
        page_url=html.escape(page_url, quote=True),
        og_image=(
            f'<meta property="og:image" content="{html.escape(str(image_url), quote=True)}">'
            if image_url else ""
        ),
        jsonld=_safe_jsonld(jsonld),
        image_tag=(
            f'<img src="{html.escape(str(image_url), quote=True)}" alt="{html.escape(name)}">'
            if image_url else ""
        ),
        price_display=html.escape(price_display),
        description=html.escape(description),
        attributes_block=attributes_block,
        sku=html.escape(sku),
        brand_display=f" · {html.escape(str(brand))}" if brand else "",
    )
    
    