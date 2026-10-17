"""
Firestore access layer.

Single source of truth for the whole app. Every module writes here and
nothing else — the storefront and (week 2) the fail-closed gate only ever
READ from Firestore. That decoupling is what lets Fraud Detector and Ops
Copilot get cut under time pressure without touching the demo spine.

Collections:
    products/{sku}        structured product docs (this phase)
                           + readiness_score / gate_status (added week 2)
    decision_log/{auto}   append-only log of scoring/gate decisions (week 2)
    rejected_rows/{auto}  Layer-0 rejections, kept for the demo's "before" view
    reflections/{auto}    validated reflexion memory entries (see
                           reflection_memory.py) — cross-batch corrections
                           the structuring agent can draw on later, NOT
                           a decision audit trail (that's decision_log).


SECOND ADDITION to firestore_client.py — paste these two functions in
alongside write_ucp_session/get_ucp_session from the earlier addition,
plus a new collection note in the module docstring's Collections list:

    ucp_idempotency_keys/{key}   Persistent Idempotency-Key records for
                                  UCP checkout (see ucp_routes.py) —
                                  replaces the in-memory dict that
                                  didn't survive a Cloud Run restart or
                                  scale beyond one instance.

REQUIRES a one-time Firestore TTL policy on this collection's
expires_at field — see the setup steps in the accompanying chat
message. Without that policy, these records simply accumulate forever;
functionally harmless (the read-path expiry check below still treats
an old record as gone), but unbounded storage growth with no cleanup.

expires_at is written as a Python datetime — google-cloud-firestore
converts this to a native Firestore Timestamp automatically. This
MATTERS: Firestore's TTL policy only recognizes a real Timestamp field
as a valid TTL field, not a string or an epoch integer. Writing
datetime.utcnow().isoformat() here instead (an easy mistake, since
that's the pattern write_ucp_session already uses for created_at)
would silently make the TTL policy inapplicable to this field.


THIRD ADDITION (reflexion) — write_reflection / list_reflections,
backing reflection_memory.KeywordReflectionMemory. Own collection, not
folded into decision_log: these are cross-batch LEARNING material a
later actor prompt retrieves from, not a chronological audit trail a
demo narrates — a different access pattern (category-scoped lookup by
tag overlap, not "everything in time order") deserves its own
collection rather than overloading decision_log's shape.
"""


from __future__ import annotations

import datetime
from typing import Any

# Imported lazily inside functions so this module loads without the SDK
# installed, matching catalog_agent.py's pattern.


def _client():
    from google.cloud import firestore

    return firestore.Client()


def write_ucp_session(session: dict[str, Any]) -> None:
    """
    Writes a full UCP checkout session document. `session` must already
    contain "id" — this function does not generate one (ucp_session.py
    owns id generation, same separation as sku ownership in
    write_structured_product).
    """
    db = _client()
    doc = dict(session)
    doc["updated_at"] = datetime.datetime.utcnow().isoformat()
    db.collection("ucp_checkout_sessions").document(doc["id"]).set(doc)  # full replace, not merge — see module note


def get_ucp_session(session_id: str) -> dict[str, Any] | None:
    db = _client()
    doc = db.collection("ucp_checkout_sessions").document(session_id).get()
    return doc.to_dict() if doc.exists else None

def write_idempotency_record(
    key: str, fingerprint: str, status_code: int, body: dict[str, Any]
) -> None:
    db = _client()
    db.collection("ucp_idempotency_keys").document(key).set(
        {
            "fingerprint": fingerprint,
            "status_code": status_code,
            "body": body,
            "created_at": datetime.datetime.utcnow().isoformat(),
            # Real Timestamp type — see module docstring. 24h matches
            # CHK-048's own "store the key+result for >=24h" requirement.
            "expires_at": datetime.datetime.utcnow() + datetime.timedelta(hours=24),
        }
    )


def get_idempotency_record(key: str) -> dict[str, Any] | None:
    db = _client()
    doc = db.collection("ucp_idempotency_keys").document(key).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    # Defensive re-check even WITH the TTL policy enabled: Firestore's
    # TTL sweep isn't guaranteed to delete a document the instant it
    # expires — there's an unspecified delay. A record could still be
    # readable for some time after its own expires_at has passed.
    # Treating an expired-but-not-yet-swept record as absent here means
    # correctness never depends on the TTL sweep's exact timing — the
    # TTL policy is purely a storage-cleanup mechanism, not something
    # this function's correctness relies on.
    expires_at = data.get("expires_at")
    if expires_at is not None:
        now = datetime.datetime.now(datetime.timezone.utc)
        if expires_at < now:
            return None
    return data

