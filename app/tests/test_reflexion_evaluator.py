"""Tests for reflexion_evaluator.py — gap classification, the grounding
guard, and the readiness-ceiling demotion. Style mirrors
test_ucp_eligibility.py: module-level fixture dicts, plain assertions,
one meta-test pinning classification against what would actually block
ingest, so the two can't silently drift apart."""

import pytest
from reflexion_evaluator import build_reflection_note, check_grounding, classify_attempt

RICH_ROW = {
    "name": "Handwoven Cotton Kurta",
    "description": "Breathable handwoven cotton kurta, net weight 250g, packed 09/2026.",
}
THIN_ROW = {"name": "Item", "description": ""}


def test_non_strict_json_is_model_fixable():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"material": "cotton"}},
        extraction_method="loose_text", acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
    )
    assert "non_strict_json" in r.fixable_tags


def test_acp_missing_brand_is_source_limited_never_retried():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=["brand"], acp_invalid_fields=[], ondc_blockers=[],
    )
    assert "acp_missing_brand" not in r.fixable_tags
    assert any(f.tag == "acp_missing_brand" for f in r.source_limited)


def test_acp_missing_description_is_model_fixable():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "", "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=["description"], acp_invalid_fields=[], ondc_blockers=[],
    )
    assert "acp_missing_description" in r.fixable_tags


def test_ondc_net_quantity_retried_when_source_has_it():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[],
        ondc_blockers=["no net quantity/measure found in product attributes (checked: net_quantity, ...)"],
    )
    assert "ondc_net_quantity" in r.fixable_tags


def test_ondc_net_quantity_source_limited_when_source_lacks_it():
    row_no_qty = {"name": "Handwoven Cotton Kurta", "description": "Breathable and stylish."}
    r = classify_attempt(
        raw_row=row_no_qty, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[],
        ondc_blockers=["no net quantity/measure found in product attributes (checked: net_quantity, ...)"],
    )
    assert "ondc_net_quantity" not in r.fixable_tags
    assert any(f.tag == "ondc_net_quantity" for f in r.source_limited)


def test_ondc_manufacture_date_retried_when_source_has_it():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[],
        ondc_blockers=["no manufacture/packing date found in product attributes (checked: ...)"],
    )
    assert "ondc_manufacture_date" in r.fixable_tags


def test_ondc_seller_config_gap_never_retried():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[],
        ondc_blockers=["no packer/manufacturer name — configure STOREMIND_SELLER_PACKER_NAME ..."],
    )
    assert r.fixable_tags == []
    assert any(f.tag == "ondc_seller_config" for f in r.source_limited)


def test_thin_keywords_retried_when_source_has_content():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
    )
    assert "thin_keywords" in r.fixable_tags


def test_thin_keywords_source_limited_when_source_is_thin():
    r = classify_attempt(
        raw_row=THIN_ROW, structured={"description": "", "keywords": ["a"], "attributes": {}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
    )
    assert "thin_keywords" not in r.fixable_tags
    assert "thin_attributes" not in r.fixable_tags


def test_ceiling_unreachable_demotes_readiness_only_findings():
    r_normal = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
        ceiling_unreachable=False,
    )
    assert "thin_keywords" in r_normal.fixable_tags

    r_ceiling = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
        ceiling_unreachable=True,
    )
    assert "thin_keywords" not in r_ceiling.fixable_tags
    assert any(f.tag == "thin_keywords" for f in r_ceiling.source_limited)


def test_ceiling_unreachable_does_not_suppress_ondc_findings():
    """A separate axis from readiness — still worth fixing even when
    readiness itself can never pass this batch."""
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[],
        ondc_blockers=["no net quantity/measure found in product attributes (checked: ...)"],
        ceiling_unreachable=True,
    )
    assert "ondc_net_quantity" in r.fixable_tags


def test_acp_finding_on_a_genuinely_thin_row_is_source_limited_not_looped():
    """Regression test for a real bug found only by running the A/B
    harness against the real repo: a too-short keywords list flagged
    by acp_schema.py as missing/invalid, on a row whose source has
    nothing more to give, must be classified source-limited on the
    FIRST attempt — not treated as model-fixable and reported
    identically on every retry until max_retries is exhausted. The
    local test double for acp_schema.py never exercised this because
    it doesn't implement a keyword-count check; the real one does."""
    r = classify_attempt(
        raw_row=THIN_ROW,
        structured={"description": "Item — a general item.", "keywords": ["item"], "attributes": {}},
        extraction_method="strict_json", acp_missing_fields=["keywords"], acp_invalid_fields=[], ondc_blockers=[],
    )
    assert "acp_missing_keywords" not in r.fixable_tags
    assert any(f.tag == "acp_missing_keywords" for f in r.source_limited)


def test_grounding_rejects_ungrounded_manufacture_date():
    before = {"manufacture_date": None}
    after = {"manufacture_date": "11/2027", "brand_source": "source"}
    rejected = check_grounding(before=before, after=after, raw_row=RICH_ROW)
    assert "manufacture_date" in rejected


def test_grounding_accepts_manufacture_date_present_in_source():
    before = {"manufacture_date": None}
    after = {"manufacture_date": "09/2026", "brand_source": "source"}
    rejected = check_grounding(before=before, after=after, raw_row=RICH_ROW)
    assert "manufacture_date" not in rejected


def test_grounding_never_checks_vision_sourced_brand():
    before = {"brand": None}
    after = {"brand": "Woodland", "brand_source": "vision"}
    rejected = check_grounding(before=before, after=after, raw_row=RICH_ROW)
    assert "brand" not in rejected


def test_grounding_rejects_ungrounded_source_brand():
    before = {"brand": None}
    after = {"brand": "Nike", "brand_source": "source"}
    rejected = check_grounding(before=before, after=after, raw_row=RICH_ROW)
    assert "brand" in rejected


def test_build_reflection_note_empty_when_nothing_fixable():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
    )
    assert build_reflection_note(r, 1) == ""


def test_build_reflection_note_lists_every_fixable_detail():
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "", "keywords": ["a"], "attributes": {}},
        extraction_method="loose_text", acp_missing_fields=["description"], acp_invalid_fields=[], ondc_blockers=[],
    )
    note = build_reflection_note(r, 2)
    assert "attempt 2" in note
    for finding in r.model_fixable:
        assert finding.detail in note


@pytest.mark.parametrize(
    "ondc_blockers,expect_fixable",
    [
        (["no net quantity/measure found in product attributes (checked: ...)"], True),
        (["no packer/manufacturer name — configure STOREMIND_SELLER_PACKER_NAME"], False),
    ],
)
def test_agrees_with_ondc_mapping_reason_shapes(ondc_blockers, expect_fixable):
    """Not a tautology: pins that classify_attempt's substring matching
    keeps working against ondc_mapping.py's REAL reason strings, not a
    paraphrase of them — guards against the two drifting apart if
    ondc_mapping's wording ever changes."""
    r = classify_attempt(
        raw_row=RICH_ROW, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        extraction_method="strict_json", acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=ondc_blockers,
    )
    assert bool(r.fixable_tags) == expect_fixable
    