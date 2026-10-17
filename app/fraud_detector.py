"""
Fraud Signal Detector.

Assesses a product return request for signs of return fraud — wardrobing,
bracketing, serial returning, vague/implausible reasons — using the same
swappable backend as the Catalog Structuring Agent: mock (offline,
default) | aistudio | vertexai, selected via STOREMIND_LLM_BACKEND. No
separate config needed; whichever backend structures your catalog also
assesses returns.

Per the build plan, this is tested against a small LABELED set of
prepared return scenarios, not one cherry-picked example — see
fraud_scenarios.json and run_test_harness() below. The accuracy readout
that produces is the actual demo evidence, not a single flagged case.

Unlike catalog_agent.py / catalog_agent_aistudio.py (kept as separate
files for historical reasons — they were built incrementally across
earlier phases), this module unifies all three backends into one class,
since it was built in one pass with the benefit of hindsight.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fallback_parser import FallbackParser

FRAUD_PROMPT = """You are a fraud-risk analyst reviewing an e-commerce order-risk \
case. Two case types are possible, told apart by "scenario_type" in the details \
below:

- "return" (or scenario_type absent): a post-delivery RETURN request. Assess for \
wardrobing (wearing/using then returning), bracketing (ordering multiple variants \
intending to return most), serial returning, or a vague/implausible return reason.

- "cod_refusal": a Cash-on-Delivery order refused at the door or left undelivered \
(customer unreachable). The item was never accepted, so wardrobing does not apply. \
Assess for a high refusal/non-delivery rate, COD bracketing (multiple variants \
ordered via COD, most refused at delivery), and a repeated "customer unreachable" \
pattern — which suggests fake, prank, or deliberately wasteful orders rather than a \
one-off missed delivery.

Respond with ONLY a JSON object (no markdown, no commentary) with exactly these \
fields:

- "risk_level": one of "low", "medium", "high"
- "reasoning": a 1-2 sentence explanation for this risk level (string)
- "signals": specific fraud indicators you noticed, empty array if none (array of strings)

