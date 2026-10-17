"""
Converts stress_test_catalog.csv into the JSON body POST /ingest expects.
Same pattern as preview_layer0.py, just pointed at the bigger catalog.

Usage:
    python3 generate_stress_ingest_body.py
"""

import csv
import json
from pathlib import Path

CSV_PATH = Path(__file__).parent / "stress_test_catalog.csv"
OUT_PATH = Path(__file__).parent / "stress_ingest_body.json"


def main() -> None:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"rows": rows}, f, indent=2, ensure_ascii=False)

    print(f"Loaded {len(rows)} rows from {CSV_PATH.name}")
    print(f"Wrote {OUT_PATH.name}")
    print('\nSend it with:')
    print(f'  curl.exe -X POST http://localhost:8080/ingest -H "Content-Type: application/json" -d "@{OUT_PATH.name}" -o stress_ingest_response.json')


if __name__ == "__main__":
    main()
