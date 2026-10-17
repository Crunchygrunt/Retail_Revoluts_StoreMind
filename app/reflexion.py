"""
Reflexion loop — orchestrates bounded self-correction around ONE row's
structuring attempt.

Deliberately NOT built on tenacity, despite length_repair.py's proven
retry_if_result pattern being the obvious reference. That pattern fits
a single function call retried until ONE return value satisfies ONE
condition. This loop is not that shape: each attempt produces a whole
bundle of evaluator output (readiness score, ACP gaps, ONDC blockers),
the decision to retry depends on CLASSIFYING that bundle
(reflexion_evaluator.classify_attempt), the retry itself needs
different input each time (a fresh reflection note), and the final
result has to be the BEST attempt seen, not just the last one. Forcing
that into tenacity's single-predicate retry callback would obscure the
logic more than a plain bounded loop does. Reusing tenacity where it
fits (length_repair.py) and not reusing it where it doesn't is the
right call, not an inconsistency.

Two things this module deliberately does NOT own, by design:
  - Structuring itself, and all the post-processing that turns a
    structured row into a scored, eligibility-checked product
    (currency/brand inference, ACP/UCP/ONDC eligibility, readiness
    scoring) — that logic already lives in main.py's /ingest loop and
    depends on batch-level and seller-level state (inferred currency,
    inferred brand, seller_profile, image_store) this module has no
    business holding. The caller supplies it as `attempt_fn`.
  - The grounding check itself (reflexion_evaluator.check_grounding)
    is also the CALLER's responsibility to run, inside attempt_fn,
    BEFORE it scores the attempt — not after, and not here. Scoring an
    attempt and only afterward stripping a fabricated field would
    score the fabricated version, which defeats the entire point of
    the check. attempt_fn receives the previous attempt's structured
    dict specifically so it can run the grounding check itself, in the
    right order, using the same post-processing pipeline that will
    score the result.

This keeps reflexion.py to what a control loop should be: attempt,
classify, decide, retry or stop, pick the best. Everything
StoreMind-specific about what an "attempt" even means stays where it
already lived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from reflexion_evaluator import (
    AttemptClassification,
    build_prior_context_note,
    build_reflection_note,
    classify_attempt,
)
from readiness_score import DEFAULT_THRESHOLD, source_richness_ceiling

DEFAULT_MAX_RETRIES = 2

# (category, tags) -> list of retrieved reflection note strings. Called
# with tags=[] once, pre-emptively, before attempt 1 (see
# run_reflexive_structuring) — ranked by recency, not gap overlap,
# since no gap exists to rank against yet. Called again with real tags
# on every retry, ranked by tag overlap. Always optional and always
# fail-open: a raising or missing implementation just means memory
# contributes nothing for that row, never breaks the loop.
RetrieveMemoryFn = Callable[[str, list[str]], list[str]]


@dataclass
class AttemptOutcome:
    """What the caller's attempt_fn returns for ONE try. Deliberately a
    flat bundle of already-computed evaluator output, not a re-derived
    summary — reflexion.py classifies and compares using exactly the
    same ACP/ONDC/readiness results main.py would otherwise write
    straight to the store, so nothing here can silently disagree with
    what the rest of the pipeline decided.
    """

    success: bool
    structured: dict[str, Any] | None
    extraction_method: str
    warnings: list[str]
    raw_model_output: str
    readiness_score: float | None
    meets_readiness_threshold: bool | None
    readiness_notes: list[str]
    acp_missing_fields: list[str]
    acp_invalid_fields: list[str]
    ondc_blockers: list[str]
    grounding_rejected_fields: list[str] = field(default_factory=list)


# (cleaned_row, row_id, reflection_note, previous_structured) -> AttemptOutcome
AttemptFn = Callable[[dict[str, Any], str, "str | None", "dict[str, Any] | None"], AttemptOutcome]


class OnAttempt(Protocol):
    def __call__(self, record: "AttemptRecord") -> None: ...


@dataclass
class AttemptRecord:
    attempt: int
    outcome: AttemptOutcome
    reflection_note: str | None
    classification: AttemptClassification


@dataclass
class ReflexionResult:
    attempts: list[AttemptRecord]
    best: AttemptRecord
    retried: bool
    improved: bool
    # "passed" | "source_limited" | "max_retries" | "failed_structuring"
    stopped_reason: str

    @property
    def fabrication_blocked(self) -> list[str]:
        """Every field any attempt's grounding check rejected, across
        the whole run — worth surfacing in the demo as proof the loop
        refuses to cheat, not just that it retries."""
        out: list[str] = []
        for record in self.attempts:
            out.extend(record.outcome.grounding_rejected_fields)
        return out


def _score_key(record: AttemptRecord) -> float:
    score = record.outcome.readiness_score
    return score if score is not None else -1.0


def _best(attempts: list[AttemptRecord]) -> AttemptRecord:
    # max() keeps the FIRST max on ties (stable), so an equal-scoring
    # retry never displaces a cheaper earlier attempt for no reason.
    return max(attempts, key=_score_key)


def _improved(attempts: list[AttemptRecord]) -> bool:
    if len(attempts) < 2:
        return False
    return _score_key(_best(attempts)) > _score_key(attempts[0])


def run_reflexive_structuring(
    *,
    cleaned_row: dict[str, Any],
    raw_row: dict[str, Any],
    row_id: str,
    attempt_fn: AttemptFn,
    threshold: float = DEFAULT_THRESHOLD,
    max_retries: int = DEFAULT_MAX_RETRIES,
    on_attempt: OnAttempt | None = None,
    retrieve_memory: "RetrieveMemoryFn | None" = None,
) -> ReflexionResult:
    """Runs the bounded actor/evaluate/reflect loop for one row.

    on_attempt, when given, fires after EVERY attempt (including ones
    that don't retry) — main.py uses this to write a decision_log entry
    per attempt, so the demo can show the reflection trail, not just
    the final outcome.

    retrieve_memory (added): the cross-batch episodic-memory hook (see
    reflection_memory.py). Called TWICE, differently, per row:
      - once, pre-emptively, with tags=[] — before attempt 1 even
        runs, using the RAW row's own stated category. This is the
        ONLY point where memory can influence a FIRST attempt, since
        no gap classification exists yet to retrieve against; skipped
        entirely if the raw row has no category field at all (no
        guessing one).
      - again on every retry, with this attempt's real
        classification.fixable_tags, using the STRUCTURED category
        once Gemini has actually assigned one (falling back to the
        raw row's, if that's absent). Ranked by tag overlap, not
        recency, since a specific gap is now known.
    A raising or None-returning implementation degrades to "no memory
    available" for that call — reflexion.py's own correctness never
    depends on this; it's a pure enhancement, same fail-open contract
    reflection_memory.py holds itself to.
    """
    # source_richness is 45% of readiness and is fixed by raw_row alone
    # — no retry of STRUCTURING can move it. Computed once, up front,
    # so classify_attempt can demote readiness-only findings on every
    # attempt without recomputing this each time.
    ceiling_unreachable = source_richness_ceiling(raw_row) < threshold

    attempts: list[AttemptRecord] = []
    previous_structured: dict[str, Any] | None = None
    reflection_note: str | None = None
    attempt_number = 1

    if retrieve_memory is not None:
        guess_category = str(raw_row.get("category") or "").strip()
        if guess_category:
            try:
                prior_notes = retrieve_memory(guess_category, [])
            except Exception:  # noqa: BLE001 — fail-open, see docstring
                prior_notes = []
            if prior_notes:
                reflection_note = build_prior_context_note(prior_notes)

    while True:
        outcome = attempt_fn(cleaned_row, row_id, reflection_note, previous_structured)

        if not outcome.success or outcome.structured is None:
            record = AttemptRecord(
                attempt=attempt_number,
                outcome=outcome,
                reflection_note=reflection_note,
                classification=AttemptClassification(),
            )
            attempts.append(record)
            if on_attempt:
                on_attempt(record)
            return ReflexionResult(
                attempts=attempts, best=record, retried=attempt_number > 1,
                improved=False, stopped_reason="failed_structuring",
            )

        classification = classify_attempt(
            raw_row=raw_row,
            structured=outcome.structured,
            extraction_method=outcome.extraction_method,
            acp_missing_fields=outcome.acp_missing_fields,
            acp_invalid_fields=outcome.acp_invalid_fields,
            ondc_blockers=outcome.ondc_blockers,
            ceiling_unreachable=ceiling_unreachable,
        )
        record = AttemptRecord(
            attempt=attempt_number, outcome=outcome, reflection_note=reflection_note, classification=classification,
        )
        attempts.append(record)
        if on_attempt:
            on_attempt(record)

        if bool(outcome.meets_readiness_threshold) and not classification.is_retriable:
            return ReflexionResult(
                attempts=attempts, best=record, retried=attempt_number > 1,
                improved=_improved(attempts), stopped_reason="passed",
            )
        if not classification.is_retriable:
            return ReflexionResult(
                attempts=attempts, best=_best(attempts), retried=attempt_number > 1,
                improved=_improved(attempts), stopped_reason="source_limited",
            )
        if attempt_number > max_retries:
            return ReflexionResult(
                attempts=attempts, best=_best(attempts), retried=attempt_number > 1,
                improved=_improved(attempts), stopped_reason="max_retries",
            )

        previous_structured = outcome.structured

        retry_prior_notes: list[str] = []
        if retrieve_memory is not None:
            retry_category = str(
                (outcome.structured or {}).get("category") or raw_row.get("category") or ""
            ).strip()
            if retry_category:
                try:
                    retry_prior_notes = retrieve_memory(retry_category, classification.fixable_tags)
                except Exception:  # noqa: BLE001 — fail-open, see docstring
                    retry_prior_notes = []

        reflection_note = build_reflection_note(classification, attempt_number, prior_notes=retry_prior_notes)
        attempt_number += 1
        
        