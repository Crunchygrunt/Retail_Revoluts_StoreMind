"""
Tests for ucp_session.py, run against the REAL storefront.checkout()
gate and readiness_score.DEFAULT_THRESHOLD — not a reimplementation of
the gate logic. FakeStore/FakeSessionStore match firestore_client.py's
real interface (including the ADDITION in firestore_client_ADDITION.py)
exactly.
"""

import pytest
from ucp_session import create_session, get_session, update_session, complete_session, cancel_session


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
    def __init__(self):
        self._sessions = {}

    def write_ucp_session(self, session):
        self._sessions[session["id"]] = dict(session)

    def get_ucp_session(self, session_id):
        s = self._sessions.get(session_id)
        return dict(s) if s else None


ELIGIBLE = {
    "sku": "KURTA-001", "name": "Premium Cotton Kurta", "description": "Hand-stitched cotton kurta.",
    "price": 1499.0, "currency": "INR", "availability": "in_stock", "readiness_score": 85.0,
}
BELOW_THRESHOLD = {
    "sku": "THIN-001", "name": "Item", "description": "",
    "price": 199.0, "currency": "INR", "availability": "in_stock", "readiness_score": 40.0,
}
VND_ELIGIBLE = {
    "sku": "AODAI-001", "name": "Ao Dai", "description": "Vietnamese traditional dress in red silk.",
    "price": 250000.0, "currency": "VND", "availability": "in_stock", "readiness_score": 90.0,
}
BAD_CURRENCY = {
    "sku": "BADCUR-001", "name": "Unmapped Currency Item", "description": "desc",
    "price": 500.0, "currency": "Rs", "availability": "in_stock", "readiness_score": 95.0,
}
OUT_OF_STOCK = {
    "sku": "OOS-001", "name": "Sold Out Saree", "description": "Beautiful but gone.",
    "price": 2999.0, "currency": "INR", "availability": "out_of_stock", "readiness_score": 90.0,
}


@pytest.fixture
def store():
    return FakeStore([ELIGIBLE])


@pytest.fixture
def sstore():
    return FakeSessionStore()


# --- create_session ---

def test_create_single_item_no_buyer_email(store, sstore):
    status, body = create_session(store, sstore, {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}]})
    assert status == 201
    assert body["status"] == "incomplete"
    assert "id" in body
    assert body["currency"] == "INR"
    assert body["totals"][0]["amount"] == 149900
    assert any(m["code"] == "missing_buyer_info" for m in body["messages"])
    assert any(d[0] == "KURTA-001" and d[1] == "gate_passed" for d in store.decisions)
    assert "payment_handlers" in body["ucp"]  # PAY-003, echoed on every real session response


def test_create_missing_line_items_is_400(store, sstore):
    """
    checkout.create_requires_line_items: a request with no line_items
    at all is a malformed request, not a business outcome — distinct
    from test_create_all_items_blocked_is_unrecoverable below, which is
    a well-formed request that resolves to zero eligible items (that
    one stays 200 + ucp.status:"error", confirmed correct by a merged
    fix to the official conformance suite itself).
    """
    status, body = create_session(store, sstore, {})
    assert status == 400
    assert body["messages"][0]["code"] == "missing_line_items"
    assert "payment_handlers" not in body["ucp"]  # no resource in this response

    status2, body2 = create_session(store, sstore, {"line_items": []})
    assert status2 == 400


def test_create_with_buyer_email_ready_for_complete(store, sstore):
    _, body = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}], "buyer": {"email": "jane@example.com"}},
    )
    assert body["status"] == "ready_for_complete"
    assert not any(m["code"] == "missing_buyer_info" for m in body.get("messages", []))


