"""
ONDC eligibility for DISPLAY — mirrors ucp_eligibility.py's exact
pattern: computed once per product at ingest time (main.py's /ingest,
alongside the existing ACP and UCP eligibility computation) and stored
on the product document, so a frontend badge never needs a live call
just to render.

Deliberately a thin wrapper around ondc_mapping.to_ondc_catalog_item,
never a reimplementation — same "one canonical mapper, reused
everywhere" principle UCP/ACP eligibility already follow.

Genuinely simpler than UCP's two-tier split (catalog_only vs
checkout_ready): ONDC has no equivalent middle tier. A product that
fails ondc_mapping's checks can't validly appear in a real on_search
response at all — there's no "listed but not transactable" state the
way UCP allows a product with a valid price but no stock to still be
catalog-listable. So this is a single boolean, not a tier, because a
tier would imply a distinction ONDC's own schema doesn't actually have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ondc_mapping import ONDCMappingError, guess_ondc_domain, to_ondc_catalog_item


@dataclass
class ONDCEligibilityResult:
    is_ondc_listable: bool
    ondc_blockers: list[str] = field(default_factory=list)
    ondc_domain: str | None = None


def derive_ondc_eligibility(structured: dict[str, Any], seller_profile: Any) -> ONDCEligibilityResult:
    """Never raises — a mapping failure is a normal, expected outcome
    (most products won't be ONDC-ready until packer/policy config
    exists), not an error condition.

    ondc_domain is resolved independently of whether the product is
    actually listable — even a blocked product can usefully show which
    domain it would list under once its gaps are fixed, same
    remediation-hint instinct as acp_schema's own conformance report.
    """
    domain, _is_food = guess_ondc_domain(structured.get("category"))
    try:
        to_ondc_catalog_item(structured, seller_profile)
        return ONDCEligibilityResult(is_ondc_listable=True, ondc_domain=domain)
    except ONDCMappingError as exc:
        return ONDCEligibilityResult(
            is_ondc_listable=False, ondc_blockers=list(exc.reasons), ondc_domain=domain
        )
        
        