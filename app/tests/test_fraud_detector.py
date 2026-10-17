import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fraud_detector import FraudSignalDetector  # noqa: E402

SCENARIOS_PATH = Path(__file__).resolve().parents[1] / "fraud_scenarios.json"


def _load_scenarios():
    with open(SCENARIOS_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_bracketing_and_high_return_rate_is_flagged():
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s["return_id"] == "RET-001")
    result = detector.check_return(scenario)
    assert result.flagged
    assert result.risk_level == "high"
    assert len(result.signals) >= 2


def test_legitimate_shipping_damage_is_not_flagged():
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s["return_id"] == "RET-002")
    result = detector.check_return(scenario)
    assert not result.flagged
    assert result.risk_level == "low"
    assert result.signals == []


def test_wardrobing_is_caught_even_with_low_return_rate():
    """RET-003 has a LOW overall return rate (1/3) — if the detector only
    looked at return rate, it would miss this. It should catch the
    wardrobing pattern (single-wear item, rapid return, vague reason)
    through a different signal."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s["return_id"] == "RET-003")
    result = detector.check_return(scenario)
    assert result.flagged
    assert any("wardrobing" in s.lower() for s in result.signals)


def test_legitimate_fulfillment_error_is_not_flagged():
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s["return_id"] == "RET-004")
    result = detector.check_return(scenario)
    assert not result.flagged


def test_borderline_case_flagged_via_behavior_not_stated_reason():
    """RET-005's stated reason is plausible on its own — the flag should
    come from the return-rate pattern, not reason-text matching."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s["return_id"] == "RET-005")
    result = detector.check_return(scenario)
    assert result.flagged
    assert "return rate" in " ".join(result.signals).lower()


def test_modest_value_inr_return_not_flagged_as_high_value():
    """Regression test for the currency-blindness gap: the old threshold
    was a bare >= 500 with no currency context, so ANY currency's "500"
    was treated as high-value — 500 rupees (~$6) is not. This stayed a
    hand-built scenario rather than a fraud_scenarios.json entry when it
    was first written, specifically to avoid changing the "5/5" figure
    then cited on the Overview page — that restraint doesn't apply to
    the COD scenarios added later in fraud_scenarios.json, which were an
    explicit, deliberate expansion of the official set (see
    test_full_harness_scores_all_scenarios_correctly)."""
    detector = FraudSignalDetector(backend="mock")
    scenario = {
        "return_id": "REGRESSION-INR-THRESHOLD",
        "customer_name": "N. Reddy",
        "product_name": "Cotton Handkerchief Set",
        "product_price": 600,
        "currency": "INR",
        "order_date": "2026-08-01",
        "return_requested_date": "2026-08-10",
        "return_reason": "Changed my mind",
        "customer_total_orders_last_90_days": 4,
        "customer_total_returns_last_90_days": 1,
        "notes": "First return from this customer this quarter.",
    }
    result = detector.check_return(scenario)
    assert not result.flagged, (
        f"\u20b9600 should not trigger the high-value signal (INR threshold is 5000) — "
        f"got signals: {result.signals}. Under the old currency-blind >= 500 check this "
        f"would have been a false positive."
    )


def test_cod_bracketing_and_high_refusal_rate_is_flagged():
    """COD-001: the COD equivalent of RET-001's textbook combo —
    multiple variants ordered via COD, most refused, plus an elevated
    refusal rate. Confirms both COD-specific signals fire together."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s.get("case_id") == "COD-001")
    result = detector.check_return(scenario)
    assert result.flagged
    assert result.risk_level == "high"
    assert len(result.signals) >= 2


def test_legitimate_single_cod_refusal_is_not_flagged():
    """COD-002: a first-time, single COD refusal with a specific,
    verifiable reason (color mismatch confirmed by the courier) — COD's
    routine doorstep quality check, not fraud. Also confirms the
    `cod_orders >= 3` guard: a 100% refusal rate from a single order
    shouldn't flag on rate alone, same reasoning as the return check's
    `orders >= 3` guard."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s.get("case_id") == "COD-002")
    result = detector.check_return(scenario)
    assert not result.flagged
    assert result.risk_level == "low"
    assert result.signals == []


def test_undelivered_customer_unreachable_pattern_is_flagged():
    """COD-003: the specifically-requested undelivered-order pattern —
    repeated 'customer unreachable' outcomes, not an active refusal at
    the door. A different signal combination than COD-001 (rate +
    unreachable-pattern, not rate + bracketing), confirming the two
    COD-specific signals are independently detected, not just one
    check disguised as two."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s.get("case_id") == "COD-003")
    result = detector.check_return(scenario)
    assert result.flagged
    assert any("unreachable" in s.lower() for s in result.signals)


def test_cod_borderline_case_flagged_via_rate_not_reason_text():
    """COD-004: the COD equivalent of RET-005 — the stated refusal
    reason sounds routine on its own; the flag should come from the
    elevated refusal rate across categories, not from matching the
    reason text against a phrase list."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    scenario = next(s for s in scenarios if s.get("case_id") == "COD-004")
    result = detector.check_return(scenario)
    assert result.flagged
    assert "refusal" in " ".join(result.signals).lower() or "rate" in " ".join(result.signals).lower()


def test_return_scenarios_unaffected_by_cod_dispatch():
    """Confirms adding the COD dispatch didn't change return-scenario
    behavior: scenario_type is absent on all 5 original scenarios, so
    they must still take the exact same code path as before."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    return_scenarios = [s for s in scenarios if s.get("scenario_type") != "cod_refusal"]
    assert len(return_scenarios) == 5
    report = detector.run_test_harness(return_scenarios)
    assert report["accuracy"] == 1.0


def test_harness_results_expose_scenario_type():
    """The frontend (FraudPage.jsx) needs a way to tell return cases from
    COD cases apart without guessing from the ID prefix — confirms every
    result carries scenario_type, defaulting to "return" for the 5
    original scenarios which never set the field explicitly."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    report = detector.run_test_harness(scenarios)
    by_id = {r["return_id"]: r["scenario_type"] for r in report["results"]}
    for ret_id in ("RET-001", "RET-002", "RET-003", "RET-004", "RET-005"):
        assert by_id[ret_id] == "return"
    for cod_id in ("COD-001", "COD-002", "COD-003", "COD-004"):
        assert by_id[cod_id] == "cod_refusal"


def test_full_harness_scores_all_scenarios_correctly():
    """The actual demo evidence: run every prepared scenario — 5 return
    scenarios plus 4 COD scenarios — score against ground truth, report
    accuracy. Renamed from test_full_harness_scores_all_five_correctly
    now that the count is 9, not 5 — that count changing here was the
    deliberate point of adding COD coverage, not an accident to guard
    against."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    report = detector.run_test_harness(scenarios)
    assert report["scored"] == 9
    assert report["accuracy"] == 1.0, f"expected 9/9, got {report['correct']}/{report['scored']}"


def test_extraction_method_varies_across_a_batch():
    """Confirms the mock's JSON-format cycling (clean vs fenced) actually
    exercises more than one fallback_parser layer, not just strict_json
    every time."""
    detector = FraudSignalDetector(backend="mock")
    scenarios = _load_scenarios()
    methods = {detector.check_return(s).extraction_method for s in scenarios}
    assert len(methods) > 1, f"expected multiple extraction methods, got only {methods}"


if __name__ == "__main__":
    tests = [f for name, f in list(globals().items()) if name.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS  {t.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed")
    
    