def test_create_partial_exclusion_graceful_degradation():
    store = FakeStore([ELIGIBLE, BELOW_THRESHOLD])
    sstore = FakeSessionStore()
    status, body = create_session(
        store, sstore,
        {
            "line_items": [
                {"item": {"id": "KURTA-001"}, "quantity": 1},
                {"item": {"id": "THIN-001"}, "quantity": 1},
            ],
            "buyer": {"email": "x@example.com"},
        },
    )
    assert status == 201  # session still created despite one blocked item
    assert len(body["line_items"]) == 1
    assert body["line_items"][0]["item"]["id"] == "KURTA-001"
    assert any("THIN-001" in m["content"] for m in body["messages"])
    assert any(m["code"] == "below_readiness_threshold" for m in body["messages"])
    assert any(d[0] == "THIN-001" and d[1] == "gate_blocked" for d in store.decisions)


def test_create_all_items_blocked_is_unrecoverable():
    """
    A well-formed request where every requested item is ineligible —
    NOT the same case as test_create_missing_line_items_is_400 above.
    This exact posture (200, ucp.status:"error", no resource) is
    confirmed correct by a merged fix to the official UCP conformance
    suite itself (Universal-Commerce-Protocol/conformance#59): the
    suite used to hard-require 400 here and was fixed because the spec
    models this as an in-band business outcome, not a protocol error.
    """
    store = FakeStore([BELOW_THRESHOLD])
    sstore = FakeSessionStore()
    status, body = create_session(store, sstore, {"line_items": [{"item": {"id": "THIN-001"}, "quantity": 1}]})
    assert status == 200  # not 201 — nothing was created
    assert body["ucp"]["status"] == "error"
    assert "id" not in body
    assert "payment_handlers" not in body["ucp"]  # no resource in this response


def test_create_nonexistent_product(store, sstore):
    status, body = create_session(store, sstore, {"line_items": [{"item": {"id": "GHOST-999"}, "quantity": 1}]})
    assert status == 200
    assert body["ucp"]["status"] == "error"


def test_create_currency_mismatch_excludes_second_currency():
    store = FakeStore([ELIGIBLE, VND_ELIGIBLE])
    sstore = FakeSessionStore()
    status, body = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}, {"item": {"id": "AODAI-001"}, "quantity": 1}]},
    )
    assert status == 201
    assert len(body["line_items"]) == 1
    assert body["currency"] == "INR"
    assert any(m["code"] == "currency_mismatch" for m in body["messages"])


def test_create_unmappable_currency_excluded_with_distinct_code():
    store = FakeStore([ELIGIBLE, BAD_CURRENCY])
    sstore = FakeSessionStore()
    _, body = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}, {"item": {"id": "BADCUR-001"}, "quantity": 1}]},
    )
    assert len(body["line_items"]) == 1
    assert any(m["code"] == "not_ucp_ready" and "BADCUR-001" in m["content"] for m in body["messages"])


# --- availability gate: added after a real conformance-test run
# (spck-conformance's out_of_stock_id fixture) exposed that the
# checkout gate previously didn't check availability at all ---

def test_create_out_of_stock_item_alone_is_all_blocked():
    store = FakeStore([OUT_OF_STOCK])
    sstore = FakeSessionStore()
    status, body = create_session(store, sstore, {"line_items": [{"item": {"id": "OOS-001"}, "quantity": 1}]})
    assert status == 200  # nothing eligible
    assert body["ucp"]["status"] == "error"
    assert any(m["code"] == "not_available" for m in body["messages"])


def test_create_out_of_stock_excluded_gracefully_from_multi_item_cart():
    store = FakeStore([ELIGIBLE, OUT_OF_STOCK])
    sstore = FakeSessionStore()
    status, body = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}, {"item": {"id": "OOS-001"}, "quantity": 1}]},
    )
    assert status == 201  # session still created — graceful degradation, same as any other gate failure
    assert len(body["line_items"]) == 1
    assert body["line_items"][0]["item"]["id"] == "KURTA-001"
    assert any("OOS-001" in m["content"] for m in body["messages"])
    assert any(d[0] == "KURTA-001" and d[1] == "gate_passed" for d in store.decisions)


