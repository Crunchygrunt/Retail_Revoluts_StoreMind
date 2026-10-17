"""Tests for reflexion.py — the loop's retry/stop decisions and best-
attempt selection, driven by a fake attempt_fn so no real agent, store,
or Gemini call is needed. attempt_fn here simulates: attempt 1 under-
delivers keywords (source-rich row), attempt 2 (after seeing the
reflection note) fixes it."""

from reflexion import AttemptOutcome, run_reflexive_structuring

RICH_ROW = {"name": "Handwoven Cotton Kurta", "description": "Breathable handwoven cotton kurta, block-printed, 250g, packed 09/2026."}
THIN_ROW = {"name": "Item", "description": ""}


def _base_outcome(**overrides):
    defaults = dict(
        success=True, structured={"description": "x" * 30, "keywords": ["a"], "attributes": {"m": "1"}},
        extraction_method="strict_json", warnings=[], raw_model_output="{}",
        readiness_score=60.0, meets_readiness_threshold=False, readiness_notes=[],
        acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
    )
    defaults.update(overrides)
    return AttemptOutcome(**defaults)


def test_passes_on_first_attempt_when_nothing_fixable_and_above_threshold():
    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        return _base_outcome(readiness_score=85.0, meets_readiness_threshold=True, structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}})

    result = run_reflexive_structuring(cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R1", attempt_fn=attempt_fn)
    assert result.stopped_reason == "passed"
    assert len(result.attempts) == 1
    assert result.retried is False


def test_retries_and_improves_when_source_supports_it():
    calls = []

    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        calls.append(reflection_note)
        if reflection_note is None:
            # attempt 1: thin keywords despite a rich source
            return _base_outcome(readiness_score=55.0, meets_readiness_threshold=False)
        # attempt 2: reflection acted on
        return _base_outcome(
            readiness_score=88.0, meets_readiness_threshold=True,
            structured={"description": "x" * 30, "keywords": ["a", "b", "c", "d"], "attributes": {"m": "1"}},
        )

    result = run_reflexive_structuring(cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R2", attempt_fn=attempt_fn)
    assert result.stopped_reason == "passed"
    assert result.retried is True
    assert result.improved is True
    assert len(result.attempts) == 2
    assert calls[0] is None
    assert calls[1] is not None and "keywords" in calls[1].lower()
    assert result.best.outcome.readiness_score == 88.0


def test_never_retries_when_source_is_the_limit():
    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        return _base_outcome(
            readiness_score=20.0, meets_readiness_threshold=False,
            structured={"description": "", "keywords": [], "attributes": {}},
        )

    result = run_reflexive_structuring(cleaned_row=THIN_ROW, raw_row=THIN_ROW, row_id="R3", attempt_fn=attempt_fn)
    assert result.stopped_reason == "source_limited"
    assert result.retried is False
    assert len(result.attempts) == 1


def test_stops_at_max_retries_even_if_still_model_fixable():
    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        # always thin keywords, source is rich enough to keep looking
        # model-fixable forever (a stubborn model, not a source limit)
        return _base_outcome(readiness_score=50.0, meets_readiness_threshold=False)

    result = run_reflexive_structuring(cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R4", attempt_fn=attempt_fn, max_retries=2)
    assert result.stopped_reason == "max_retries"
    assert len(result.attempts) == 3  # 1 initial + 2 retries
    assert result.retried is True


def test_best_attempt_selected_even_when_later_attempt_is_worse():
    scores = iter([70.0, 55.0, 65.0])

    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        return _base_outcome(readiness_score=next(scores), meets_readiness_threshold=False)

    result = run_reflexive_structuring(cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R5", attempt_fn=attempt_fn, max_retries=2)
    assert result.best.outcome.readiness_score == 70.0
    assert result.best.attempt == 1


def test_failed_structuring_stops_immediately():
    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        return _base_outcome(success=False, structured=None, extraction_method="failed")

    result = run_reflexive_structuring(cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R6", attempt_fn=attempt_fn)
    assert result.stopped_reason == "failed_structuring"
    assert len(result.attempts) == 1


def test_grounding_rejected_fields_surface_in_fabrication_blocked():
    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        rejected = ["manufacture_date"] if reflection_note else []
        return _base_outcome(
            readiness_score=88.0, meets_readiness_threshold=True,
            grounding_rejected_fields=rejected,
        )

    def attempt_fn_two_pass(cleaned_row, row_id, reflection_note, previous_structured):
        if reflection_note is None:
            return _base_outcome(readiness_score=50.0, meets_readiness_threshold=False)
        return _base_outcome(
            readiness_score=88.0, meets_readiness_threshold=True,
            grounding_rejected_fields=["manufacture_date"],
        )

    result = run_reflexive_structuring(cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R7", attempt_fn=attempt_fn_two_pass)
    assert "manufacture_date" in result.fabrication_blocked


def test_on_attempt_callback_fires_once_per_attempt():
    seen = []

    def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured):
        if reflection_note is None:
            return _base_outcome(readiness_score=50.0, meets_readiness_threshold=False)
        return _base_outcome(
            readiness_score=88.0, meets_readiness_threshold=True,
            structured={"description": "x" * 30, "keywords": ["a", "b", "c"], "attributes": {"m": "1"}},
        )

    run_reflexive_structuring(
        cleaned_row=RICH_ROW, raw_row=RICH_ROW, row_id="R8", attempt_fn=attempt_fn,
        on_attempt=lambda record: seen.append(record.attempt),
    )
    assert seen == [1, 2]
    
    