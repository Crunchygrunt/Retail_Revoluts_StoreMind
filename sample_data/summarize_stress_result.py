"""
Summarizes stress_ingest_response.json (the real /ingest response, saved
via `curl.exe ... -o stress_ingest_response.json`) into something readable
— 50 rows of raw JSON in a terminal is not a usable result.

Usage:
    python3 summarize_stress_result.py
"""

import json
from collections import Counter
from pathlib import Path

RESPONSE_PATH = Path(__file__).parent / "stress_ingest_response.json"

# The rows worth specifically calling out — the whole point of this run
# was testing real Gemini against genuine non-Latin product names.
WATCH_LIST = {
    "ST-043": "Hindi name",
    "ST-044": "Chinese name",
    "ST-045": "emoji in name",
}


def main() -> None:
    if not RESPONSE_PATH.exists():
        print(f"{RESPONSE_PATH.name} not found — run the curl command with -o {RESPONSE_PATH.name} first.")
        return

    with open(RESPONSE_PATH, encoding="utf-8") as f:
        data = json.load(f)

    print(f"llm_backend: {data.get('llm_backend')}   store_backend: {data.get('store_backend')}")
    print(f"submitted: {data.get('submitted')}   layer0_accepted: {data.get('layer0_accepted')}   layer0_rejected: {data.get('layer0_rejected')}")

    results = data.get("structuring_results", [])
    method_counts = Counter(r["extraction_method"] for r in results)
    print(f"\nextraction methods: {dict(method_counts)}")

    scores = [r["readiness_score"] for r in results if r.get("success") and r.get("readiness_score") is not None]
    if scores:
        print(f"score range: {min(scores):.1f} - {max(scores):.1f}   avg: {sum(scores) / len(scores):.1f}")

    failed = [r for r in results if not r.get("success")]
    if failed:
        print(f"\nstructuring FAILURES ({len(failed)}):")
        for r in failed:
            print(f"  {r['row_id']:10s} {r['warnings']}")

    below = sorted(
        (r for r in results if r.get("success") and r.get("meets_readiness_threshold") is False),
        key=lambda r: r["readiness_score"],
    )
    print(f"\nbelow threshold ({len(below)}):")
    for r in below:
        print(f"  {r['row_id']:10s} score={r['readiness_score']:<6} {r['readiness_notes']}")

    print("\n--- multilingual watch list ---")
    by_id = {r["row_id"]: r for r in results}
    for sku, label in WATCH_LIST.items():
        if sku in by_id:
            r = by_id[sku]
            status = "OK" if r["success"] else "FAILED"
            score = r.get("readiness_score")
            print(f"  {sku} ({label}): {status}, score={score}, extraction={r.get('extraction_method')}, notes={r.get('readiness_notes')}")
        else:
            reasons = next((rej["reasons"] for rej in data.get("rejections", []) if rej["row_id"] == sku), None)
            if reasons:
                print(f"  {sku} ({label}): REJECTED at Layer-0 — {reasons}")
            else:
                print(f"  {sku} ({label}): not found in response at all — check it was in the submitted batch")


if __name__ == "__main__":
    main()
