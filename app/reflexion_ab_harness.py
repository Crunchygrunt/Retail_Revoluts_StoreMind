"""
Reflexion A/B harness — the actual demo evidence for "reflexion helps,"
in the same spirit as fraud_detector.py's run_test_harness(): a labeled
scenario set, run through the real pipeline, reduced to numbers a judge
can be shown rather than asserted at.

Deliberately does NOT import main.py — same reasoning
backfill_ucp_eligibility.py already gives for its own standalone
_build_store(): main.py also stands up the FastAPI app, mounts MCP,
connects to real GCP if STORE_BACKEND=firestore, etc. on import, none
of which a comparison script needs or should trigger. _build_agent()
below is kept in sync with main.py's own by hand, same convention.

WHAT "without reflexion" MEANS, exactly: run_reflexive_structuring(...,
max_retries=0). Not a second, separately-written code path — the SAME
loop, the SAME classifier, the SAME grounding check, just stopped after
attempt 1. This matters: it guarantees the "before" arm can never
diverge from the "after" arm for any reason OTHER than the retries
themselves, which is the entire point of an A/B comparison. Verified:
attempt_number=1, not > max_retries=0 fails immediately after the first
attempt's classification, landing on stopped_reason="max_retries" with
exactly one attempt recorded — functionally identical to reflexion
never having existed for that row.

WHAT THIS HARNESS DELIBERATELY DOES NOT DO: run the full main.py
/ingest post-processing (currency/brand batch inference, image hosting,
product-page URL generation). Those are cross-product or I/O-bound
concerns orthogonal to what reflexion actually targets. Skipping them
does not corrupt the comparison: url/image_url/brand, when absent, land
in acp_missing_fields exactly as they would with the full pipeline, but
none of the three are in reflexion_evaluator.MODEL_FIXABLE_ACP_FIELDS,
so they are classified source-limited and never retried either way —
identical behavior to the full pipeline for the one thing this harness
measures.

Run standalone:
    python3 reflexion_ab_harness.py

Or via GET /reflexion/ab-harness once wired into main.py (see the
main.py patch note in the accompanying chat message) — same "run it
live during the demo" role /fraud/test-harness already plays.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from acp_schema import check_acp_conformance
from ondc_eligibility import derive_ondc_eligibility
from readiness_score import DEFAULT_THRESHOLD, compute_readiness_score
from reflexion import AttemptFn, AttemptOutcome, DEFAULT_MAX_RETRIES, run_reflexive_structuring
from reflexion_evaluator import check_grounding

DATASET_PATH = Path(__file__).parent / "reflexion_ab_dataset.json"


def _build_agent():
    """Mirrors main.py's own _build_agent() — kept in sync by hand,
    same convention backfill_ucp_eligibility.py already established
    for exactly this reason (see module docstring)."""
    llm_backend = os.environ.get("STOREMIND_LLM_BACKEND", "mock")
    if llm_backend == "vertexai":
        from catalog_agent import CatalogStructuringAgent

        return CatalogStructuringAgent()
    if llm_backend == "aistudio":
        from catalog_agent_aistudio import GeminiAIStudioAgent

        return GeminiAIStudioAgent()
    from catalog_agent_mock import MockCatalogStructuringAgent

    return MockCatalogStructuringAgent()


def build_attempt_fn(agent: Any, seller_profile: Any) -> AttemptFn:
    """The same attempt shape main.py's /ingest uses (structure ->
    grounding check -> score -> conformance/ONDC), minus the batch/IO
    concerns this module's docstring explains skipping. seller_profile
    is passed in rather than loaded here, so a caller can hand this an
    unconfigured stub without ANY env vars set — every seller-config
    gap that produces just becomes another source-limited finding
    (see reflexion_evaluator.py), never a false positive for what
    reflexion is actually being measured on.
    """

    def attempt_fn(
        cleaned_row: dict[str, Any],
        row_id: str,
        reflection_note: "str | None",
        previous_structured: "dict[str, Any] | None",
    ) -> AttemptOutcome:
        result = agent.structure_row(cleaned_row, row_id, reflection_note=reflection_note)

        if not result.success or result.structured is None:
            return AttemptOutcome(
                success=False, structured=None, extraction_method=result.extraction_method,
                warnings=result.warnings, raw_model_output=result.raw_model_output,
                readiness_score=None, meets_readiness_threshold=None, readiness_notes=[],
                acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
            )

        # Grounding check BEFORE scoring — same ordering requirement as
        # main.py's own attempt_fn, same reason (see reflexion.py).
        grounding_rejected = check_grounding(before=previous_structured, after=result.structured, raw_row=cleaned_row)
        for f in grounding_rejected:
            if previous_structured and f in previous_structured:
                result.structured[f] = previous_structured[f]
            else:
                result.structured.pop(f, None)

        score_result = compute_readiness_score(result.structured, result.extraction_method, raw_row=cleaned_row)
        result.structured["readiness_score"] = score_result.score
        result.structured["meets_readiness_threshold"] = score_result.meets_threshold

        conformance = check_acp_conformance(result.structured, seller_profile=seller_profile)
        ondc_result = derive_ondc_eligibility(result.structured, seller_profile)

        return AttemptOutcome(
            success=True, structured=result.structured, extraction_method=result.extraction_method,
            warnings=result.warnings, raw_model_output=result.raw_model_output,
            readiness_score=score_result.score, meets_readiness_threshold=score_result.meets_threshold,
            readiness_notes=score_result.notes,
            acp_missing_fields=conformance.missing_fields, acp_invalid_fields=conformance.invalid_fields,
            ondc_blockers=ondc_result.ondc_blockers, grounding_rejected_fields=grounding_rejected,
        )

    return attempt_fn


@dataclass
class RowComparison:
    row_id: str
    label: str
    score_without: "float | None"
    score_with: "float | None"
    passed_without: bool
    passed_with: bool
    attempts_with: int
    stopped_reason_with: str
    fabrication_blocked: list[str]
    improved: bool

    @property
    def score_delta(self) -> "float | None":
        if self.score_without is None or self.score_with is None:
            return None
        return round(self.score_with - self.score_without, 1)


@dataclass
class ABHarnessResult:
    rows: list[RowComparison] = field(default_factory=list)
    n_rows: int = 0
    avg_score_without: float = 0.0
    avg_score_with: float = 0.0
    passed_without: int = 0
    passed_with: int = 0
    rows_improved: int = 0
    rows_retried: int = 0
    total_fabrication_blocked: int = 0

    @property
    def avg_score_lift(self) -> float:
        return round(self.avg_score_with - self.avg_score_without, 1)

    @property
    def additional_rows_passing(self) -> int:
        return self.passed_with - self.passed_without

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable summary — what GET /reflexion/ab-harness
        returns, same role fraud_detector.run_test_harness()'s return
        value plays for /fraud/test-harness."""
        return {
            "n_rows": self.n_rows,
            "avg_score_without_reflexion": self.avg_score_without,
            "avg_score_with_reflexion": self.avg_score_with,
            "avg_score_lift": self.avg_score_lift,
            "rows_passed_without_reflexion": self.passed_without,
            "rows_passed_with_reflexion": self.passed_with,
            "additional_rows_passing": self.additional_rows_passing,
            "rows_improved": self.rows_improved,
            "rows_retried": self.rows_retried,
            "total_fabrication_blocked": self.total_fabrication_blocked,
            "rows": [
                {
                    "row_id": r.row_id,
                    "label": r.label,
                    "score_without": r.score_without,
                    "score_with": r.score_with,
                    "score_delta": r.score_delta,
                    "passed_without": r.passed_without,
                    "passed_with": r.passed_with,
                    "attempts_with": r.attempts_with,
                    "stopped_reason_with": r.stopped_reason_with,
                    "fabrication_blocked": r.fabrication_blocked,
                }
                for r in self.rows
            ],
        }


