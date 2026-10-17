"""Tests for reflexion_ab_harness.py — run against the mock backend so
this is fully offline. Two kinds of check: that the harness's own
aggregation arithmetic is correct (not just "runs without crashing"),
and that the fair-methodology invariant it depends on actually holds —
see run_ab_comparison's own docstring on why a fresh agent per arm was
necessary, found by running this harness for real and catching a
shared-mutable-state bug that made the comparison unfair."""

from dataclasses import dataclass

import pytest
from catalog_agent_mock import MockCatalogStructuringAgent
from reflexion_ab_harness import build_attempt_fn, load_dataset, run_ab_comparison


@dataclass
class FakeSellerProfile:
    name: str = "AB Test Seller"
    is_packer: bool = False
    packer_name: str = None
    packer_address: str = None
    consumer_care_name: str = None
    consumer_care_email: str = None
    consumer_care_phone: str = None
    returnable: bool = None
    cancellable: bool = None
    return_window_days: int = None
    time_to_ship_hours: int = None
    privacy_policy: str = None
    tos: str = None

    @property
    def resolved_packer_name(self):
        return self.packer_name

    @property
    def consumer_care_contact(self):
        return None


def _agent_factory():
    return MockCatalogStructuringAgent()


def _run():
    return run_ab_comparison(load_dataset(), _agent_factory, FakeSellerProfile())


def test_dataset_loads_with_expected_row_count():
    dataset = load_dataset()
    assert len(dataset) == 10
    assert all("sku" in row and "note" in row for row in dataset)


def test_every_row_produces_a_comparison():
    result = _run()
    assert result.n_rows == 10
    assert len(result.rows) == 10


def test_score_with_never_regresses_below_score_without():
    """The core fairness invariant: a fresh agent per arm means the
    'with' arm's first attempt is computed under IDENTICAL conditions
    to the 'without' arm's only attempt, so best-attempt selection can
    only match or beat it. This is what a shared-agent bug violated —
    found by running the real harness, not by this test alone; kept
    here so it can never silently regress again."""
    result = _run()
    for r in result.rows:
        if r.score_without is not None and r.score_with is not None:
            assert r.score_with >= r.score_without, (
                f"{r.row_id}: reflexion regressed the score "
                f"({r.score_without} -> {r.score_with})"
            )


def test_genuinely_thin_source_never_retries():
    result = _run()
    ab03 = next(r for r in result.rows if r.row_id == "AB-03")
    assert ab03.attempts_with == 1
    assert ab03.stopped_reason_with == "source_limited"


def test_rich_source_row_retries_and_improves():
    result = _run()
    ab01 = next(r for r in result.rows if r.row_id == "AB-01")
    assert ab01.attempts_with > 1
    assert ab01.score_delta is not None and ab01.score_delta > 0


def test_no_fabrication_ever_slips_through_an_honest_mock():
    result = _run()
    assert result.total_fabrication_blocked == 0


def test_aggregate_averages_match_manual_arithmetic():
    """Pins the aggregation math itself, not just that it runs —
    recomputes both averages independently from the per-row list and
    checks they agree with what ABHarnessResult reports."""
    result = _run()
    scored_without = [r.score_without for r in result.rows if r.score_without is not None]
    scored_with = [r.score_with for r in result.rows if r.score_with is not None]
    assert result.avg_score_without == round(sum(scored_without) / len(scored_without), 1)
    assert result.avg_score_with == round(sum(scored_with) / len(scored_with), 1)
    assert result.passed_without == sum(1 for r in result.rows if r.passed_without)
    assert result.passed_with == sum(1 for r in result.rows if r.passed_with)


def test_to_dict_is_json_shaped_and_round_trips_row_count():
    import json

    result = _run()
    d = result.to_dict()
    encoded = json.dumps(d)  # must not raise — every value JSON-serializable
    decoded = json.loads(encoded)
    assert decoded["n_rows"] == 10
    assert len(decoded["rows"]) == 10


def test_max_retries_zero_is_equivalent_to_reflexion_disabled():
    """Directly exercises what 'without' means, in isolation from the
    harness's own row-comparison plumbing — see run_ab_comparison's
    docstring for why this equivalence is what makes the whole A/B
    design valid rather than two separately-written code paths that
    could silently drift apart."""
    from reflexion import run_reflexive_structuring

    attempt_fn = build_attempt_fn(MockCatalogStructuringAgent(), FakeSellerProfile())
    row = {"sku": "X", "name": "Handwoven Cotton Kurta", "description": "Breathable cotton kurta with detailing.", "price": 899, "category": "Apparel"}
    result = run_reflexive_structuring(cleaned_row=row, raw_row=row, row_id="X", attempt_fn=attempt_fn, max_retries=0)
    assert len(result.attempts) == 1
    assert result.retried is False
    