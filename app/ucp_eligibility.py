"""
UCP eligibility for DISPLAY — mirrors acp_schema.py's derive_eligibility()
pattern, computed once per product at ingest time (main.py's /ingest,
right alongside the existing ACP eligibility computation) and stored on
the product document. Without this, a frontend eligibility column would
need a live UCP API call per table row just to show a badge — the wrong
shape for a products list, and the same reason ACP's acp_tier/
is_eligible_checkout are stored rather than recomputed on every page load.

Deliberately a thin wrapper, never a reimplementation, of checks that
already exist and are already tested elsewhere:
  - is_ucp_purchasable()         (ucp_mapping.py)  — availability gate
  - to_ucp_checkout_line_item()  (ucp_mapping.py)  — price/currency mappability
  - meets_readiness_threshold    (already stored on the product by
                                   readiness_score.py via main.py's
                                   existing /ingest flow)             — readiness gate
These are the EXACT three conditions ucp_session.py's checkout gate
enforces. Computing them here via the same functions, rather than
re-deriving equivalent logic, is what guarantees the number shown on
the Products page can never silently drift from what checkout actually
does — the same reuse principle already applied throughout this
project (storefront.checkout() for readiness, is_ucp_purchasable() for
availability itself).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ucp_mapping import is_ucp_purchasable, to_ucp_checkout_line_item, UCPMappingError


@dataclass
class UCPEligibilityResult:
    is_ucp_catalog_listable: bool
    is_ucp_checkout_eligible: bool
    ucp_tier: str  # "checkout_ready" | "catalog_only" | "not_ucp_ready"
    ucp_blockers: list[str] = field(default_factory=list)


def derive_ucp_eligibility(structured: dict[str, Any]) -> UCPEligibilityResult:
    """
    is_ucp_catalog_listable: can this appear in /catalog/search,
        /catalog/lookup, /catalog/product at all? Only needs a valid
        UCP Price (price + a real ISO currency) — see
        ucp_mapping.to_ucp_checkout_line_item's own validation, reused
        here rather than duplicated.
    is_ucp_checkout_eligible: can this actually be checked out? Needs
        ALL THREE gates ucp_session.py enforces — mappable, in stock,
        and above the readiness threshold.

    Reads meets_readiness_threshold as an ALREADY-COMPUTED field rather
    than recomputing the score — that field is written by main.py's
    /ingest step (compute_readiness_score) before this function is
    ever called, avoiding a second, possibly-inconsistent readiness
    computation living in two places.
    """
    blockers: list[str] = []

    try:
        to_ucp_checkout_line_item(structured)
        mappable = True
    except UCPMappingError as exc:
        mappable = False
        blockers.append(str(exc))

    purchasable = is_ucp_purchasable(structured)
    if not purchasable:
        blockers.append(f"availability is {structured.get('availability')!r}, not in_stock")

    meets_threshold = bool(structured.get("meets_readiness_threshold"))
    if not meets_threshold:
        blockers.append("below the readiness threshold")

    is_ucp_catalog_listable = mappable
    is_ucp_checkout_eligible = mappable and purchasable and meets_threshold

    if is_ucp_checkout_eligible:
        tier = "checkout_ready"
    elif is_ucp_catalog_listable:
        tier = "catalog_only"
    else:
        tier = "not_ucp_ready"

    return UCPEligibilityResult(
        is_ucp_catalog_listable=is_ucp_catalog_listable,
        is_ucp_checkout_eligible=is_ucp_checkout_eligible,
        ucp_tier=tier,
        ucp_blockers=blockers,
    )
    
    