"""
Maps a StoreMind structured product + seller config to an ONDC retail
on_search catalog item (ONDC:RET10-16 — standard single-seller retail,
NOT ONDC:RETINVL's multi-seller price-discovery model, which is a
genuinely different business model this project doesn't target).

Field requirements confirmed from real ONDC certification feedback
(v1.2.0-logs conformance issues, not guessed):
  - @ondc/org/statutory_reqs_packaged_commodities requires
    manufacturer_or_packer_name, manufacturer_or_packer_address,
    common_or_generic_name_of_commodity,
    net_quantity_or_measure_of_commodity_in_pkg,
    month_year_of_manufacture_packing_import — confirmed mandatory,
    with manufacturer_or_packer_address specifically needing "at least
    3 alphabets/characters".
  - Either statutory_reqs_packaged_commodities OR
    statutory_reqs_prepackaged_food is required depending on
    category_id — this module builds packaged_commodities ONLY.
    prepackaged_food needs FSSAI license numbers (other_FSSAI_license_no
    / importer_FSSAI_license_no per real conformance feedback), a real
    government food-safety registration StoreMind has no way to
    validate or even collect today — NOT built, deliberately, rather
    than faked. A food-category product maps to ONDCMappingError with
    an honest "needs prepackaged_food support, not yet built" reason.
  - quantity.maximum.count: a real conformance thread ("available count
    must be either 99 or 0 only") shows ONDC's own ecosystem uses a
    binary available/not-available convention for non-serialized retail
    goods, not a real inventory count — this module follows that
    confirmed convention (99 = available, 0 = not) rather than
    fabricating an inventory number StoreMind doesn't track.

KNOWN GAP, not silently papered over: category_id here is StoreMind's
own free-text category string, NOT validated against ONDC's real
per-domain category taxonomy (e.g. RET12 Fashion's actual category
codes) — that taxonomy wasn't available to build against. Flagged in
ONDCMappingError's reasons whenever domain mapping itself is uncertain,
so this is visible rather than assumed correct.

Unlike ucp_mapping.py's UCPMappingError (one reason at a time),
ONDCMappingError collects ALL applicable reasons together — ONDC has
enough independent prerequisites (domain, statutory fields, seller
policy config) that reporting them one at a time would mean several
rounds of "fix one thing, discover the next" instead of one complete
gap report.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# Confirmed live domains only (Grocery through Pharma) — RET17-19 exist
# in the log-validation-utility's domain list but no confirmed public
# label/scope was found for them, so they're deliberately left out
# rather than guessed at.
CATEGORY_TO_ONDC_DOMAIN: dict[str, str] = {
    "grocery": "ONDC:RET10",
    "food": "ONDC:RET11",
    "beverage": "ONDC:RET11",
    "f&b": "ONDC:RET11",
    "fashion": "ONDC:RET12",
    "apparel": "ONDC:RET12",
    "clothing": "ONDC:RET12",
    "footwear": "ONDC:RET12",
    "kurta": "ONDC:RET12",
    "beauty": "ONDC:RET13",
    "personal care": "ONDC:RET13",
    "cosmetic": "ONDC:RET13",
    "electronics": "ONDC:RET14",
    "gadget": "ONDC:RET14",
    "home": "ONDC:RET15",
    "decor": "ONDC:RET15",
    "kitchen": "ONDC:RET15",
    "furniture": "ONDC:RET15",
    "pharma": "ONDC:RET16",
    "medicine": "ONDC:RET16",
    "health": "ONDC:RET16",
}

# Categories this mapper explicitly refuses rather than mismaps — food
# needs statutory_reqs_prepackaged_food (FSSAI), not built (see module
# docstring). Spices/seasoning is the one case in this project's own
# demo catalog that would otherwise silently mismap to RET10 Grocery
# when it's really an F&B-adjacent packaged-food item.
_FOOD_KEYWORDS = ("spice", "seasoning", "snack", "sweet", "confection")


def guess_ondc_domain(category: str | None) -> tuple[str | None, bool]:
    """Returns (domain_or_None, is_food). Keyword match on StoreMind's
    own free-text category string — never a confident guess when
    nothing matches; an unmapped category is a reported gap, not a
    silent default to some domain.

    Public (not module-private) because ondc_eligibility.py also calls
    this directly for DISPLAY — a product blocked for some other reason
    (missing packer info, say) can still usefully show which ONDC
    domain it would list under once fixed, the same "show the one fix
    that would close it" instinct acp_schema's remediation hints
    already use. Single source of truth either way: to_ondc_catalog_item
    below calls this exact function too, not a second copy of the logic.
    """
    text = str(category or "").strip().lower()
    if not text:
        return None, False
    if any(kw in text for kw in _FOOD_KEYWORDS):
        return "ONDC:RET11", True
    for keyword, domain in CATEGORY_TO_ONDC_DOMAIN.items():
        if keyword in text:
            return domain, False
    return None, False


# Attribute keys checked, in priority order, for net quantity — Gemini's
# structuring prompt has no dedicated field for this, so it's read from
# whatever attribute key the model happened to use. Absence is a real,
# reported gap, never a fabricated "1 unit".
_NET_QUANTITY_ATTRIBUTE_KEYS = (
    "net_quantity", "quantity", "weight", "net_weight", "pack_size", "size", "volume",
)

# Same reasoning, for the manufacture/packing date statutory field —
# also not part of the current structuring prompt, so almost always
# absent today. Checked anyway rather than skipped outright, in case a
# seller's own catalog data happens to include it under one of these
# keys.
_MANUFACTURE_DATE_ATTRIBUTE_KEYS = (
    "month_year_of_manufacture", "manufacture_date", "manufacturing_date",
    "mfg_date", "packing_date", "manufactured",
)


class ONDCMappingError(ValueError):
    """Raised with ALL applicable reasons at once (see module
    docstring for why, unlike ucp_mapping.UCPMappingError's one-at-a-
    time shape). Callers must catch this per-product, same "one bad
    row never kills the batch" instinct used throughout this project."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def _build_statutory_reqs(
    structured: dict[str, Any], seller_profile: Any, reasons: list[str]
) -> dict[str, str] | None:
    """Builds @ondc/org/statutory_reqs_packaged_commodities. Appends to
    `reasons` (rather than raising directly) for every missing required
    field, so the caller gets a complete picture in one pass."""
    packer_name = seller_profile.resolved_packer_name
    packer_address = seller_profile.packer_address

    # Prefer catalog_agent.py's own named, optional, anti-fabrication-
    # guarded "net_quantity"/"manufacture_date" fields (added specifically
    # for this gate — see that module's STRUCTURING_PROMPT comment) over
    # the attribute-dict scan below. The scan stays as a fallback, not
    # dead code: it's still the only path for anything ingested BEFORE
    # that prompt change, or a seller's own catalog data that happens to
    # carry these under one of the checked keys already.
    net_quantity = structured.get("net_quantity") or None
    manufacture_date = structured.get("manufacture_date") or None

    attributes = structured.get("attributes") or {}
    if isinstance(attributes, dict):
        if not net_quantity:
            for key in _NET_QUANTITY_ATTRIBUTE_KEYS:
                if attributes.get(key):
                    net_quantity = str(attributes[key])
                    break
        if not manufacture_date:
            for key in _MANUFACTURE_DATE_ATTRIBUTE_KEYS:
                if attributes.get(key):
                    manufacture_date = str(attributes[key])
                    break

    if not packer_name:
        reasons.append(
            "no packer/manufacturer name — configure STOREMIND_SELLER_PACKER_NAME "
            "(for a reseller of branded goods) or STOREMIND_SELLER_IS_PACKER=true "
            "(if the seller packs their own goods)"
        )
    if not packer_address:
        reasons.append("no packer/manufacturer address configured (STOREMIND_SELLER_PACKER_ADDRESS)")
    elif sum(c.isalpha() for c in packer_address) < 3:
        # Confirmed real ONDC validation rule, not invented.
        reasons.append("packer address needs at least 3 alphabetic characters")
    if not net_quantity:
        reasons.append(
            f"no net quantity/measure found in product attributes "
            f"(checked: {', '.join(_NET_QUANTITY_ATTRIBUTE_KEYS)})"
        )
    if not manufacture_date:
        # NOT given a placeholder — same standard as every other field
        # here. This is genuinely per-batch product data (when THIS
        # item was packed), not seller-wide config like packer_name,
        # so there's no honest fallback the way seller-as-packer is
        # for the name field; it's a real, currently-unfillable gap
        # unless the catalog structuring prompt starts asking for it.
        reasons.append(
            f"no manufacture/packing date found in product attributes "
            f"(checked: {', '.join(_MANUFACTURE_DATE_ATTRIBUTE_KEYS)})"
        )

    name = str(structured.get("name") or "").strip()
    if not packer_name or not packer_address or not net_quantity or not manufacture_date:
        return None

    return {
        "manufacturer_or_packer_name": packer_name,
        "manufacturer_or_packer_address": packer_address,
        "common_or_generic_name_of_commodity": name,
        "net_quantity_or_measure_of_commodity_in_pkg": net_quantity,
        "month_year_of_manufacture_packing_import": manufacture_date,
    }


