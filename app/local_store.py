"""
Local store — a drop-in stand-in for firestore_client.py.

Same functions, same signatures, so main.py can switch between this
and real Firestore with one environment variable. Backed by a single JSON
file on disk so /products persists across restarts, the same way it would
against real Firestore, without needing any GCP account, emulator, or
network access.

File written to: local_store_data.json (next to this file), or the path
in STOREMIND_LOCAL_STORE_PATH if set.

REFLEXION ADDITION: write_reflection / list_reflections, mirroring
firestore_client.py's own two new functions exactly — same shape, same
"own collection, category-scoped, unordered" reasoning. Backs
reflection_memory.KeywordReflectionMemory under the mock/local backend,
which is what makes the reflexion demo runnable fully offline.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).parent / "local_store_data.json"
STORE_PATH = Path(os.environ.get("STOREMIND_LOCAL_STORE_PATH", str(DEFAULT_PATH)))


def _load() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"products": {}, "rejected_rows": [], "decision_log": [], "reflections": []}
    with open(STORE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict[str, Any]) -> None:
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_structured_product(structured: dict[str, Any], sku: str, extraction_method: str) -> None:
    data = _load()
    doc = dict(structured)
    doc["sku"] = sku
    doc["extraction_method"] = extraction_method
    doc.setdefault("readiness_score", None)
    doc.setdefault("gate_status", "pending")
    doc["updated_at"] = datetime.datetime.utcnow().isoformat()
    data["products"][sku] = doc
    _save(data)


def write_rejected_row(row_id: str, reasons: list[str], raw_row: dict[str, Any]) -> None:
    data = _load()
    data["rejected_rows"].append(
        {
            "row_id": row_id,
            "reasons": reasons,
            "raw_row": raw_row,
            "rejected_at": datetime.datetime.utcnow().isoformat(),
        }
    )
    _save(data)


def list_products(limit: int = 50) -> list[dict[str, Any]]:
    """Confirmed real bug, not theoretical: this used to be
    list(data["products"].values())[:limit] — Python dicts preserve
    INSERTION order, so this returned the OLDEST products first, and
    updating an existing product doesn't move it in that order either
    (dict key reassignment isn't a reorder). A growing catalog would
    permanently bury anything ingested after the first `limit` products
    ever written, exactly mirroring the missing-order_by bug found in
    firestore_client.py's own list_products — same symptom, different
    mechanism, fixed the same way: sort by updated_at, newest first,
    THEN slice. A product with no updated_at at all (shouldn't happen —
    write_structured_product always sets it — but never assume) sorts
    to the end via the empty-string fallback, not the top.
    """
    data = _load()
    products = sorted(data["products"].values(), key=lambda p: p.get("updated_at") or "", reverse=True)
    return products[:limit]


def get_product(sku: str) -> dict[str, Any] | None:
    data = _load()
    return data["products"].get(sku)


def log_decision(product_sku: str, event_type: str, reason: str) -> None:
    data = _load()
    data["decision_log"].append(
        {
            "product_sku": product_sku,
            "event_type": event_type,
            "reason": reason,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    )
    _save(data)


def log_seller_event(event_type: str, reason: str) -> None:
    """Mirrors firestore_client.log_seller_event exactly — same shape,
    same collection, product_sku explicitly None. See that docstring for
    why this isn't a separate collection/schema."""
    data = _load()
    data["decision_log"].append(
        {
            "product_sku": None,
            "event_type": event_type,
            "reason": reason,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    )
    _save(data)


def list_decision_log(limit: int = 100) -> list[dict[str, Any]]:
    data = _load()
    return data["decision_log"][-limit:]


def write_reflection(entry: dict[str, Any]) -> None:
    """Mirrors firestore_client.write_reflection exactly — see that
    function's docstring for the full reasoning (own collection, not
    decision_log; caller has already applied the poisoning guard)."""
    data = _load()
    doc = dict(entry)
    doc.setdefault("created_at", datetime.datetime.utcnow().isoformat())
    data.setdefault("reflections", []).append(doc)
    _save(data)


def list_reflections(category: str, limit: int = 500) -> list[dict[str, Any]]:
    """Mirrors firestore_client.list_reflections exactly — category-
    scoped, unordered (the caller re-ranks by tag overlap itself)."""
    data = _load()
    matches = [r for r in data.get("reflections", []) if r.get("category") == category]
    return matches[:limit]


def reset() -> None:
    """Wipe local store — handy between demo rehearsals."""
    if STORE_PATH.exists():
        STORE_PATH.unlink()
        