def load_dataset(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_ab_comparison(
    dataset: list[dict[str, Any]],
    agent_factory: "Callable[[], Any]",
    seller_profile: Any,
    max_retries: int = DEFAULT_MAX_RETRIES,
    threshold: float = DEFAULT_THRESHOLD,
) -> ABHarnessResult:
    """Runs every row through the IDENTICAL loop twice — max_retries=0
    ("without") and max_retries=<real value> ("with") — see module
    docstring for why this guarantees the two arms can only differ
    because of the retries themselves.

    Takes an agent FACTORY, not a shared agent instance, and calls it
    fresh for EACH arm of EACH row. Found necessary by running this
    harness for real: catalog_agent_mock.py's deterministic output-
    style rotation is driven by an instance-level call counter (by
    design — see that file's own docstring on exercising every
    FallbackParser layer across a batch). Sharing one agent across both
    arms meant the "without" arm's one call and the "with" arm's FIRST
    call landed on different rotation phases purely from call-order
    accounting, not from anything about reflexion itself — an unfair
    comparison, not just a coincidence. A fresh agent per arm guarantees
    both start from the same internal state, so the "with" arm's first
    attempt is computed under IDENTICAL conditions to the "without"
    arm's only attempt; any difference from there is attributable
    ONLY to the retries. Cheap either way — agent construction is
    lazy on all three backends (no client/auth work until the first
    real call), so this costs nothing beyond one extra object per arm.
    """
    rows: list[RowComparison] = []

    for row in dataset:
        cleaned_row = {k: v for k, v in row.items() if k != "note"}
        row_id = str(cleaned_row.get("sku") or cleaned_row.get("name") or "unknown")
        label = row.get("note", "")

        without_attempt_fn = build_attempt_fn(agent_factory(), seller_profile)
        without = run_reflexive_structuring(
            cleaned_row=cleaned_row, raw_row=cleaned_row, row_id=row_id,
            attempt_fn=without_attempt_fn, threshold=threshold, max_retries=0,
        )

        with_attempt_fn = build_attempt_fn(agent_factory(), seller_profile)
        with_reflexion = run_reflexive_structuring(
            cleaned_row=cleaned_row, raw_row=cleaned_row, row_id=row_id,
            attempt_fn=with_attempt_fn, threshold=threshold, max_retries=max_retries,
        )

        score_without = without.best.outcome.readiness_score
        score_with = with_reflexion.best.outcome.readiness_score
        improved = score_without is not None and score_with is not None and score_with > score_without

        rows.append(
            RowComparison(
                row_id=row_id, label=label,
                score_without=score_without, score_with=score_with,
                passed_without=bool(without.best.outcome.meets_readiness_threshold),
                passed_with=bool(with_reflexion.best.outcome.meets_readiness_threshold),
                attempts_with=len(with_reflexion.attempts),
                stopped_reason_with=with_reflexion.stopped_reason,
                fabrication_blocked=with_reflexion.fabrication_blocked,
                improved=improved,
            )
        )

    scored_without = [r.score_without for r in rows if r.score_without is not None]
    scored_with = [r.score_with for r in rows if r.score_with is not None]

    return ABHarnessResult(
        rows=rows,
        n_rows=len(rows),
        avg_score_without=round(sum(scored_without) / len(scored_without), 1) if scored_without else 0.0,
        avg_score_with=round(sum(scored_with) / len(scored_with), 1) if scored_with else 0.0,
        passed_without=sum(1 for r in rows if r.passed_without),
        passed_with=sum(1 for r in rows if r.passed_with),
        rows_improved=sum(1 for r in rows if r.improved),
        rows_retried=sum(1 for r in rows if r.attempts_with > 1),
        total_fabrication_blocked=sum(len(r.fabrication_blocked) for r in rows),
    )


def print_report(result: ABHarnessResult) -> None:
    print(f"Reflexion A/B — {result.n_rows} rows\n")
    print(f"{'row':<8} {'without':>8} {'with':>8} {'delta':>7}  attempts  stopped_reason         label")
    for r in result.rows:
        delta = f"{r.score_delta:+.1f}" if r.score_delta is not None else "  n/a"
        print(
            f"{r.row_id:<8} {str(r.score_without):>8} {str(r.score_with):>8} {delta:>7}  "
            f"{r.attempts_with:>8}  {r.stopped_reason_with:<22} {r.label[:60]}"
        )
    print()
    print(f"Average readiness score  — without: {result.avg_score_without}   with: {result.avg_score_with}   "
          f"lift: {result.avg_score_lift:+.1f}")
    print(f"Rows passing threshold   — without: {result.passed_without}/{result.n_rows}   "
          f"with: {result.passed_with}/{result.n_rows}   (+{result.additional_rows_passing})")
    print(f"Rows genuinely improved by a retry: {result.rows_improved}")
    print(f"Rows that retried at all:           {result.rows_retried}")
    print(f"Fabrication attempts blocked:       {result.total_fabrication_blocked}"
          + ("  <-- should be 0; investigate if not" if result.total_fabrication_blocked else "  (expected: 0)"))


def main() -> None:
    from seller_profile import load_seller_profile

    seller_profile = load_seller_profile()
    dataset = load_dataset()
    result = run_ab_comparison(dataset, _build_agent, seller_profile)
    print_report(result)


if __name__ == "__main__":
    main()
    