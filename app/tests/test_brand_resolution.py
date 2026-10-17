"""
Tests for brand_resolution.py — the store-level brand cascade (stages
3-4: majority-vote inference, "Unbranded" fallback). Stages 1-2
(explicit source, vision) are covered in test_catalog_agent_acp_repair.py
since they live inside the catalog agent, not here.
"""

from __future__ import annotations

import pytest

from brand_resolution import (
    BrandInferenceResult,
    infer_brand,
    resolve_brand,
)


class FakeStore:
    """Minimal store stand-in — infer_brand only calls list_products()."""

    def __init__(self, products: list[dict]) -> None:
        self._products = products

    def list_products(self, limit: int = 10_000) -> list[dict]:
        return self._products


def make_products(brand: str, source: str, count: int) -> list[dict]:
    return [{"brand": brand, "brand_source": source} for _ in range(count)]


# ---------------------------------------------------------------------
# resolve_brand
# ---------------------------------------------------------------------

def test_resolve_brand_leaves_already_resolved_brand_untouched():
    """Stage 1/2 already ran in the catalog agent — resolve_brand must
    not override a brand that's already present."""
    structured = {"name": "Kurta", "brand": "Fabindia", "brand_source": "source"}
    resolve_brand(structured, BrandInferenceResult(brand=None, confidence=0.0, sample_size=0, applicable=False))
    assert structured["brand"] == "Fabindia"
    assert structured["brand_source"] == "source"


def test_resolve_brand_falls_back_to_the_sellers_own_store_name():
    """Corrected behaviour. The old fallback emitted the literal string
    "Unbranded", which the ACP spec rejects as a placeholder. A seller's
    own store name is real, specific and verifiable — and for
    private-label sellers it's also more accurate."""
    structured = {"name": "Loose sugar 1kg"}
    resolve_brand(
        structured,
        BrandInferenceResult(brand=None, confidence=0.0, sample_size=2, applicable=False),
        seller_name="Kurta House",
    )
    assert structured["brand"] == "Kurta House"
    assert structured["brand_source"] == "seller_name"


def test_resolve_brand_never_emits_a_placeholder_string():
    """Regression guard for the spec violation this replaced."""
    from acp_schema import is_placeholder

    structured = {"name": "Loose sugar 1kg"}
    resolve_brand(
        structured,
        BrandInferenceResult(brand=None, confidence=0.0, sample_size=0, applicable=False),
        seller_name=None,
    )
    assert not is_placeholder(structured.get("brand", ""), "brand")


def test_brand_is_left_unresolved_when_there_is_no_seller_name():
    """With nothing safe to use, an honest reportable gap beats a
    fabricated value in a feed."""
    structured = {"name": "Loose sugar 1kg"}
    resolve_brand(
        structured,
        BrandInferenceResult(brand=None, confidence=0.0, sample_size=0, applicable=False),
        seller_name=None,
    )
    assert "brand" not in structured
    assert structured["brand_source"] == "unresolved"


def test_resolve_brand_applies_confident_inference_and_tags_confidence():
    structured = {"name": "Loose sugar 1kg"}
    resolve_brand(structured, BrandInferenceResult(brand="Fabindia", confidence=0.9, sample_size=8, applicable=True))
    assert structured["brand"] == "Fabindia"
    assert structured["brand_source"] == "inferred"
    assert structured["brand_inference_confidence"] == 0.9


def test_resolve_brand_ignores_a_non_applicable_inference_result():
    """applicable=False must mean "not confident enough to use" even if
    a brand string happens to be present on the result — the seller-name
    fallback wins instead."""
    structured = {"name": "Loose sugar 1kg"}
    resolve_brand(
        structured,
        BrandInferenceResult(brand="Fabindia", confidence=0.4, sample_size=8, applicable=False),
        seller_name="Kurta House",
    )
    assert structured["brand"] == "Kurta House"
    assert structured["brand_source"] == "seller_name"


# ---------------------------------------------------------------------
# infer_brand
# ---------------------------------------------------------------------

def test_infer_brand_refuses_below_minimum_sample_size():
    store = FakeStore(make_products("Fabindia", "source", 3))
    result = infer_brand(store, min_sample_size=5)
    assert result.applicable is False
    assert result.sample_size == 3
    assert result.brand is None


def test_infer_brand_picks_a_confident_majority():
    store = FakeStore(make_products("Fabindia", "source", 8) + make_products("Local Weaves", "vision", 2))
    result = infer_brand(store, min_confidence=0.8, min_sample_size=5)
    assert result.applicable is True
    assert result.brand == "Fabindia"
    assert result.confidence == pytest.approx(0.8)


def test_infer_brand_stays_inconclusive_for_a_genuinely_multi_brand_catalog():
    """A real multi-brand reseller must NOT get one brand wrongly
    stamped across their whole catalog — confidence should fall below
    the threshold and applicable should be False."""
    store = FakeStore(
        make_products("Fabindia", "source", 3)
        + make_products("Local Weaves", "vision", 3)
        + make_products("Handloom Co", "source", 3)
    )
    result = infer_brand(store, min_confidence=0.8, min_sample_size=5)
    assert result.applicable is False


def test_infer_brand_excludes_inferred_and_fallback_brands_from_the_vote():
    """An already-inferred or already-Unbranded product must never count
    as evidence for another product's inference — otherwise a fallback
    could compound on itself across batches."""
    store = FakeStore(
        make_products("Fabindia", "source", 4)
        + make_products("Kurta House", "seller_name", 4)
        + make_products("Guessed Co", "inferred", 4)
    )
    result = infer_brand(store)
    assert result.sample_size == 4  # only the 4 "source" products count


def test_infer_brand_respects_custom_confidence_threshold():
    store = FakeStore(make_products("Fabindia", "source", 6) + make_products("Local Weaves", "vision", 4))
    # 60% majority: applicable at a lower bar, not at the default 0.8
    lenient = infer_brand(store, min_confidence=0.5, min_sample_size=5)
    strict = infer_brand(store, min_confidence=0.8, min_sample_size=5)
    assert lenient.applicable is True and lenient.brand == "Fabindia"
    assert strict.applicable is False
    