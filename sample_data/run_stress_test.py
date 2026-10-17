"""
Stress-test runner — same pipeline as run_offline_pipeline.py, but against
the wider, messier stress_test_catalog.csv, with a categorized summary
instead of a row-by-row printout (73 rows is too many to eyeball).

Uses a SEPARATE local store file so it doesn't clobber local_store_data.json
from the smaller demo catalog.

Usage:
    python3 run_stress_test.py
"""

import csv
import os
import sys
from collections import Counter
from pathlib import Path

STORE_PATH = Path(__file__).parent / "stress_test_store.json"
os.environ["STOREMIND_LOCAL_STORE_PATH"] = str(STORE_PATH)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from layer0_validator import Layer0Validator  # noqa: E402
from catalog_agent_mock import MockCatalogStructuringAgent  # noqa: E402
from readiness_score import compute_readiness_score  # noqa: E402
from storefront import search_products, checkout  # noqa: E402
import local_store  # noqa: E402

CSV_PATH = Path(__file__).parent / "stress_test_catalog.csv"


def categorize_reason(reasons: list[str]) -> str:
    """Buckets a rejection into one category for the summary, using the
    first reason that matches a known pattern."""
    joined = " ".join(reasons).lower()
    if "duplicate sku" in joined:
        return "duplicate_sku"
    if "missing required field: name" in joined or "name is empty" in joined:
        return "missing_name"
    if "missing required field: price" in joined:
        return "missing_price"
    if "not a parseable number" in joined:
        return "unparseable_price"
    if "must be positive" in joined:
        return "non_positive_price"
    if "exceeds" in joined and "name" in joined:
        return "name_too_long"
    if "encoding" in joined:
        return "mojibake"
    if "is not a record" in joined:
        return "empty_row"
    return "other"


def main() -> None:
    local_store.reset()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Loaded {len(rows)} rows from {CSV_PATH.name}\n")

    validator = Layer0Validator()
    accepted, rejected = validator.validate_batch(rows)

    print("=== Layer-0 summary ===")
    print(f"accepted: {len(accepted)}")
    print(f"rejected: {len(rejected)}")

    reason_counts = Counter(categorize_reason(r.reasons) for r in rejected)
    print("\nrejection reasons breakdown:")
    for category, count in reason_counts.most_common():
        print(f"  {category:20s} {count}")

    print("\n=== structuring + scoring (mock LLM) ===")
    agent = MockCatalogStructuringAgent()
    cleaned_rows = [r.cleaned_row for r in accepted]
    structuring_results = agent.structure_batch(cleaned_rows)

    method_counts: Counter = Counter()
    scores: list[float] = []
    below_threshold: list[tuple[str, float]] = []
    crashes = 0

    for cleaned_row, result in zip(cleaned_rows, structuring_results):
        method_counts[result.extraction_method] += 1
        if not result.success:
            crashes += 1
            continue
        try:
            score_result = compute_readiness_score(result.structured, result.extraction_method, raw_row=cleaned_row)
        except Exception as exc:  # noqa: BLE001 — this is exactly the kind of thing we're hunting for
            print(f"  SCORING CRASHED on {result.row_id}: {exc}")
            crashes += 1
            continue
        scores.append(score_result.score)
        if not score_result.meets_threshold:
            below_threshold.append((result.row_id, score_result.score))
        result.structured["readiness_score"] = score_result.score
        result.structured["readiness_breakdown"] = score_result.breakdown
        result.structured["meets_readiness_threshold"] = score_result.meets_threshold
        result.structured["gate_status"] = "eligible" if score_result.meets_threshold else "blocked"
        try:
            local_store.write_structured_product(
                result.structured, sku=result.row_id, extraction_method=result.extraction_method
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  STORE WRITE CRASHED on {result.row_id}: {exc}")
            crashes += 1

    print(f"extraction methods: {dict(method_counts)}")
    print(f"structuring/scoring crashes: {crashes}")
    if scores:
        print(f"score range: {min(scores):.1f} - {max(scores):.1f}, avg: {sum(scores) / len(scores):.1f}")
    print(f"products below threshold (70.0): {len(below_threshold)}")
    for sku, score in sorted(below_threshold, key=lambda x: x[1]):
        print(f"  {sku:10s} score={score}")

    print("\n=== storefront sanity check ===")
    search_crashed = False
    try:
        hits = search_products(local_store, query="wool")
        print(f"search 'wool' -> {len(hits)} hits")
    except Exception as exc:  # noqa: BLE001
        print(f"  SEARCH CRASHED: {exc}")
        search_crashed = True

    checkout_crashed = False
    if below_threshold:
        test_sku = below_threshold[0][0]
        try:
            result = checkout(local_store, test_sku, quantity=1)
            status = "BLOCKED (correct)" if not result["success"] else "PASSED (unexpected!)"
            print(f"checkout on below-threshold {test_sku}: {status}")
        except Exception as exc:  # noqa: BLE001
            print(f"  CHECKOUT CRASHED: {exc}")
            checkout_crashed = True

    print("\n=== summary ===")
    total_crashes = crashes + (1 if search_crashed else 0) + (1 if checkout_crashed else 0)
    if total_crashes == 0:
        print(f"PASS — {len(rows)} rows processed end to end with zero crashes.")
        print(f"Layer-0 correctly split {len(accepted)}/{len(rejected)} accepted/rejected.")
        print("Ready to re-run this same catalog against real Gemini via /ingest.")
    else:
        print(f"FOUND {total_crashes} CRASH(ES) — see above. Fix before running against real Gemini.")
        sys.exit(1)


if __name__ == "__main__":
    main()