def test_complete_revalidation_catches_stock_out_since_creation():
    """
    Same pattern as test_complete_revalidates_and_catches_score_drop_since_creation,
    for the availability gate instead of readiness: a product that goes
    out of stock between session creation and payment completion must
    be caught at the moment of truth, not trusted from create time.
    """
    store = FakeStore([dict(ELIGIBLE)])
    sstore = FakeSessionStore()
    _, created = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}], "buyer": {"email": "a@b.com"}},
    )
    assert created["status"] == "ready_for_complete"

    store._products["KURTA-001"]["availability"] = "out_of_stock"  # goes out of stock post-creation

    status, body = complete_session(store, sstore, created["id"])
    assert status == 200
    assert body["status"] == "incomplete"
    assert any(m["code"] == "revalidation_failed" for m in body["messages"])
    assert body["line_items"] == []


# --- get_session ---

def test_get_session_found(store, sstore):
    _, created = create_session(store, sstore, {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}]})
    status, body = get_session(sstore, created["id"])
    assert status == 200
    assert body["id"] == created["id"]


def test_get_session_not_found_is_404(sstore):
    status, body = get_session(sstore, "GHOST-SESSION")
    assert status == 404  # confirmed different from catalog's not-found handling
    assert body["ucp"]["status"] == "error"


# --- update_session ---

def test_update_full_replace_semantics(store, sstore):
    _, created = create_session(store, sstore, {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}]})
    status, body = update_session(
        store, sstore, created["id"],
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 2}], "buyer": {"email": "jane@example.com"}},
    )
    assert status == 200
    assert body["line_items"][0]["quantity"] == 2
    assert body["totals"][0]["amount"] == 299800
    assert body["status"] == "ready_for_complete"


# --- complete_session ---

def test_complete_happy_path(store, sstore):
    _, created = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}], "buyer": {"email": "a@b.com"}},
    )
    status, body = complete_session(
        store, sstore, created["id"],
        payment={"instruments": [{"id": "instr_1", "handler_id": "8c9202bd", "selected": True}]},
        order_permalink_base="https://storemind-508614.web.app",
    )
    assert status == 200
    assert body["status"] == "completed"
    assert "order" in body and "id" in body["order"]
    assert body["order"]["permalink_url"].startswith("https://storemind-508614.web.app/orders/")
    assert body["payment"]["instruments"][0]["id"] == "instr_1"


def test_complete_recall_on_already_completed_is_safe(store, sstore):
    _, created = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}], "buyer": {"email": "a@b.com"}},
    )
    _, first = complete_session(store, sstore, created["id"], payment={"instruments": []})
    status2, second = complete_session(store, sstore, created["id"], payment={"instruments": []})
    assert status2 == 200
    assert second["order"]["id"] == first["order"]["id"]  # not reprocessed


def test_complete_revalidates_and_catches_score_drop_since_creation():
    """
    The strongest test of the whole module: a product eligible at
    session-creation time later drops below threshold (simulating a
    re-ingest or live score change). complete_session must catch this
    at the moment of truth, not trust stale eligibility from create time.
    """
    store = FakeStore([dict(ELIGIBLE)])
    sstore = FakeSessionStore()
    _, created = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}], "buyer": {"email": "a@b.com"}},
    )
    assert created["status"] == "ready_for_complete"

    store._products["KURTA-001"]["readiness_score"] = 30.0  # score drops post-creation

    status, body = complete_session(store, sstore, created["id"])
    assert status == 200
    assert body["status"] == "incomplete"  # blocked, not completed
    assert any(m["code"] == "revalidation_failed" for m in body["messages"])
    assert body["line_items"] == []


# --- cancel_session ---

def test_cancel_happy_path(store, sstore):
    _, created = create_session(store, sstore, {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}]})
    status, body = cancel_session(sstore, created["id"])
    assert status == 200
    assert body["status"] == "canceled"


def test_cancel_completed_session_is_rejected(store, sstore):
    _, created = create_session(
        store, sstore,
        {"line_items": [{"item": {"id": "KURTA-001"}, "quantity": 1}], "buyer": {"email": "a@b.com"}},
    )
    complete_session(store, sstore, created["id"])
    status, body = cancel_session(sstore, created["id"])
    assert status == 200  # business rule outcome, not a transport error
    assert any(m["code"] == "already_completed" for m in body["messages"])
    assert body["status"] == "completed"  # not overwritten
    
    