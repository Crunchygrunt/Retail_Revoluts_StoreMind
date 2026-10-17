"""Tests for the memory-retrieval wiring added to reflexion.py's loop —
the gap found by direct questioning, not by running anything: memory
was being SAVED but nothing ever RETRIEVED it. Covers both call sites:
the pre-emptive one (before attempt 1, tags=[]) and the retry-time one
(real tags, once a gap is classified)."""

from reflexion import AttemptOutcome, run_reflexive_structuring

RICH_ROW = {
    "name": "Handwoven Cotton Kurta",
    "description": "Breathable handwoven cotton kurta, block-printed, 250g, packed 09/2026.",
    "category": "Apparel",
}
NO_CATEGORY_ROW = {"name": "Handwoven Cotton Kurta", "description": "Breathable handwoven cotton kurta, block-printed."}


def _base_outcome(**overrides):
    defaults = dict(
        success=True, structured={"description": "x" * 30, "keywords": ["a"], "attributes": {"m": "1"}, "category": "Apparel"},
        extraction_method="strict_json", warnings=[], raw_model_output="{}",
        readiness_score=60.0, meets_readiness_threshold=False, readiness_notes=[],
        acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
    )
    defaults.update(overrides)
    return AttemptOutcome(**defaults)


def test_preemptive_retrieval_seeds_the_first_attempts_reflection_note():
    seen_notes = []

    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        seen_notes.append(reflection_note)
        return _base_outcome(readiness_score=85.0, meets_readiness_threshold=True,
                              structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}, "category": "Apparel"})

    def retrieve_memory(category, tags):
        assert tags == []  # pre-emptive call must ask with no tags
        assert category == "Apparel"
        return ["net_quantity was previously missed on a similar kurta listing — check for a stated weight"]

    result = run_reflexive_structuring(
        cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R1", attempt_fn=attempt_fn, retrieve_memory=retrieve_memory,
    )
    assert result.stopped_reason == "passed"
    assert len(result.attempts) == 1
    assert seen_notes[0] is not None
    assert "CONTEXT from previously corrected products" in seen_notes[0]
    assert "your previous attempt" not in seen_notes[0].lower()  # never framed as self-critique
    assert "net_quantity was previously missed" in seen_notes[0]


def test_preemptive_retrieval_skipped_when_raw_row_has_no_category():
    called = []

    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        return _base_outcome(readiness_score=85.0, meets_readiness_threshold=True,
                              structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}})

    def retrieve_memory(category, tags):
        called.append((category, tags))
        return ["should never be seen"]

    result = run_reflexive_structuring(
        cleaned_row=NO_CATEGORY_ROW, raw_row=NO_CATEGORY_ROW, row_id="R2", attempt_fn=attempt_fn, retrieve_memory=retrieve_memory,
    )
    assert called == []  # never even called — no category to look up, no guessing one
    assert len(result.attempts) == 1


def test_retry_time_retrieval_uses_real_tags_and_enriches_the_note():
    retrieve_calls = []

    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        if reflection_note is None:
            return _base_outcome(readiness_score=55.0, meets_readiness_threshold=False)
        return _base_outcome(
            readiness_score=88.0, meets_readiness_threshold=True,
            structured={"description": "x" * 30, "keywords": ["a", "b", "c", "d"], "attributes": {"m": "1"}, "category": "Apparel"},
        )

    def retrieve_memory(category, tags):
        retrieve_calls.append((category, tags))
        if tags:  # retry-time call — real tags now exist
            return ["adding a size/material attribute usually needs a second look at the description"]
        return []  # pre-emptive call — nothing stored yet for this test

    result = run_reflexive_structuring(
        cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R3", attempt_fn=attempt_fn, retrieve_memory=retrieve_memory,
    )
    assert result.retried is True
    # first call was pre-emptive (tags=[]), second was retry-time (real tags)
    assert retrieve_calls[0][1] == []
    assert retrieve_calls[1][1] != []
    retry_note = result.attempts[1].reflection_note
    assert "Also relevant" in retry_note
    assert "adding a size/material attribute" in retry_note
    assert "REFLECTION on attempt" in retry_note  # this row's own diagnosis still comes first


def test_memory_failure_is_fail_open_and_never_breaks_the_loop():
    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        return _base_outcome(readiness_score=85.0, meets_readiness_threshold=True,
                              structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}, "category": "Apparel"})

    def broken_retrieve_memory(category, tags):
        raise RuntimeError("Firestore index missing")

    result = run_reflexive_structuring(
        cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R4", attempt_fn=attempt_fn, retrieve_memory=broken_retrieve_memory,
    )
    assert result.stopped_reason == "passed"  # never crashed, memory just contributed nothing


def test_no_retrieve_memory_given_behaves_exactly_as_before():
    """Backward-compatibility check: the default (None) must reproduce
    every existing test_reflexion.py assertion unchanged."""
    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        assert reflection_note is None  # no memory hook -> attempt 1 unseeded, exactly as before
        return _base_outcome(readiness_score=85.0, meets_readiness_threshold=True,
                              structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}})

    result = run_reflexive_structuring(cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R5", attempt_fn=attempt_fn)
    assert result.stopped_reason == "passed"
    