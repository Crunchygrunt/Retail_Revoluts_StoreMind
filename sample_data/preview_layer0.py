"""
Runs ONLY the Layer-0 validator against the sample messy catalog — no GCP
credentials needed. Useful to sanity-check the validator on its own, and to
generate the JSON body for POST /ingest.

Usage:
    python3 preview_layer0.py
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from layer0_validator import Layer0Validator  # noqa: E402

CSV_PATH = Path(__file__).parent / "messy_catalog.csv"


def load_rows() -> list[dict]:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    rows = load_rows()
    validator = Layer0Validator()
    accepted, rejected = validator.validate_batch(rows)

    print(f"Loaded {len(rows)} rows from {CSV_PATH.name}\n")

    print(f"ACCEPTED ({len(accepted)}):")
    for r in accepted:
        print(f"  {r.row_id:10s} -> {r.cleaned_row['name']!r} @ {r.cleaned_row['price']}")

    print(f"\nREJECTED ({len(rejected)}):")
    for r in rejected:
        print(f"  {r.row_id:10s} -> {'; '.join(r.reasons)}")

    # Write the JSON body ready for POST /ingest (accepted rows are re-derived
    # server-side from the raw rows too, so we just pass the raw rows through —
    # /ingest re-runs Layer-0 itself, this file is only for eyeballing results).
    out_path = Path(__file__).parent / "ingest_request_body.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"rows": rows}, f, indent=2)
    print(f"\nWrote ingest request body -> {out_path.name}")
    print('Try it against a deployed/local service with:')
    print(f'  curl -X POST http://localhost:8080/ingest -H "Content-Type: application/json" -d @{out_path.name}')


if __name__ == "__main__":
    main()
