"""
Brand resolution — cascade, not a single guess.

ACP/UCP require `brand` on every product, but most SEA/India SME sellers
never state one explicitly. Rather than blocking checkout on an absence,
brand is resolved through a cascade of increasingly-inferred sources,
each one only reached if every prior stage found nothing — and each
result is tagged with exactly which stage produced it (`brand_source`),
so an inferred or fallback brand is never silently indistinguishable
from one the seller actually stated. This mirrors currency_inference.py's
majority-vote design (same confidence/min-sample gating, same
once-per-batch computation, same store-agnostic `store` parameter) —
a proven pattern, second application, not a new one.

Cascade, in order:
    1. source     — raw_row already has a "brand" field. Zero inference,
                     zero risk. Resolved in catalog_agent.py (and its
                     aistudio/mock siblings), not here.
    2. vision      — Gemini reads a brand off the source product image,
                     IF one exists and no explicit brand was given.
                     Also resolved in catalog_agent.py, since it needs
                     the agent's own Gemini client — this module only
                     resolves the two NON-visual stages below, and is
                     deliberately callable with zero LLM/network
                     dependency so it stays fully unit-testable.
    3. inferred    — majority-vote over the store's already-resolved
                     brands (source or vision only — see the filter in
                     _vote), confidence-thresholded. A genuinely
                     multi-brand catalog won't clear the confidence bar,
                     so it correctly falls through to (4) rather than
                     wrongly stamping one brand on every product.
    4. seller_name — the seller's OWN store/business name, from
                     seller_profile.py.

CORRECTION (see seller_profile.py for the full reasoning): stage 4 used
to emit the literal string "Unbranded". That directly violates the ACP
spec, which states "Use a real brand and seller name, not placeholders"
and "Do not use placeholder strings such as `null`, `unknown`, or
`n/a`". A generic category word is exactly what those rules reject, and
it would be down-ranked or refused at feed ingestion. The seller's own
store name is a real, specific, verifiable string — and for
private-label, handmade and long-tail sellers the seller genuinely IS
the brand, so it is also more accurate, not merely more compliant.

If no seller name is configured, brand is left UNRESOLVED rather than
filled with anything invented. An unresolved required field is an
honest, reportable gap with a specific fix; a fabricated one is a
silent lie in a feed.

Store-agnostic: both inference functions take the store module as an
explicit parameter, matching storefront.py's and currency_inference.py's
existing convention — works identically against local_store.py or
firestore_client.py with zero code difference.

CATEGORY SCOPING (added after the original store-wide design shipped —
this was the exact "still undecided" open question from the project's
own early handoff notes): infer_brand's original vote runs over the
WHOLE store regardless of category. A 90-kurta/10-kitchenware catalog
would let a confident Fabindia majority among the kurtas silently
stamp Fabindia onto the unbranded mugs too — the vote was never wrong,
it was just answering the wrong question (store-wide, when it should
be "what's the majority brand among comparable products").

infer_brand ITSELF IS UNCHANGED — not touched, not deprecated, kept for
exact backward compatibility with its own existing test suite, and
because a genuinely single-category catalog gets an identical answer
either way. infer_brand_by_category is new, additive code: same voting
logic (now shared via the _vote helper, so there's one implementation,
not two that could quietly drift apart), grouped by category first. The
call site that actually matters — main.py's /ingest loop — now uses the
category-scoped version; see that file's own comment at the call site.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

DEFAULT_MIN_CONFIDENCE = 0.8
DEFAULT_MIN_SAMPLE_SIZE = 5

# Only these two brand_source values count as a real vote for the
# majority-vote stage below — an already-inferred or already-fallback
# brand on one product must never get counted as evidence for another
# product's inference, or the fallback would compound on itself across
# batches.
_TRUSTED_BRAND_SOURCES = ("source", "vision")


@dataclass
class BrandInferenceResult:
    brand: str | None
    confidence: float
    sample_size: int
    applicable: bool  # True if this result is confident enough to apply


def _vote(brands: list[str], min_confidence: float, min_sample_size: int) -> BrandInferenceResult:
    """The actual majority-vote arithmetic, shared by infer_brand
    (store-wide) and infer_brand_by_category (per-category) below —
    one implementation, not two that could drift out of sync with each
    other over time."""
    sample_size = len(brands)
    if sample_size < min_sample_size:
        return BrandInferenceResult(brand=None, confidence=0.0, sample_size=sample_size, applicable=False)

    counts = Counter(brands)
    top_brand, top_count = counts.most_common(1)[0]
    confidence = top_count / sample_size
    applicable = confidence >= min_confidence
    return BrandInferenceResult(
        brand=top_brand if applicable else None,
        confidence=confidence,
        sample_size=sample_size,
        applicable=applicable,
    )


def infer_brand(
    store,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE,
) -> BrandInferenceResult:
    """Majority-vote over the store's already-resolved (source/vision)
    brands, STORE-WIDE — not category-scoped. Computed ONCE per ingest
    batch (see main.py) — same reasoning as currency_inference.infer_currency:
    a per-row store query would be wasteful, and different rows in the
    same batch could get different answers if the store changed
    mid-batch.

    UNCHANGED from before category scoping existed — main.py's /ingest
    flow now calls infer_brand_by_category instead (see that function
    and this module's docstring for why), but this is kept exactly as
    it was, not removed, both for its own existing test coverage and
    because it's still a legitimate, correct answer to a different
    question ("what's the majority brand across the whole store") than
    the one /ingest actually needs answered per-row.
    """
    products = store.list_products(limit=10_000)
    brands = [
        str(p.get("brand")).strip()
        for p in products
        if p.get("brand")
        and str(p.get("brand")).strip()
        and p.get("brand_source") in _TRUSTED_BRAND_SOURCES
    ]
    return _vote(brands, min_confidence, min_sample_size)


def infer_brand_by_category(
    store,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE,
) -> dict[str, BrandInferenceResult]:
    """Same majority-vote logic as infer_brand, but computed SEPARATELY
    per category — see module docstring for why this exists. One store
    query for the whole batch (same once-per-batch principle as
    infer_brand/infer_currency), grouped in memory, voted per group —
    not one store query per category, which would repeat the same cost
    infer_brand already avoids by computing once, not per row.

    A category with zero trusted-source (source/vision) brands anywhere
    in the store simply isn't a key in the returned dict — the caller
    (main.py) is responsible for treating a missing key the same as an
    inapplicable result, exactly as it already would for a category
    that IS present but below min_sample_size.
    """
    products = store.list_products(limit=10_000)
    by_category: dict[str, list[str]] = {}
    for p in products:
        if not (
            p.get("brand")
            and str(p.get("brand")).strip()
            and p.get("brand_source") in _TRUSTED_BRAND_SOURCES
        ):
            continue
        category = str(p.get("category") or "").strip()
        by_category.setdefault(category, []).append(str(p["brand"]).strip())

    return {
        category: _vote(brands, min_confidence, min_sample_size)
        for category, brands in by_category.items()
    }


def resolve_brand(
    structured: dict[str, Any],
    inferred_result: BrandInferenceResult,
    seller_name: str | None = None,
) -> None:
    """Applies the two NON-visual cascade stages (inferred, fallback) to
    an already-structured row, in place. Call AFTER the catalog agent
    has already tried stages 1-2 (structured["brand"] /
    structured["brand_source"] already reflect those, if either
    succeeded). Pure — no store access, no network — so it's safe to
    call once per row without repeating infer_brand's store query
    (matches apply_inferred_currency's split for the identical reason:
    one store query per batch, not per row).

    UNCHANGED — still takes a single BrandInferenceResult, exactly as
    before. Category-scoping lives entirely upstream, at the call site
    in main.py's /ingest loop, which now looks up the right per-
    category result before calling this — this function has no idea
    whether the result it was handed came from infer_brand or
    infer_brand_by_category, and doesn't need to.
    """
    if structured.get("brand"):
        # Stage 1 or 2 already resolved it in the catalog agent —
        # nothing to do here. setdefault as a safety net only; the
        # catalog agent always sets brand_source alongside brand.
        structured.setdefault("brand_source", "source")
        return

    if inferred_result.applicable and inferred_result.brand:
        structured["brand"] = inferred_result.brand
        structured["brand_source"] = "inferred"
        structured["brand_inference_confidence"] = round(inferred_result.confidence, 2)
        return

    # Stage 4: the seller's own store name. Never a placeholder string —
    # see the module docstring and seller_profile.py.
    if seller_name and str(seller_name).strip():
        structured["brand"] = str(seller_name).strip()
        structured["brand_source"] = "seller_name"
        return

    # Nothing safe to use. Leave `brand` unset so check_acp_conformance
    # reports it as a real gap with a real fix, rather than passing a
    # fabricated value into a feed.
    structured["brand_source"] = "unresolved"
    
    
    