def to_ondc_catalog_item(structured: dict[str, Any], seller_profile: Any) -> dict[str, Any]:
    """Maps a structured product to an ONDC on_search item. Raises
    ONDCMappingError (all applicable reasons at once) if it can't form
    a valid, honest item — never emits a partially-fabricated one."""
    reasons: list[str] = []

    domain, is_food = guess_ondc_domain(structured.get("category"))
    if domain is None:
        reasons.append(f"category {structured.get('category')!r} did not map to a known ONDC retail domain")
    elif is_food:
        reasons.append(
            "category maps to F&B/packaged food, which needs "
            "@ondc/org/statutory_reqs_prepackaged_food (FSSAI license) — "
            "not yet built, see module docstring"
        )

    price = structured.get("price")
    currency = structured.get("currency")
    price_value = None
    if price is None:
        reasons.append("no price")
    else:
        try:
            price_value = f"{Decimal(str(price)):.2f}"
        except (InvalidOperation, TypeError, ValueError):
            reasons.append(f"price {price!r} is not a valid number")
    if not currency:
        reasons.append("no currency")

    statutory_reqs = _build_statutory_reqs(structured, seller_profile, reasons)

    consumer_care = seller_profile.consumer_care_contact
    if not consumer_care:
        reasons.append(
            "no consumer care contact configured (all three of "
            "STOREMIND_SELLER_CONSUMER_CARE_NAME/_EMAIL/_PHONE required)"
        )

    if seller_profile.returnable is None:
        reasons.append("STOREMIND_SELLER_RETURNABLE not configured")
    elif seller_profile.returnable and seller_profile.return_window_days is None:
        reasons.append("returnable=true but STOREMIND_SELLER_RETURN_WINDOW_DAYS not configured")
    if seller_profile.cancellable is None:
        reasons.append("STOREMIND_SELLER_CANCELLABLE not configured")
    if seller_profile.time_to_ship_hours is None:
        reasons.append("STOREMIND_SELLER_TIME_TO_SHIP_HOURS not configured")

    if reasons:
        raise ONDCMappingError(reasons)

    sku = str(structured.get("sku") or "")
    name = str(structured.get("name") or "")
    image_url = structured.get("image_url")

    item: dict[str, Any] = {
        "id": sku,
        "descriptor": {
            "name": name,
            "short_desc": str(structured.get("description") or "")[:160],
            "long_desc": str(structured.get("description") or ""),
            **({"images": [str(image_url)]} if image_url else {}),
        },
        "quantity": {
            # See module docstring: 99/0 is ONDC's own confirmed
            # available/not-available convention for non-serialized
            # goods, not a fabricated real inventory count.
            "maximum": {"count": "99" if structured.get("availability") == "in_stock" else "0"},
        },
        "price": {"currency": str(currency).upper(), "value": price_value},
        "category_id": str(structured.get("category") or ""),
        "@ondc/org/returnable": bool(seller_profile.returnable),
        "@ondc/org/cancellable": bool(seller_profile.cancellable),
        "@ondc/org/time_to_ship": f"PT{seller_profile.time_to_ship_hours}H",
        "@ondc/org/contact_details_consumer_care": consumer_care,
        "@ondc/org/statutory_reqs_packaged_commodities": statutory_reqs,
    }
    if seller_profile.returnable:
        item["@ondc/org/return_window"] = f"P{seller_profile.return_window_days}D"
    return item