def write_structured_product(structured: dict[str, Any], sku: str, extraction_method: str) -> None:
    db = _client()
    doc = dict(structured)
    doc["sku"] = sku
    doc["extraction_method"] = extraction_method
    doc.setdefault("readiness_score", None)
    doc.setdefault("gate_status", "pending")
    doc["updated_at"] = datetime.datetime.utcnow().isoformat()
    db.collection("products").document(sku).set(doc, merge=True)


def write_rejected_row(row_id: str, reasons: list[str], raw_row: dict[str, Any]) -> None:
    db = _client()
    db.collection("rejected_rows").add(
        {
            "row_id": row_id,
            "reasons": reasons,
            "raw_row": raw_row,
            "rejected_at": datetime.datetime.utcnow().isoformat(),
        }
    )


def list_products(limit: int = 50) -> list[dict[str, Any]]:
    """Confirmed real bug, not theoretical: this had no .order_by() at
    all — a plain unordered .limit(), which Firestore does NOT guarantee
    returns "most recent" anything. In practice this kept surfacing the
    same fixed slice of early test products (BRAND-*, CUR-*, sorted
    first by document ID) while anything ingested later, including
    generated row-... IDs that sort after them, was silently excluded
    from every /products response — not a write failure, a read-query
    bug. Fixed the same way list_decision_log already does it below:
    order by updated_at, newest first, then limit.

    updated_at is stored as an ISO-8601 string (see write_structured_product
    above), not a native Timestamp like ucp_idempotency_keys' expires_at
    — but ISO-8601's zero-padded format sorts correctly as a plain string
    too, so ordering by it directly is correct, not an approximation.
    """
    from google.cloud import firestore

    db = _client()
    docs = (
        db.collection("products")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [d.to_dict() for d in docs]


def get_product(sku: str) -> dict[str, Any] | None:
    db = _client()
    doc = db.collection("products").document(sku).get()
    return doc.to_dict() if doc.exists else None


def log_decision(product_sku: str, event_type: str, reason: str) -> None:
    """Stub for week 2 — kept here now so catalog_agent/main.py can already
    call it without a week-2 refactor. event_type: 'score_computed' |
    'gate_blocked' | 'gate_passed'."""
    db = _client()
    db.collection("decision_log").add(
        {
            "product_sku": product_sku,
            "event_type": event_type,
            "reason": reason,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    )


def log_seller_event(event_type: str, reason: str) -> None:
    """Seller/merchant-level decision-log entries — currently just GSTIN
    validation at startup. Same collection and shape as log_decision, but
    product_sku is explicitly None: these aren't about any single
    product, they're about whether the seller's own identity checked out
    at all. Kept in the SAME collection (not a separate one) so
    /decision-log's existing order-by-timestamp query surfaces these
    alongside product decisions without a second query or the frontend
    having to branch on two different shapes — event_type alone
    ('seller_gstin_valid' / 'seller_gstin_invalid') is enough to tell
    them apart."""
    db = _client()
    db.collection("decision_log").add(
        {
            "product_sku": None,
            "event_type": event_type,
            "reason": reason,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
    )


def list_decision_log(limit: int = 100) -> list[dict[str, Any]]:
    from google.cloud import firestore

    db = _client()
    docs = (
        db.collection("decision_log")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [d.to_dict() for d in docs]


def write_reflection(entry: dict[str, Any]) -> None:
    """Persists one validated reflexion memory entry (see
    reflection_memory.ReflectionEntry). The CALLER (reflection_memory.py)
    has already decided this entry is worth keeping — grounded (no
    fabrication rejected) AND measurably improved the readiness score —
    before this is ever called; this function just writes what it's
    handed, same division of responsibility as every other write_*
    function in this module."""
    db = _client()
    doc = dict(entry)
    doc.setdefault("created_at", datetime.datetime.utcnow().isoformat())
    db.collection("reflections").add(doc)


def list_reflections(category: str, limit: int = 500) -> list[dict[str, Any]]:
    """Category-scoped, like brand_resolution.infer_brand_by_category's
    own scoping — a Fashion reflection must never surface for a
    Kitchenware row. Deliberately NO order_by: KeywordReflectionMemory
    re-ranks whatever this returns by tag overlap, not recency, so an
    unordered bounded scan is correct here — not the missing-order_by
    bug already fixed twice elsewhere in this project (that bug was
    about silently dropping RECENT writes from a limited scan while the
    caller believed it was seeing "the whole catalog"; here the caller
    re-ranks the full returned set itself, so read order never affects
    which reflections end up chosen)."""
    db = _client()
    docs = db.collection("reflections").where("category", "==", category).limit(limit).stream()
    return [d.to_dict() for d in docs]

