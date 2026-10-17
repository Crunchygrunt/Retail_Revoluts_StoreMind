"""
Replays exactly what POST /ingest does, calling the same modules main.py
calls, but without needing fastapi/uvicorn installed. Useful for verifying
pipeline logic in any environment, and for a scripted demo rehearsal that
doesn't depend on the HTTP layer at all.

Usage:
    python3 run_offline_pipeline.py
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from layer0_validator import Layer0Validator  # noqa: E402
from catalog_agent_mock import MockCatalogStructuringAgent  # noqa: E402
from readiness_score import compute_readiness_score  # noqa: E402
from storefront import search_products, checkout  # noqa: E402
import local_store  # noqa: E402

CSV_PATH = Path(__file__).parent / "messy_catalog.csv"


def main() -> None:
    local_store.reset()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    validator = Layer0Validator()
    accepted, rejected = validator.validate_batch(rows)

    agent = MockCatalogStructuringAgent()
    cleaned_rows = [r.cleaned_row for r in accepted]
    structuring_results = agent.structure_batch(cleaned_rows)

    print(f"submitted:        {len(rows)}")
    print(f"layer0_accepted:  {len(accepted)}")
    print(f"layer0_rejected:  {len(rejected)}\n")

    print("rejections:")
    for r in rejected:
        print(f"  {r.row_id:10s} {'; '.join(r.reasons)}")

    print("\nstructuring results (note extraction_method varies — proves the")
    print("fallback parser layers are all genuinely exercised, not decorative):")
    method_counts: dict[str, int] = {}
    for cleaned_row, result in zip(cleaned_rows, structuring_results):
        method_counts[result.extraction_method] = method_counts.get(result.extraction_method, 0) + 1
        if result.success:
            score_result = compute_readiness_score(result.structured, result.extraction_method, raw_row=cleaned_row)
            result.structured["readiness_score"] = score_result.score
            result.structured["readiness_breakdown"] = score_result.breakdown
            result.structured["meets_readiness_threshold"] = score_result.meets_threshold
            local_store.write_structured_product(
                result.structured, sku=result.row_id, extraction_method=result.extraction_method
            )
        status = "OK  " if result.success else "FAIL"
        score_str = f"score={result.structured['readiness_score']:.1f}" if result.success else ""
        print(f"  {status}  {result.row_id:10s} extraction={result.extraction_method:15s} {score_str} warnings={result.warnings}")

    for r in rejected:
        local_store.write_rejected_row(r.row_id, r.reasons, raw_row={})

    print(f"\nextraction method distribution: {method_counts}")

    stored = local_store.list_products()
    print(f"\nproducts now in local_store_data.json: {len(stored)}")
    for p in stored:
        print(f"  {p['sku']:10s} {p['name']!r:35s} ${p['price']:<8} score={p['readiness_score']:<6} category={p.get('category')}")

    assert len(stored) == len(accepted), "every Layer-0-accepted row should have landed in the store"
    assert len(method_counts) > 1, "expected multiple extraction methods to fire across the batch"

    # ---- simulate a shopping agent: search, then check out ----------------
    print("\n--- simulated shopping agent ---")
    print("agent searches for 'cotton'...")
    hits = search_products(local_store, query="cotton")
    for h in hits:
        print(f"  found: {h['sku']} {h['name']!r} (readiness_score={h['readiness_score']})")
    assert len(hits) >= 1, "expected the tote bag to be findable by 'cotton'"

    if hits:
        target = hits[0]
        print(f"\nagent checks out {target['sku']}...")
        result = checkout(local_store, target["sku"], quantity=1)
        print(f"  checkout result: {result}")
        assert result["success"], "checkout should succeed — this product scored above threshold"

    # ---- prove the gate actually blocks something -------------------------
    print("\n--- fail-closed gate check ---")
    low_scorers = [p for p in stored if p["readiness_score"] < 70.0]
    gate_proved = False
    if low_scorers:
        blocked_sku = low_scorers[0]["sku"]
        print(f"attempting checkout on {blocked_sku} (score={low_scorers[0]['readiness_score']}, below threshold)...")
        blocked_result = checkout(local_store, blocked_sku, quantity=1)
        print(f"  checkout result: {blocked_result}")
        assert not blocked_result["success"], "checkout should be BLOCKED for a below-threshold product"
        gate_proved = True
        print("  BLOCKED as expected — the gate is enforcing, not just displaying, the score.")
    else:
        print("  no product in this batch scored below threshold — nothing to prove the gate against.")
        print("  (this catalog is expected to always include SKU-010, a deliberately thin product,")
        print("   specifically so this branch doesn't happen — if you're seeing this, check messy_catalog.csv)")

    print("\ndecision log:")
    for entry in local_store.list_decision_log():
        print(f"  {entry['timestamp']}  {entry['product_sku']:10s} {entry['event_type']:15s} {entry['reason']}")

    print("\nPASS — offline pipeline produced a consistent, verifiable result end to end,")
    print("including a simulated agent finding a product and checking it out.")
    if gate_proved:
        print("The fail-closed gate was also proven to block a below-threshold product.")
    else:
        print("WARNING: the fail-closed gate was NOT exercised this run — no product scored")
        print("below threshold. Treat this run as incomplete verification of the gate.")


if __name__ == "__main__":
    main()
