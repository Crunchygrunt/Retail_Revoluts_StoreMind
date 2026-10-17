"""
Runs the Fraud Signal Detector's test harness directly — no server, no
network. Same pattern as run_offline_pipeline.py.

Reads app/.env automatically — set STOREMIND_LLM_BACKEND there (mock |
aistudio | vertexai) instead of exporting it manually each run.

Usage:
    python3 run_fraud_test_harness.py
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "app" / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from fraud_detector import FraudSignalDetector  # noqa: E402

SCENARIOS_PATH = Path(__file__).resolve().parents[1] / "app" / "fraud_scenarios.json"


def main() -> None:
    with open(SCENARIOS_PATH, encoding="utf-8") as f:
        scenarios = json.load(f)

    detector = FraudSignalDetector()
    print(f"backend: {detector.backend}\n")

    report = detector.run_test_harness(scenarios)

    for r in report["results"]:
        verdict = "FLAGGED" if r["flagged"] else "clear  "
        correct = "correct" if r["correct"] else "WRONG"
        print(f"  {r['return_id']:10s} {verdict}  risk={r['risk_level']:6s} ground_truth_flag={r['ground_truth_flag']!s:6s} [{correct}]")
        print(f"             reasoning: {r['reasoning']}")
        if r["signals"]:
            print(f"             signals: {', '.join(r['signals'])}")

    print(f"\naccuracy: {report['correct']}/{report['scored']} ({report['accuracy']:.0%})")

    if report["accuracy"] == 1.0:
        print("PASS — all prepared scenarios scored correctly.")
    else:
        print("Some scenarios scored incorrectly — worth digging into before the demo.")


if __name__ == "__main__":
    main()
    