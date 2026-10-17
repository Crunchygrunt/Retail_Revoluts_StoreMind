"""
Tests for ucp_routes.py using FastAPI's TestClient — the automated
counterpart to the manual curl pass already run against the live
deployment (create/get/update/complete/cancel, idempotency replay,
idempotency conflict — all confirmed working against real production
traffic). This exercises the same paths locally and in-process, so a
future change to ucp_routes.py gets caught by `pytest` without needing
a live deployment to test against.

*** UNVERIFIED BY CLAUDE. *** No fastapi/httpx were available in the
sandbox this was written in, so — unlike every other test file this
session — this one has never actually been executed anywhere. It's
written against TestClient's ordinary, long-stable usage (construct a
plain FastAPI() app, mount the router, drive it with plain
get/post/put calls), and confidence is high given the manual pass just
confirmed the underlying route logic works live. High confidence is
not the same claim as verified, though — run this for real before
trusting it the way the rest of this session's test files can be
trusted.

NOTE on shared state: ucp_routes.py's idempotency cache is a
MODULE-LEVEL dict, not reset between tests in the same pytest process
(the `client` fixture below gives each test a fresh FakeStore /
FakeSessionStore, but NOT a fresh idempotency cache — that's real
production behavior, not a test artifact, and mirrors the actual
Cloud Run instance's in-memory cache persisting across requests). Every
Idempotency-Key used below is a distinct string for exactly this
reason — reusing a key across two unrelated tests would make the
second one see the first one's cached result.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ucp_routes import build_ucp_router


class FakeStore:
    def __init__(self, products):
        self._products = {p["sku"]: p for p in products}
        self.decisions = []

    def list_products(self, limit=50):
        return list(self._products.values())[:limit]

    def get_product(self, sku):
        return self._products.get(sku)

    def log_decision(self, sku, event_type, reason):
        self.decisions.append((sku, event_type, reason))


class FakeSessionStore:
    """
    Matches BOTH firestore_client.py additions this project now has:
    write_ucp_session/get_ucp_session (sessions) AND
    write_idempotency_record/get_idempotency_record (idempotency). The
    real ucp_routes.py switched idempotency storage from an in-memory
    dict to session_store-backed persistence after this test file was
    first written — this fake was missing the second half of that
    interface, which is exactly what a live pytest run just caught.
    """

    def __init__(self):
        self._sessions = {}
        self._idempotency = {}

    def write_ucp_session(self, session):
        self._sessions[session["id"]] = dict(session)

    def get_ucp_session(self, session_id):
        s = self._sessions.get(session_id)
        return dict(s) if s else None

    def write_idempotency_record(self, key, fingerprint, status_code, body):
        self._idempotency[key] = {
            "fingerprint": fingerprint,
            "status_code": status_code,
            "body": body,
        }

    def get_idempotency_record(self, key):
        record = self._idempotency.get(key)
        return dict(record) if record else None


ELIGIBLE = {
    "sku": "KURTA-001",
    "name": "Premium Cotton Kurta",
    "description": "Hand-stitched cotton kurta.",
    "price": 1499.0,
    "currency": "INR",
    "availability": "in_stock",
    "readiness_score": 85.0,
    "meets_readiness_threshold": True,
    "url": "https://example.com/products/KURTA-001",
    "image_url": "https://example.com/kurta.jpg",
    "category": "Apparel",
    "keywords": ["kurta"],
}


@pytest.fixture
def client():
    store = FakeStore([ELIGIBLE])
    session_store = FakeSessionStore()
    app = FastAPI()
    app.include_router(build_ucp_router(store, session_store))
    return TestClient(app)


# --- profile ---


def test_get_profile(client):
    resp = client.get("/.well-known/ucp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ucp"]["version"] == "2026-04-08"
    assert "dev.ucp.shopping.checkout" in body["ucp"]["capabilities"]
    # DISC-003, fixed via a real conformance-test deviation this session.
    assert resp.headers["cache-control"] == "public, max-age=300"


# --- catalog ---


def test_catalog_search_finds_product(client):
    resp = client.post("/ucp/v1/catalog/search", json={"query": "kurta"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["products"]) == 1
    assert body["products"][0]["id"] == "KURTA-001"


def test_catalog_search_rejects_empty_request(client):
    """CAT-009/010, fixed via a real conformance-test deviation this session."""
    resp = client.post("/ucp/v1/catalog/search", json={"query": ""})
    assert resp.status_code == 400
    assert resp.json()["messages"][0]["code"] == "missing_search_input"


def test_catalog_lookup_partial_success(client):
    resp = client.post("/ucp/v1/catalog/lookup", json={"ids": ["KURTA-001", "GHOST"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["products"]) == 1
    assert any(m["code"] == "not_found" for m in body["messages"])


def test_catalog_get_product_found(client):
    resp = client.post("/ucp/v1/catalog/product", json={"id": "KURTA-001"})
    assert resp.status_code == 200
    assert "product" in resp.json()


def test_catalog_get_product_not_found_is_business_outcome_not_404(client):
    """
    Deliberately different from checkout's not-found handling (see
    test_checkout_get_not_found_is_404 below) — confirmed real spec
    difference, not an inconsistency: catalog's not-found is an
    application-level 200 + ucp.status:"error", checkout's is a
    genuine transport 404.
    """
    resp = client.post("/ucp/v1/catalog/product", json={"id": "GHOST"})
    assert resp.status_code == 200
    assert resp.json()["ucp"]["status"] == "error"


# --- checkout: full lifecycle, mirroring the manual curl pass exactly ---


def test_checkout_full_lifecycle(client):
    create_resp = client.post(
        "/ucp/v1/checkout-sessions",
        json={
            "line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}],
            "buyer": {"email": "a@b.com"},
        },
        headers={"Idempotency-Key": "lifecycle-test-key"},
    )
    assert create_resp.status_code == 201
    session = create_resp.json()
    assert session["status"] == "ready_for_complete"
    assert "payment_handlers" in session["ucp"]  # PAY-003
    session_id = session["id"]

    get_resp = client.get(f"/ucp/v1/checkout-sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == session_id

    update_resp = client.put(
        f"/ucp/v1/checkout-sessions/{session_id}",
        json={
            "line_items": [{"item": {"id": "KURTA-001"}, "quantity": 2}],
            "buyer": {"email": "a@b.com"},
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["totals"][0]["amount"] == 149900 * 2

    complete_resp = client.post(
        f"/ucp/v1/checkout-sessions/{session_id}/complete",
        json={
            "payment": {
                "instruments": [{"id": "instr_1", "handler_id": "com.google.pay", "selected": True}]
            }
        },
    )
    assert complete_resp.status_code == 200
    completed = complete_resp.json()
    assert completed["status"] == "completed"
    assert "order" in completed and "id" in completed["order"]


def test_checkout_get_not_found_is_404(client):
    resp = client.get("/ucp/v1/checkout-sessions/GHOST-SESSION")
    assert resp.status_code == 404


def test_checkout_missing_line_items_is_400(client):
    """checkout.create_requires_line_items, fixed this session — distinct
    from the confirmed-correct 200+error path for a well-formed-but-
    ineligible request (see test_checkout_all_items_blocked_is_200 below)."""
    resp = client.post("/ucp/v1/checkout-sessions", json={})
    assert resp.status_code == 400
    assert resp.json()["messages"][0]["code"] == "missing_line_items"


def test_checkout_all_items_blocked_is_200_not_400(client):
    """
    The posture independently confirmed correct by a merged fix to the
    OFFICIAL UCP conformance suite itself (see ucp_session.py's module
    docstring VERSION NOTE) — do not "fix" this back to a 400.
    """
    resp = client.post(
        "/ucp/v1/checkout-sessions",
        json={"line_items": [{"item": {"id": "GHOST-PRODUCT"}, "quantity": 1}]},
    )
    assert resp.status_code == 200
    assert resp.json()["ucp"]["status"] == "error"


def test_checkout_cancel(client):
    create_resp = client.post(
        "/ucp/v1/checkout-sessions",
        json={"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}]},
    )
    session_id = create_resp.json()["id"]
    cancel_resp = client.post(f"/ucp/v1/checkout-sessions/{session_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "canceled"


def test_checkout_cancel_after_complete_is_rejected(client):
    create_resp = client.post(
        "/ucp/v1/checkout-sessions",
        json={
            "line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}],
            "buyer": {"email": "a@b.com"},
        },
    )
    session_id = create_resp.json()["id"]
    client.post(
        f"/ucp/v1/checkout-sessions/{session_id}/complete",
        json={"payment": {"instruments": []}},
    )
    cancel_resp = client.post(f"/ucp/v1/checkout-sessions/{session_id}/cancel")
    assert cancel_resp.status_code == 200  # business rule outcome, not a transport error
    assert cancel_resp.json()["status"] == "completed"  # not overwritten
    assert any(m["code"] == "already_completed" for m in cancel_resp.json()["messages"])


# --- idempotency: the two behaviors confirmed live, now covered locally ---


def test_idempotency_replay_returns_cached_result(client):
    body = {
        "line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}],
        "buyer": {"email": "x@y.com"},
    }
    headers = {"Idempotency-Key": "replay-test-key"}
    first = client.post("/ucp/v1/checkout-sessions", json=body, headers=headers)
    second = client.post("/ucp/v1/checkout-sessions", json=body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]  # same session, not reprocessed


def test_idempotency_conflict_on_mismatched_reuse(client):
    """
    CHK-048 / IDM-004 — the fix this session's conformance testing
    actually caught: the cache must compare the request body, not just
    replay on the key alone.
    """
    headers = {"Idempotency-Key": "conflict-test-key"}
    first = client.post(
        "/ucp/v1/checkout-sessions",
        json={"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}]},
        headers=headers,
    )
    second = client.post(
        "/ucp/v1/checkout-sessions",
        json={"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 5}]},  # different body, same key
        headers=headers,
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["messages"][0]["code"] == "idempotency_key_conflict"


def test_idempotency_on_complete_endpoint_too(client):
    """The OTHER state-mutating POST the spec's Idempotency-Key
    requirement applies to — not just create."""
    create_resp = client.post(
        "/ucp/v1/checkout-sessions",
        json={
            "line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}],
            "buyer": {"email": "a@b.com"},
        },
    )
    session_id = create_resp.json()["id"]

    complete_body = {
        "payment": {"instruments": [{"id": "instr_1", "handler_id": "com.google.pay", "selected": True}]}
    }
    headers = {"Idempotency-Key": "complete-replay-key"}
    first = client.post(f"/ucp/v1/checkout-sessions/{session_id}/complete", json=complete_body, headers=headers)
    second = client.post(f"/ucp/v1/checkout-sessions/{session_id}/complete", json=complete_body, headers=headers)
    assert first.json()["order"]["id"] == second.json()["order"]["id"]


def test_missing_idempotency_key_does_not_crash(client):
    """The header is spec-required, but a client omitting it must not
    break the server — same defensive posture as everything else here."""
    resp = client.post(
        "/ucp/v1/checkout-sessions",
        json={"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}]},
    )
    assert resp.status_code == 201
    