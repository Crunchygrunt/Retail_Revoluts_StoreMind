"""
One-off backfill: computes and stores ucp_tier / is_ucp_catalog_listable /
is_ucp_checkout_eligible / ucp_blockers on every product ALREADY in the
store, so the Products page's UCP column shows real data immediately
rather than only for products ingested after ucp_eligibility.py existed.

REAL RISK THIS SCRIPT IS WRITTEN AROUND, not discovered after the fact:
firestore_client.write_structured_product() writes with merge=True, but
it ALSO calls doc.setdefault("readiness_score", None) and
doc.setdefault("gate_status", "pending") before writing. If this script
called that function with a dict containing ONLY the four new UCP
fields, those setdefault calls would inject readiness_score=None and
gate_status="pending" into the write payload — and because the write is
a merge, that would silently overwrite every product's REAL readiness
score and gate status back to their defaults. This script avoids that
entirely by reading each product's FULL existing document first and
adding the four new fields to it, so every setdefault call is a no-op
(the keys are already present with their real values) before the
complete dict is written back.

Run once, from the app/ directory, in the same environment main.py
already uses (STOREMIND_STORE_BACKEND=firestore and whatever GCP
credentials that already implies):

    python3 backfill_ucp_eligibility.py

Safe to re-run — it recomputes and overwrites only the four UCP fields
each time (via the full-dict-passthrough above), never accumulates or
duplicates anything. Also safe to run with STOREMIND_STORE_BACKEND=local
for an offline sanity check before touching the real Firestore data.
"""

from __future__ import annotations

import os
import sys

# REAL BUG THIS FIXES, found via a live run: main.py calls
# load_dotenv(override=True) before reading STOREMIND_STORE_BACKEND —
# that's how it picks up "firestore" from .env. This script originally
# skipped that call entirely, so os.environ.get(..., "local") silently
# fell through to "local" regardless of .env's real content. A live run
# confirmed this exactly: it found 26 unrelated local-store fixture
# products (SKU-001, ST-001..024 — not the real catalog's
# BRAND-SELLERNAME-01-style SKUs) instead of the real Firestore catalog.
# No real data was touched by that run; this fixes the script so the
# next run actually reaches Firestore.
from dotenv import load_dotenv

load_dotenv("deploy.env", override=True)

from ucp_eligibility import derive_ucp_eligibility

STORE_BACKEND = os.environ.get("STOREMIND_STORE_BACKEND", "local")
# Same "cap rather than assume unbounded" precedent already set by
# currency_inference.py's QUERY_LIMIT and ucp_catalog.py's
# _MAX_CATALOG_SCAN — override via env var if the real catalog is larger.
BATCH_LIMIT = int(os.environ.get("STOREMIND_BACKFILL_LIMIT", "1000"))


def _build_store():
    # Same selection logic as main.py's _build_store() — kept in sync
    # deliberately rather than importing main.py itself, since main.py
    # also stands up the FastAPI app, Gemini agent, image store, etc. on
    # import, none of which this one-off script needs or should trigger.
    if STORE_BACKEND == "firestore":
        import firestore_client as store
    else:
        import local_store as store
    return store


def main() -> None:
    store = _build_store()
    print(f"Using store backend: '{STORE_BACKEND}' (from STOREMIND_STORE_BACKEND, .env loaded).")
    if STORE_BACKEND != "firestore":
        print(
            "WARNING: this is NOT firestore — if you meant to backfill the real deployed "
            "catalog, set STOREMIND_STORE_BACKEND=firestore in .env (or in this shell) and "
            "re-run. Continuing against the local store as configured.",
            file=sys.stderr,
        )
    products = store.list_products(limit=BATCH_LIMIT)
    print(f"Found {len(products)} product(s) in the '{STORE_BACKEND}' store.")

    updated = 0
    skipped = 0
    for product in products:
        sku = product.get("sku")
        if not sku:
            print(f"  SKIP: product with no sku — {product!r}", file=sys.stderr)
            skipped += 1
            continue

        result = derive_ucp_eligibility(product)
        # Mutating the FULL existing dict, not a fresh partial one — see
        # module docstring for exactly why this matters.
        product["ucp_tier"] = result.ucp_tier
        product["is_ucp_catalog_listable"] = result.is_ucp_catalog_listable
        product["is_ucp_checkout_eligible"] = result.is_ucp_checkout_eligible
        product["ucp_blockers"] = result.ucp_blockers

        extraction_method = product.get("extraction_method", "unknown")
        store.write_structured_product(product, sku=sku, extraction_method=extraction_method)
        updated += 1
        print(f"  {sku}: ucp_tier={result.ucp_tier}")

    print(f"\nDone. {updated} updated, {skipped} skipped.")

    if len(products) >= BATCH_LIMIT:
        print(
            f"WARNING: hit the {BATCH_LIMIT}-product limit — there may be more products "
            f"than this run saw. Re-run with STOREMIND_BACKFILL_LIMIT set higher.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
    
    