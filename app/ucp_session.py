"""
UCP Checkout session lifecycle: create, get, update, complete, cancel.

THE HEADLINE CLAIM LIVES HERE. Every function that can add a product to
a session calls storefront.checkout(store, sku, quantity, threshold) —
the SAME fail-closed gate the ACP-facing storefront already enforces.
This module never reimplements the readiness threshold check. That's
deliberate, not laziness: "enforced, not just scored" only means
something if ACP and UCP are provably enforcing the identical rule, not
two implementations that could quietly drift apart. If storefront.py's
gate logic changes, this module changes with it for free.

Gate re-run at COMPLETE, not just at CREATE: a product's readiness score
or availability can change between session creation and payment
completion (a re-ingest, a stock-out). Re-checking at the moment of
truth — right before completion — is a MORE faithful implementation of
fail-closed than gating once at creation and trusting it forever. This
strengthens the headline claim; it isn't just spec-compliance box-
ticking.

Three gates apply to every line item, independently, all must pass:
  1. Readiness gate    — storefront.checkout()'s threshold check.
  2. Availability gate — ucp_mapping.is_ucp_purchasable() (in_stock
                           only). Added after a real conformance-test
                           fixture (an out_of_stock_id) exposed that
                           this gate didn't exist at all — availability
                           was displayed on catalog responses but never
                           enforced at checkout. Fixed by reusing the
                           SAME predicate the catalog display already
                           used, rather than defining the policy twice.
  3. UCP mappability   — ucp_mapping.to_ucp_checkout_line_item() must
                           not raise (valid price + currency).
These are orthogonal failure modes, exactly mirroring how acp_schema.py
keeps ACP conformance and the readiness threshold as separate checks
combined with AND in derive_eligibility(). A product can be UCP-mappable
but below threshold, out of stock but perfectly priced, or above
threshold but UCP-unmappable (bad currency) — each gets its own message
code so the buyer/agent gets an accurate, specific reason, matching this
project's existing remediation-first messaging philosophy (see
acp_schema.py's REMEDIATION dict).

GRACEFUL DEGRADATION, same principle as ACP tiering: a blocked line item
in a multi-item cart does NOT fail the whole session. It's dropped with
an explanatory recoverable message while eligible items proceed. Only
when EVERY requested item is blocked does session creation fail
outright — this mirrors the real spec's own worked example of an
unrecoverable error ("all items are out of stock" is an ALL-condition,
not a some-condition).

CURRENCY: a checkout session has exactly ONE top-level `currency` field
(confirmed in every real example). This project's catalog is genuinely
multi-currency. If eligible line items span more than one currency,
silently summing their minor-unit amounts as if they were the same
currency would silently produce a nonsense total — exactly the class of
bug ucp_money.py exists to prevent, one layer up. So: the FIRST eligible
item's currency becomes the session currency; any later eligible item
in a DIFFERENT currency is excluded too, with its own message
(code "currency_mismatch"), not silently mispriced.

VERSION NOTE: the generic 2026-01-23 checkout-rest binding's own
"Error Responses" section shows a different illustrative error shape
(top-level "status": "requires_escalation", severity
"requires_buyer_input") than what Google's version-PINNED 2026-04-08
implementation guide documents for the same scenario (session-creation-
blocked -> HTTP 200, "ucp": {"status": "error"}, messages[],
continue_url). This module follows the 2026-04-08-specific documented
behavior, since that's the locked version — the older page's shape
looks superseded, not a second valid option to pick between.

INDEPENDENTLY CONFIRMED, not just inferred from the docs: a merged PR
to the OFFICIAL conformance suite itself (Universal-Commerce-Protocol/
conformance#59) fixed that suite's own tests to accept this exact
posture — "checkout-rest.md's own 'Error Response' example answers an
all-items-out-of-stock create with HTTP 200 OK, ucp.status: 'error',
and a typed messages[] entry... A merchant implementing the spec's
documented posture fails these tests today [before the fix]." This
module's all-blocked create response was built to this posture before
that PR was found — the PR is corroborating evidence, not the source of
the decision.

NOT MODELED, deliberately, matching UCP_CHECKLIST.md's Tier C scope:
fulfillment/shipping computation, tax itemization beyond "subtotal
already includes GST/MRP tax the way Indian pricing normally does" (the
tax-inclusive display_text labeling requirement is NOT implemented —
flagged, not silently skipped), identity linking, discounts, real
payment processing. Buyer info is limited to email (the one field the
spec's own worked examples actually gate on).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from readiness_score import DEFAULT_THRESHOLD
from storefront import checkout as _gate_checkout
from ucp_mapping import to_ucp_checkout_line_item, is_ucp_purchasable, UCPMappingError
from ucp_profile import build_payment_handlers

UCP_VERSION = "2026-04-08"


def _envelope(status: str | None = None) -> dict[str, Any]:
    """
    status="error" is used in this file ONLY for the "no resource in
    the response body" cases (the genuine all-items-blocked create
    failure, and the three 404-not-found paths) — confirmed by checking
    every call site in this module, not assumed. Per checkout.md's own
    documented rule for that posture ("no resource is included in the
    response body") and PAY-003's framing of payment_handlers as
    something echoed alongside a real checkout resource, payment_handlers
    is included on every OTHER response (where a real session object is
    attached right after this call) and omitted here.
    """
    ucp: dict[str, Any] = {
        "version": UCP_VERSION,
        "capabilities": {
            "dev.ucp.shopping.checkout": [{"version": UCP_VERSION}],
        },
    }
    if status:
        ucp["status"] = status
    else:
        ucp["payment_handlers"] = build_payment_handlers()
    return {"ucp": ucp}


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


def _sum_totals(line_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subtotal = sum(li["totals"][-1]["amount"] for li in line_items) if line_items else 0
    # No tax/fee/fulfillment modeling — Tier C. subtotal == total until
    # that's built. See module docstring's tax-inclusive note: the
    # NUMBER is probably already tax-inclusive (Indian MRP convention),
    # but the spec's required tax-inclusive display_text labeling is
    # NOT implemented here — that's a real gap, not an oversight.
    return [
        {"type": "subtotal", "amount": subtotal},
        {"type": "total", "amount": subtotal},
    ]


def _resolve_line_items(
    store,
    requested: list[dict[str, Any]],
    threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """
    Runs BOTH gates (readiness + UCP mappability) on each requested line
    item. Returns (eligible_line_items, messages, session_currency).
    session_currency is None if nothing was eligible.

    Every gate decision, pass or block, goes through storefront.checkout
    -> store.log_decision — this module adds no separate audit path.
    """
    eligible: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    session_currency: str | None = None

    for idx, req in enumerate(requested):
        sku = str((req.get("item") or {}).get("id") or "")
        quantity = req.get("quantity")
        if quantity is None:
            quantity = 1
        li_id = req.get("id") or f"li_{idx + 1}"

        if not sku:
            messages.append(
                {
                    "type": "error",
                    "code": "missing",
                    "path": f"$.line_items[{idx}].item.id",
                    "content": "line item is missing item.id",
                    "severity": "recoverable",
                }
            )
            continue
        if not isinstance(quantity, int) or quantity <= 0:
            messages.append(
                {
                    "type": "error",
                    "code": "invalid",
                    "path": f"$.line_items[{idx}].quantity",
                    "content": f"quantity must be a positive integer, got {quantity!r}",
                    "severity": "recoverable",
                }
            )
            continue

        # Gate 1: readiness threshold, via the REAL gate — never reimplemented.
        gate_result = _gate_checkout(store, sku, quantity=quantity, threshold=threshold)
        if not gate_result["success"]:
            messages.append(
                {
                    "type": "error",
                    "code": "below_readiness_threshold" if gate_result.get("readiness_score") is not None else "not_found",
                    "path": f"$.line_items[{idx}].item.id",
                    "content": f"{sku}: {gate_result['reason']}",
                    "severity": "recoverable",
                }
            )
            continue

        # Gate 2: availability, then Gate 3: UCP mappability, below.
        structured = store.get_product(sku)  # already confirmed to exist by gate 1

        # Gate 2: availability. Found missing during a real conformance
        # test run (an out_of_stock_id fixture) — a product's readiness
        # score and price/currency validity say nothing about whether
        # it's currently in stock. Reuses ucp_mapping.is_ucp_purchasable,
        # the SAME predicate the catalog display uses, so what's shown
        # and what's enforced can't drift apart again.
        if not is_ucp_purchasable(structured):
            messages.append(
                {
                    "type": "error",
                    "code": "not_available",
                    "path": f"$.line_items[{idx}].item.id",
                    "content": f"{sku} is not currently available for checkout (availability: {structured.get('availability')!r})",
                    "severity": "recoverable",
                }
            )
            continue

        # Gate 3: UCP mappability (valid price + currency), independent of readiness and availability.
        try:
            item = to_ucp_checkout_line_item(structured)
            item_currency = structured.get("currency", "").upper()
        except UCPMappingError as exc:
            messages.append(
                {
                    "type": "error",
                    "code": "not_ucp_ready",
                    "path": f"$.line_items[{idx}].item.id",
                    "content": f"{sku}: {exc}",
                    "severity": "recoverable",
                }
            )
            continue

        # Currency consistency — see module docstring.
        if session_currency is None:
            session_currency = item_currency
        elif item_currency != session_currency:
            messages.append(
                {
                    "type": "error",
                    "code": "currency_mismatch",
                    "path": f"$.line_items[{idx}].item.id",
                    "content": (
                        f"{sku} is priced in {item_currency}, but this session is "
                        f"already in {session_currency} — a checkout session can "
                        "only hold one currency"
                    ),
                    "severity": "recoverable",
                }
            )
            continue

        unit_price = item["price"]
        line_total = unit_price * quantity
        eligible.append(
            {
                "id": li_id,
                "item": item,
                "quantity": quantity,
                "totals": [
                    {"type": "subtotal", "amount": line_total},
                    {"type": "total", "amount": line_total},
                ],
            }
        )

    return eligible, messages, session_currency


def create_session(
    store,
    session_store,
    request: dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[int, dict[str, Any]]:
    """
    POST /checkout-sessions. Returns (http_status, body).

    HTTP 201 whenever a session is actually created — including the
    "incomplete" case (missing buyer email is still a created session
    with a recoverable message, confirmed by the real spec's own worked
    example). HTTP 200, no session id, only when EVERY requested item
    was blocked (the genuine unrecoverable case, per Google's 2026-04-08
    -specific documented behavior AND per a real merged fix to the
    official conformance suite itself confirming this exact posture —
    see module docstring's VERSION NOTE).

    HTTP 400 is a DIFFERENT case from the above, and was missing until a
    real conformance-test deviation (checkout.create_requires_line_items)
    exposed it: a request with NO line_items at all (malformed shape) is
    not the same thing as a well-formed request where every item happens
    to be ineligible (a business outcome). Collapsing both into the same
    200 response was the actual bug — CAT-009/010's checkout analogue is
    a genuine input-validation requirement, not a business-outcome one.
    """
    requested = request.get("line_items")
    if not requested:
        return 400, {
            **_envelope(status="error"),
            "messages": [
                {
                    "type": "error",
                    "code": "missing_line_items",
                    "path": "$.line_items",
                    "content": "line_items is required and must be a non-empty array",
                    "severity": "unrecoverable",
                }
            ],
        }
    eligible, messages, currency = _resolve_line_items(store, requested, threshold)

    if not eligible:
        response = _envelope(status="error")
        response["messages"] = messages or [
            {
                "type": "error",
                "code": "no_eligible_items",
                "content": "No requested items could be added to a checkout session",
                "severity": "unrecoverable",
            }
        ]
        response["continue_url"] = None  # caller may set a real storefront URL
        return 200, response

    session_id = str(uuid.uuid4())
    buyer = request.get("buyer") or {}
    buyer_email = buyer.get("email")

    status = "incomplete" if not buyer_email else "ready_for_complete"
    if not buyer_email:
        messages.append(
            {
                "type": "error",
                "code": "missing_buyer_info",
                "path": "$.buyer.email",
                "content": "Buyer email is required",
                "severity": "recoverable",
            }
        )

    session = {
        "id": session_id,
        "status": status,
        "currency": currency,
        "line_items": eligible,
        "totals": _sum_totals(eligible),
        "buyer": buyer if buyer_email else None,
        "links": request.get("links") or [],
        "created_at": _now(),
    }
    session_store.write_ucp_session(session)

    response = _envelope()
    response.update({k: v for k, v in session.items() if k != "created_at"})
    if messages:
        response["messages"] = messages
    return 201, response


def get_session(session_store, session_id: str) -> tuple[int, dict[str, Any]]:
    """
    GET /checkout-sessions/{id}. Unlike catalog's not-found handling
    (200 + messages), checkout's own status-code table explicitly lists
    404 as a defined outcome for a missing resource — a real, confirmed
    difference between the two capabilities, not an inconsistency to
    paper over.
    """
    session = session_store.get_ucp_session(session_id)
    if session is None:
        return 404, {
            **_envelope(status="error"),
            "messages": [
                {
                    "type": "error",
                    "code": "not_found",
                    "content": f"Checkout session not found: {session_id}",
                    "severity": "unrecoverable",
                }
            ],
        }
    response = _envelope()
    response.update({k: v for k, v in session.items() if k not in ("created_at", "updated_at")})
    return 200, response


def update_session(
    store,
    session_store,
    session_id: str,
    request: dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[int, dict[str, Any]]:
    """
    PUT /checkout-sessions/{id}. Full-replace semantics, confirmed by
    the real spec: "Each PUT replaces the entire session, so clients
    must include all previously set fields they wish to retain." So
    this re-derives eligibility, currency, and totals from THIS
    request's line_items rather than patching the stored session —
    avoids stale-gate-state bugs where a previously-eligible item stays
    listed after conditions changed.

    A completed or canceled session cannot be updated — returns the
    session as-is with an explanatory message, HTTP 200 (a business
    rule outcome, not a transport error).
    """
    existing = session_store.get_ucp_session(session_id)
    if existing is None:
        return 404, {
            **_envelope(status="error"),
            "messages": [
                {"type": "error", "code": "not_found", "content": f"Checkout session not found: {session_id}", "severity": "unrecoverable"}
            ],
        }
    if existing["status"] in ("completed", "canceled"):
        response = _envelope()
        response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
        response["messages"] = [
            {
                "type": "error",
                "code": "session_closed",
                "content": f"Checkout session is already {existing['status']} and cannot be updated",
                "severity": "unrecoverable",
            }
        ]
        return 200, response

    requested = request.get("line_items") or []
    eligible, messages, currency = _resolve_line_items(store, requested, threshold)

    if not eligible:
        response = _envelope()
        response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
        response["messages"] = messages or [
            {"type": "error", "code": "no_eligible_items", "content": "No requested items are eligible", "severity": "unrecoverable"}
        ]
        return 200, response

    buyer = request.get("buyer") or {}
    buyer_email = buyer.get("email")
    status = "incomplete" if not buyer_email else "ready_for_complete"
    if not buyer_email:
        messages.append(
            {"type": "error", "code": "missing_buyer_info", "path": "$.buyer.email", "content": "Buyer email is required", "severity": "recoverable"}
        )

    session = {
        "id": session_id,
        "status": status,
        "currency": currency,
        "line_items": eligible,
        "totals": _sum_totals(eligible),
        "buyer": buyer if buyer_email else None,
        "links": request.get("links") or existing.get("links") or [],
        "created_at": existing.get("created_at"),
    }
    session_store.write_ucp_session(session)

    response = _envelope()
    response.update({k: v for k, v in session.items() if k != "created_at"})
    if messages:
        response["messages"] = messages
    return 200, response


def complete_session(
    store,
    session_store,
    session_id: str,
    payment: dict[str, Any] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    order_permalink_base: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """
    POST /checkout-sessions/{id}/complete.

    Re-runs the readiness gate on every line item RIGHT NOW, not just
    trusting the state from create/update — see module docstring. If
    anything that was eligible at create time has since fallen below
    threshold or gone unmappable, completion is blocked and the session
    drops back to "incomplete" with an explanatory message, rather than
    completing on stale eligibility.

    Basic re-call safety: completing an already-completed session
    returns the same completed session again rather than re-processing.
    This is NOT the full Idempotency-Key HTTP mechanism the real spec
    requires (store-key-for-24h, 409 on conflicting reuse) — that's a
    header-level concern that belongs in ucp_routes.py, not here, and
    is not yet built. This is a narrower, session-state-level safety
    net, not a substitute for it.

    Payment is intentionally not really processed — see
    UCP_CHECKLIST.md's Tier C scope and the build spec's payment-handler
    decision (com.google.pay / TEST, unprocessed). `payment` is accepted
    and its `instruments[0].id` echoed back, matching the response shape
    real agents expect, but no token is charged.
    """
    existing = session_store.get_ucp_session(session_id)
    if existing is None:
        return 404, {
            **_envelope(status="error"),
            "messages": [
                {"type": "error", "code": "not_found", "content": f"Checkout session not found: {session_id}", "severity": "unrecoverable"}
            ],
        }

    if existing["status"] == "completed":
        response = _envelope()
        response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
        return 200, response

    if existing["status"] == "canceled":
        response = _envelope()
        response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
        response["messages"] = [
            {"type": "error", "code": "session_closed", "content": "Checkout session was canceled", "severity": "unrecoverable"}
        ]
        return 200, response

    # Re-run the gate NOW, on the session's current line items — see docstring.
    reverify_request = [
        {"item": {"id": li["item"]["id"]}, "id": li["id"], "quantity": li["quantity"]}
        for li in existing["line_items"]
    ]
    eligible, messages, currency = _resolve_line_items(store, reverify_request, threshold)

    if len(eligible) < len(existing["line_items"]):
        existing["status"] = "incomplete"
        existing["line_items"] = eligible
        existing["totals"] = _sum_totals(eligible)
        session_store.write_ucp_session(existing)
        response = _envelope()
        response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
        response["messages"] = messages + [
            {
                "type": "error",
                "code": "revalidation_failed",
                "content": "One or more items are no longer eligible for checkout and were removed from this session",
                "severity": "recoverable",
            }
        ]
        return 200, response

    if not existing.get("buyer", {}) or not existing["buyer"].get("email"):
        response = _envelope()
        response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
        response["messages"] = [
            {"type": "error", "code": "missing_buyer_info", "path": "$.buyer.email", "content": "Buyer email is required", "severity": "recoverable"}
        ]
        return 200, response

    order_id = f"ORD{uuid.uuid4().hex[:12]}"
    permalink = f"{order_permalink_base.rstrip('/')}/orders/{order_id}" if order_permalink_base else None

    existing["status"] = "completed"
    existing["order"] = {"id": order_id, "permalink_url": permalink} if permalink else {"id": order_id}
    if payment and payment.get("instruments"):
        existing["payment"] = {
            "instruments": [
                {"id": inst.get("id"), "handler_id": inst.get("handler_id"), "selected": True}
                for inst in payment["instruments"]
                if inst.get("selected")
            ]
        }
    session_store.write_ucp_session(existing)

    response = _envelope()
    response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
    return 200, response


def cancel_session(session_store, session_id: str) -> tuple[int, dict[str, Any]]:
    """POST /checkout-sessions/{id}/cancel."""
    existing = session_store.get_ucp_session(session_id)
    if existing is None:
        return 404, {
            **_envelope(status="error"),
            "messages": [
                {"type": "error", "code": "not_found", "content": f"Checkout session not found: {session_id}", "severity": "unrecoverable"}
            ],
        }

    if existing["status"] == "completed":
        response = _envelope()
        response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
        response["messages"] = [
            {"type": "error", "code": "already_completed", "content": "A completed checkout session cannot be canceled", "severity": "unrecoverable"}
        ]
        return 200, response

    existing["status"] = "canceled"
    session_store.write_ucp_session(existing)
    response = _envelope()
    response.update({k: v for k, v in existing.items() if k not in ("created_at", "updated_at")})
    return 200, response

