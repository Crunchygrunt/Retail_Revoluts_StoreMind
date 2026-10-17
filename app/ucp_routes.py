"""
FastAPI router for the UCP server: the well-known profile, the catalog
capability, and the checkout capability, all in one place.

STATUS: the routing/dispatch mechanics in this file were confirmed
working via a real manual pass against the live deployment (create /
get / update / complete / cancel a real checkout session, idempotency
replay, idempotency conflict — all matched expected behavior) and via
a full local pytest run using FastAPI's TestClient (see
test_ucp_routes.py). This file was originally written and shipped
without ever having been executed anywhere — that's no longer true.

What IS new and NOT yet re-verified as of this edit: the idempotency
storage backing was just switched from an in-memory dict to persistent
Firestore records (see the block below) — same external behavior,
different internals. Re-run the idempotency replay/conflict checks
(manual or via test_ucp_routes.py) after this change before trusting
it, since the storage layer itself changed even though nothing about
the request/response contract did.

Mounting, in main.py:
    from ucp_routes import build_ucp_router
    app.include_router(build_ucp_router(store, session_store))

No blanket prefix is used on the router (deliberately — see below), so
every path below is written out in full.

WHY NO PREFIX: /.well-known/ucp MUST be served at the true site root,
unauthenticated. An APIRouter(prefix="/ucp/v1") would push it to
/ucp/v1/.well-known/ucp, which is not where any agent will look for it.
Using one un-prefixed router with full paths avoids this whole class of
mistake rather than needing two separately-mounted routers kept in sync.

DEPLOYMENT NOTE: this project's actual deployment does NOT proxy
through Firebase Hosting — /.well-known/ucp and /p/{sku} (ACP's own
product pages, already in main.py) are both served directly from the
Cloud Run origin. firebase.json has no rewrites and needs none. An
earlier version of this comment said the opposite; confirmed wrong
once main.py's real routes were actually seen.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ucp_profile import build_ucp_profile
from ucp_catalog import search_catalog, lookup_catalog, get_product as catalog_get_product
from ucp_session import (
    create_session,
    get_session,
    update_session,
    complete_session,
    cancel_session,
)

# Persistent idempotency storage — see firestore_client_IDEMPOTENCY_
# ADDITION.py for the two functions this relies on
# (write_idempotency_record / get_idempotency_record). This REPLACES an
# earlier in-memory dict, which was flagged explicitly as demo-scoped
# and not surviving a Cloud Run cold start or multiple instances — that
# gap is now closed. `session_store` here is the same object already
# passed into build_ucp_router (in production, firestore_client), so
# this requires no new parameter threading through the router.
#
# request_fingerprint exists because a real conformance-test run
# (checkout.idempotency_conflict / idempotency.conflict_409) exposed
# that an even earlier version of this only did exact-key replay — it
# never compared the incoming request against what was stored for that
# key, so CHK-048's actual requirement ("409 if [the key is] reused
# with different parameters") was never implemented at all, only the
# simpler "same key -> same cached result" half of it.


def _fingerprint(request_body: dict[str, Any]) -> Any:
    """
    A comparable, order-independent fingerprint of a request body, for
    idempotency conflict detection. json.dumps(sort_keys=True) rather
    than the raw dict — dicts compare equal regardless of key order in
    Python already, but nested lists-of-dicts and float/int formatting
    differences (1 vs 1.0) could otherwise cause two semantically-
    identical requests to be treated as a conflict. A string fingerprint
    sidesteps that rather than trying to write a custom deep-equality
    check.
    """
    import json

    return json.dumps(request_body, sort_keys=True, default=str)


class IdempotencyConflict(Exception):
    """Raised when an Idempotency-Key is reused with different request
    parameters — the route handler catches this and returns 409, per
    CHK-048 / IDM-004."""


def _idempotent_replay(
    session_store, key: str | None, request_body: dict[str, Any]
) -> tuple[int, dict[str, Any]] | None:
    """
    Returns the stored (status_code, body) if this exact key+body was
    seen before. Returns None if the key is new (proceed normally) —
    including when a record exists but has expired, which
    get_idempotency_record already treats as absent (see its own
    docstring on why that check lives there rather than here). Raises
    IdempotencyConflict if the key was seen before with a DIFFERENT
    body — the route handler must catch this specifically and return
    409, not treat it like a cache miss.
    """
    if not key:
        return None
    record = session_store.get_idempotency_record(key)
    if record is None:
        return None
    if record["fingerprint"] != _fingerprint(request_body):
        raise IdempotencyConflict(key)
    return record["status_code"], record["body"]


def _idempotent_store(
    session_store, key: str | None, request_body: dict[str, Any], status_code: int, body: dict[str, Any]
) -> None:
    if not key:
        return
    session_store.write_idempotency_record(key, _fingerprint(request_body), status_code, body)


def _idempotency_conflict_response() -> dict[str, Any]:
    return {
        "ucp": {"version": "2026-04-08", "status": "error"},
        "messages": [
            {
                "type": "error",
                "code": "idempotency_key_conflict",
                "content": "This Idempotency-Key was already used with different request parameters",
                "severity": "unrecoverable",
            }
        ],
    }


def build_ucp_router(store, session_store) -> APIRouter:
    """
    store: whatever module main.py already selected (local_store or
        firestore_client), duck-typed to get_product/list_products/
        log_decision — same object storefront.py and currency_inference.py
        already receive.
    session_store: an object providing write_ucp_session/get_ucp_session
        — in production this is firestore_client (once
        firestore_client_ADDITION.py's two functions are pasted in), so
        `store` and `session_store` will usually be the SAME module
        passed twice. Kept as two parameters rather than one so a test
        or a future split-store setup isn't forced to satisfy both
        interfaces on one object.
    """
    router = APIRouter()

    # ---- Well-known profile ----

    @router.get("/.well-known/ucp")
    async def get_profile(request: Request):
        # request.base_url reflects the actual serving origin (works
        # whether reached directly on Cloud Run or via a Firebase
        # Hosting rewrite), which is more robust than trusting only the
        # STOREMIND_UCP_BASE_URL env var — ucp_profile.py still falls
        # back to that env var if base_url resolution ever looks wrong,
        # but this is the primary source.
        base_url = str(request.base_url).rstrip("/")
        profile = build_ucp_profile(base_url=base_url)
        # DISC-003, found via a real conformance-test deviation
        # (discovery.profile_cache_control): profile responses MUST
        # send Cache-Control: public, max-age>=60. Starlette's
        # JSONResponse sets no Cache-Control by default.
        return JSONResponse(
            content=profile,
            status_code=200,
            headers={"Cache-Control": "public, max-age=300"},
        )

    # ---- Catalog capability — always HTTP 200, business outcomes are
    # embedded in the body per the confirmed spec pattern (see
    # ucp_catalog.py's own docstrings for the search/lookup vs.
    # get_product asymmetry already handled inside that module). ----

    @router.post("/ucp/v1/catalog/search")
    async def catalog_search(request: Request):
        body = await request.json()
        status_code, response = search_catalog(
            store,
            query=body.get("query", ""),
            filters=body.get("filters"),
            pagination=body.get("pagination"),
        )
        return JSONResponse(content=response, status_code=status_code)

    @router.post("/ucp/v1/catalog/lookup")
    async def catalog_lookup(request: Request):
        body = await request.json()
        ids = body.get("ids") or []
        response = lookup_catalog(store, ids)
        return JSONResponse(content=response, status_code=200)

    @router.post("/ucp/v1/catalog/product")
    async def catalog_product(request: Request):
        body = await request.json()
        product_id = body.get("id", "")
        response = catalog_get_product(store, product_id)
        return JSONResponse(content=response, status_code=200)

    # ---- Checkout capability — HTTP status varies by outcome, per the
    # (status_code, body) tuples ucp_session.py already returns. ----

    @router.post("/ucp/v1/checkout-sessions")
    async def checkout_create(
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        request_body = await request.json()
        try:
            cached = _idempotent_replay(session_store, idempotency_key, request_body)
        except IdempotencyConflict:
            return JSONResponse(content=_idempotency_conflict_response(), status_code=409)
        if cached is not None:
            status_code, body = cached
            return JSONResponse(content=body, status_code=status_code)

        status_code, body = create_session(store, session_store, request_body)
        _idempotent_store(session_store, idempotency_key, request_body, status_code, body)
        return JSONResponse(content=body, status_code=status_code)

    @router.get("/ucp/v1/checkout-sessions/{session_id}")
    async def checkout_get(session_id: str):
        status_code, body = get_session(session_store, session_id)
        return JSONResponse(content=body, status_code=status_code)

    @router.put("/ucp/v1/checkout-sessions/{session_id}")
    async def checkout_update(session_id: str, request: Request):
        request_body = await request.json()
        status_code, body = update_session(store, session_store, session_id, request_body)
        return JSONResponse(content=body, status_code=status_code)

    @router.post("/ucp/v1/checkout-sessions/{session_id}/complete")
    async def checkout_complete(
        session_id: str,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        # This is the OTHER state-mutating POST the spec's
        # Idempotency-Key requirement applies to — completion is
        # exactly the operation where accidental double-processing
        # matters most (even though payment itself is mocked here, the
        # session state transition to "completed" should still only
        # happen once per real request, not once per retry).
        request_body = await request.json()
        try:
            cached = _idempotent_replay(session_store, idempotency_key, request_body)
        except IdempotencyConflict:
            return JSONResponse(content=_idempotency_conflict_response(), status_code=409)
        if cached is not None:
            status_code, body = cached
            return JSONResponse(content=body, status_code=status_code)

        status_code, body = complete_session(
            store,
            session_store,
            session_id,
            payment=request_body.get("payment"),
        )
        _idempotent_store(session_store, idempotency_key, request_body, status_code, body)
        return JSONResponse(content=body, status_code=status_code)

    @router.post("/ucp/v1/checkout-sessions/{session_id}/cancel")
    async def checkout_cancel(session_id: str):
        status_code, body = cancel_session(session_store, session_id)
        return JSONResponse(content=body, status_code=status_code)

    return router