Case details:
{scenario}
"""

RISK_LEVELS = ("low", "medium", "high")

# Same rate-limit handling proven against real 429s in catalog_agent_aistudio.py.
RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "rate limit", "quota")
DAILY_QUOTA_MARKER = "PerDay"
RETRY_DELAY_PATTERN = re.compile(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s")
MAX_RETRIES = 1
DEFAULT_RETRY_WAIT_SECONDS = 20.0
MAX_RETRY_WAIT_SECONDS = 65.0

VAGUE_REASON_PHRASES = ("didn't like", "changed my mind", "not as expected", "no longer needed")
SINGLE_USE_CATEGORIES = ("dress", "costume", "gown", "suit", "tuxedo")

# "High-value" thresholds by ISO 4217 code, for the vague-reason-on-a-
# high-value-item signal below. The original >= 500 threshold was
# currency-unit-agnostic (flagged as a known gap in the original build
# handoff) — 500 of a currency isn't a fixed real-world value, so the
# same check silently meant "$500" for one scenario and "500 rupiah"
# (a few cents) for another. These are rough, approximate starting
# points for calibration, not authoritative figures — same caveat as
# the weights in readiness_score.py: worth tuning against real return
# data before relying on them, not a claimed-precise conversion table.
HIGH_VALUE_THRESHOLD_BY_CURRENCY = {
    "USD": 500,
    "EUR": 500,
    "GBP": 500,
    "INR": 5000,
    "IDR": 5_000_000,
    "VND": 5_000_000,
    "THB": 15_000,
    "LAK": 4_000_000,
    "MMK": 500_000,
}
# Falls back to the original >= 500 behavior when a scenario has no
# currency field at all — existing scenarios in fraud_scenarios.json
# keep scoring exactly as before; only a scenario that actually
# specifies a currency gets the currency-aware threshold.
DEFAULT_HIGH_VALUE_THRESHOLD = 500


@dataclass
class FraudCheckResult:
    return_id: str
    risk_level: str  # "low" | "medium" | "high"
    flagged: bool  # True when risk_level != "low"
    reasoning: str
    signals: list[str]
    extraction_method: str
    raw_model_output: str
    warnings: list[str] = field(default_factory=list)


class FraudSignalDetector:
    def __init__(
        self,
        backend: str | None = None,
        api_key: str | None = None,
        project: str | None = None,
        location: str | None = None,
        model_name: str | None = None,
    ):
        self.backend = backend or os.environ.get("STOREMIND_LLM_BACKEND", "mock")
        self.parser = FallbackParser()
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        if self.backend == "aistudio":
            default_model = os.environ.get("STOREMIND_AISTUDIO_MODEL", "gemini-3.6-flash")
        elif self.backend == "vertexai":
            default_model = os.environ.get("STOREMIND_VERTEXAI_MODEL", "gemini-3.6-flash")
        else:
            default_model = "mock"
        self.model_name = model_name or default_model
        self._client = None
        self._call_count = 0

    # ---- public API -----------------------------------------------------

    def check_return(self, scenario: dict[str, Any]) -> FraudCheckResult:
        # "case_id" is the more accurate name for a COD-refusal case (it
        # was never a return), but "return_id" stays the lookup fallback
        # so the 5 existing return scenarios are untouched.
        return_id = str(scenario.get("case_id") or scenario.get("return_id", "unknown"))

        if self.backend == "mock":
            return self._mock_check(return_id, scenario)

        raw_text = self._call_real_model(scenario)
        parsed = self.parser.parse(raw_text)

        if not parsed.success or not parsed.data:
            # Fail closed: if we can't parse an assessment at all, treat it
            # as needing manual review rather than silently letting it
            # through — same "enforced, not just scored" instinct as the
            # readiness gate.
            return FraudCheckResult(
                return_id=return_id,
                risk_level="high",
                flagged=True,
                reasoning="Could not parse a fraud assessment from the model response — flagged for manual review.",
                signals=["parse_failure"],
                extraction_method=parsed.extraction_method,
                raw_model_output=raw_text,
                warnings=parsed.warnings,
            )

        data = parsed.data
        risk_level = str(data.get("risk_level", "medium")).lower()
        if risk_level not in RISK_LEVELS:
            risk_level = "medium"

        return FraudCheckResult(
            return_id=return_id,
            risk_level=risk_level,
            flagged=risk_level != "low",
            reasoning=str(data.get("reasoning", "")),
            signals=list(data.get("signals") or []),
            extraction_method=parsed.extraction_method,
            raw_model_output=raw_text,
            warnings=parsed.warnings,
        )

    def run_test_harness(self, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
        """Runs every prepared scenario and scores against its
        ground_truth_flag. This accuracy readout IS the demo evidence —
        per the build plan, a single flagged example proves nothing."""
        results = []
        correct = 0
        for scenario in scenarios:
            result = self.check_return(scenario)
            ground_truth = scenario.get("ground_truth_flag")
            is_correct = (ground_truth is not None) and (result.flagged == ground_truth)
            if is_correct:
                correct += 1
            results.append(
                {
                    "return_id": result.return_id,
                    "scenario_type": scenario.get("scenario_type", "return"),
                    "risk_level": result.risk_level,
                    "flagged": result.flagged,
                    "ground_truth_flag": ground_truth,
                    "correct": is_correct,
                    "reasoning": result.reasoning,
                    "signals": result.signals,
                    "extraction_method": result.extraction_method,
                }
            )

        scored = [r for r in results if r["ground_truth_flag"] is not None]
        accuracy = (correct / len(scored)) if scored else None
        return {
            "backend": self.backend,
            "total_scenarios": len(scenarios),
            "scored": len(scored),
            "correct": correct,
            "accuracy": round(accuracy, 3) if accuracy is not None else None,
            "results": results,
        }

    # ---- real backends (aistudio / vertexai) -------------------------------

    def _call_real_model(self, scenario: dict[str, Any]) -> str:
        prompt = FRAUD_PROMPT.format(scenario=json.dumps(scenario, ensure_ascii=False))
        client = self._get_client()

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(model=self.model_name, contents=prompt)
                return response.text
            except Exception as exc:  # noqa: BLE001
                err_str = str(exc)
                is_rate_limit = any(marker.lower() in err_str.lower() for marker in RATE_LIMIT_MARKERS)
                is_daily_quota = DAILY_QUOTA_MARKER in err_str
                if is_rate_limit and not is_daily_quota and attempt < MAX_RETRIES:
                    delay_match = RETRY_DELAY_PATTERN.search(err_str)
                    wait = float(delay_match.group(1)) if delay_match else DEFAULT_RETRY_WAIT_SECONDS
                    time.sleep(min(wait, MAX_RETRY_WAIT_SECONDS))
                    continue
                raise
        raise RuntimeError("unreachable")  # loop always returns or raises

    def _get_client(self):
        if self._client is not None:
            return self._client
        from google import genai  # lazy import, matches the pattern used elsewhere

        if self.backend == "aistudio":
            if not self.api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY not set. Get a free key at https://aistudio.google.com/apikey"
                )
            self._client = genai.Client(api_key=self.api_key)
        elif self.backend == "vertexai":
            if not self.project:
                raise RuntimeError("GOOGLE_CLOUD_PROJECT not set — required for the vertexai backend.")
            self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        else:
            raise RuntimeError(f"Unknown backend: {self.backend!r}")
        return self._client

    # ---- mock backend -------------------------------------------------------

    def _mock_check(self, return_id: str, scenario: dict[str, Any]) -> FraudCheckResult:
        """Dispatches on scenario_type. Absent or "return" -> the existing
        return-fraud heuristic (byte-identical to before this dispatcher
        was added — the 5 original scenarios don't set scenario_type, so
        their behavior is untouched). "cod_refusal" -> a genuinely
        different heuristic: a COD refusal happens before the item is
        ever accepted, so return-specific checks like wardrobing (days
        held, single-use category) don't apply, and the dominant cost to
        the seller is wasted shipping/courier effort rather than item
        value — a different economic shape than a return, not a variant
        of one."""
        if scenario.get("scenario_type") == "cod_refusal":
            return self._mock_check_cod_refusal(return_id, scenario)
        return self._mock_check_return(return_id, scenario)

    def _mock_check_return(self, return_id: str, scenario: dict[str, Any]) -> FraudCheckResult:
        """Rule-based heuristic standing in for Gemini reasoning — offline,
        deterministic, no network. Deliberately NOT cycled through a prose
        fallback format like catalog_agent_mock.py: the fallback parser's
        field aliases are tuned for catalog fields (name/price/keywords),
        not fraud fields (risk_level/reasoning/signals), so a fake prose
        response here would just fail to parse and prove nothing useful.
        The fallback parser's prose recovery is already covered by its own
        dedicated tests in test_fallback_parser.py."""
        signals: list[str] = []

        orders = scenario.get("customer_total_orders_last_90_days", 0) or 0
        returns = scenario.get("customer_total_returns_last_90_days", 0) or 0
        return_rate = (returns / orders) if orders else 0

        if return_rate >= 0.5 and orders >= 3:
            signals.append("high personal return rate")

        reason = str(scenario.get("return_reason", "")).lower()
        price = scenario.get("product_price", 0) or 0
        currency = scenario.get("currency")
        high_value_threshold = HIGH_VALUE_THRESHOLD_BY_CURRENCY.get(currency, DEFAULT_HIGH_VALUE_THRESHOLD)
        if any(p in reason for p in VAGUE_REASON_PHRASES) and price >= high_value_threshold:
            signals.append("vague return reason for a high-value item")

        notes = str(scenario.get("notes", "")).lower()
        if "multiple sizes" in notes or "multiple colors" in notes or "multiple colours" in notes:
            signals.append("bracketing pattern — multiple variants ordered together")

        days_held = None
        try:
            order_date = date.fromisoformat(str(scenario.get("order_date", "")))
            return_date = date.fromisoformat(str(scenario.get("return_requested_date", "")))
            days_held = (return_date - order_date).days
        except (ValueError, TypeError):
            pass

        product_name = str(scenario.get("product_name", "")).lower()
        if days_held is not None and days_held <= 5 and any(c in product_name for c in SINGLE_USE_CATEGORIES):
            signals.append("rapid return on a likely single-wear item — possible wardrobing")

        reasoning = (
            f"{len(signals)} fraud signal(s) detected: {'; '.join(signals)}."
            if signals
            else "No notable fraud signals detected in this return request."
        )
        return self._finalize_mock_result(return_id, signals, reasoning)

    def _mock_check_cod_refusal(self, case_id: str, scenario: dict[str, Any]) -> FraudCheckResult:
        """COD refusal / undelivered-order risk. Frequency is the
        dominant signal here, not item value: every refusal costs the
        seller a broadly similar shipping/courier cost regardless of
        price, unlike a return where the item's value is what's at risk
        — so this deliberately does NOT reuse HIGH_VALUE_THRESHOLD_BY_
        CURRENCY the way the return check does. product_price/currency
        are still recorded on each scenario for realism and possible
        future use, just not wired into today's signal logic."""
        signals: list[str] = []

        cod_orders = scenario.get("customer_total_cod_orders_last_90_days", 0) or 0
        cod_refusals = scenario.get("customer_total_cod_refusals_last_90_days", 0) or 0
        refusal_rate = (cod_refusals / cod_orders) if cod_orders else 0

        if refusal_rate >= 0.5 and cod_orders >= 3:
            signals.append("high COD refusal/non-delivery rate")

        notes = str(scenario.get("notes", "")).lower()
        if any(p in notes for p in ("multiple sizes", "multiple colors", "multiple colours", "multiple variants")):
            signals.append("COD bracketing — multiple variants ordered, most refused at delivery")

        delivery_outcome = str(scenario.get("delivery_outcome", "")).lower()
        if delivery_outcome == "customer_unreachable" and refusal_rate >= 0.5 and cod_orders >= 3:
            signals.append("repeated delivery-unreachable pattern — possible fake or prank orders")

        reasoning = (
            f"{len(signals)} COD fraud signal(s) detected: {'; '.join(signals)}."
            if signals
            else "No notable COD fraud signals detected in this order history."
        )
        return self._finalize_mock_result(case_id, signals, reasoning)

    def _finalize_mock_result(self, case_id: str, signals: list[str], reasoning: str) -> FraudCheckResult:
        """Shared by both mock heuristics: risk-level bucketing, JSON-
        format cycling (clean vs. markdown-fenced, to exercise more than
        one fallback_parser layer across a batch), and result assembly —
        factored out so the return and COD paths can't drift apart in
        this shared plumbing."""
        if len(signals) >= 2:
            risk_level = "high"
        elif len(signals) == 1:
            risk_level = "medium"
        else:
            risk_level = "low"

        payload = {"risk_level": risk_level, "reasoning": reasoning, "signals": signals}
        style = self._call_count % 2
        self._call_count += 1
        raw_text = json.dumps(payload) if style == 0 else "```json\n" + json.dumps(payload, indent=2) + "\n```"

        parsed = self.parser.parse(raw_text)
        return FraudCheckResult(
            return_id=case_id,
            risk_level=risk_level,
            flagged=risk_level != "low",
            reasoning=reasoning,
            signals=signals,
            extraction_method=parsed.extraction_method,
            raw_model_output=raw_text,
            warnings=parsed.warnings,
        )
        
        