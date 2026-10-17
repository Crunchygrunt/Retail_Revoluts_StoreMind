"""
Builds the UCP business profile served at /.well-known/ucp.

Single-tenant, per the Sept 19 decision: one seller config, one profile.
Reads config the same way catalog_agent.py does — os.environ.get() with
explicit fallbacks, no hidden defaults — so this stays swappable via
.env exactly like STOREMIND_LLM_BACKEND / STOREMIND_STORE_BACKEND
already are.

Declares ONLY capabilities this project actually implements:
    dev.ucp.shopping.catalog.search   (ucp_catalog.search_catalog)
    dev.ucp.shopping.catalog.lookup   (ucp_catalog.lookup_catalog + get_product —
                                        spec requires BOTH /catalog/lookup and
                                        /catalog/product when Lookup is advertised)
    dev.ucp.shopping.checkout         (ucp_session.py, all five operations)
Deliberately NOT declared, matching UCP_CHECKLIST.md's Tier C scope and
ucp_session.py's own documented gaps: fulfillment, discount, order
(full lifecycle/webhooks), identity_linking, cart. Declaring a
capability this server doesn't actually implement would make the
profile itself non-conformant — worse than declaring fewer things
honestly.

2026-04-08 note (confirmed via the DEV.to community writeup of that
release): business profiles no longer REQUIRE spec/schema URLs on
capabilities, services, or payment handlers — that's now
platform-level-only. This module includes them anyway, matching the
spec's own worked profile example exactly, since it costs nothing and
is more informative for any agent inspecting the profile; they are
optional decoration here, not a conformance requirement being met.

signing_keys: NOT included. RFC 9421 message signing is Tier C — not
implemented. Fabricating a placeholder keypair that looks real would be
worse than honestly omitting the field: a key nobody can actually verify
against is a liability, not a feature, in a demo a judge might inspect.
Same "never guess, report or omit" instinct as acp_schema.py's REMEDIATION
approach.

Payment handler: com.google.pay in "TEST" environment, per the build
spec's documented decision — keeps the profile conformance-suite-
compatible (it's the spec's own worked example shape) while
ucp_session.complete_session never actually processes the token. A
decision log entry recording this should be written once at startup by
main.py, not per-request here.

DEPLOYMENT REMINDER (not this module's job, but breaks silently if
missed): Firebase Hosting must REWRITE /.well-known/ucp to Cloud Run,
not serve it as a static file — otherwise this function's live config
awareness is moot and the profile agents see is stale/wrong.
"""

from __future__ import annotations

import os
from typing import Any

UCP_VERSION = "2026-04-08"


def build_payment_handlers(
    seller_name: str | None = None,
    merchant_id: str | None = None,
) -> dict[str, Any]:
    """
    The com.google.pay handler declaration, extracted as its own
    function for one reason: it needs to appear in TWO places with
    IDENTICAL content — the /.well-known/ucp profile (via
    build_ucp_profile below) AND inside every checkout session response
    (ucp_session.py's _envelope()), per PAY-003: "Checkout/Order
    responses echo handlers in ucp.payment_handlers". Confirmed against
    two independent real sources, not assumed: Google's own worked
    checkout-response example nests payment_handlers inside the
    response's own `ucp` object (identical shape to the profile), and
    ucp.dev's Checkout Response schema table separately lists a
    required `payment` field "containing handlers". Before this
    function existed, ucp_session.py's checkout responses simply didn't
    echo this at all — found via a real conformance-test deviation
    (payment.response_handlers_echo), not anticipated in advance.

    Defining this ONCE and importing it into ucp_session.py means the
    profile and every checkout response can never quietly drift apart
    on payment handler details — same reuse principle as
    storefront.checkout() for the readiness gate and
    ucp_mapping.is_ucp_purchasable() for the availability gate.

    Falls back to the same placeholder-looking values as
    build_ucp_profile() when config is missing — never crashes, never
    silently invents a real-looking merchant id.
    """
    seller_name = seller_name or os.environ.get("STOREMIND_SELLER_NAME") or "StoreMind Seller (unconfigured)"
    merchant_id = merchant_id or os.environ.get("STOREMIND_UCP_MERCHANT_ID") or "UNCONFIGURED_TEST_MERCHANT_ID"

    return {
        "com.google.pay": [
            {
                "id": merchant_id,
                "version": "2026-01-23",
                "spec": "https://pay.google.com/gp/p/ucp/2026-01-23/",
                "schema": "https://pay.google.com/gp/p/ucp/2026-01-23/schemas/config.json",
                "config": {
                    "api_version": 2,
                    "api_version_minor": 0,
                    "environment": "TEST",
                    "merchant_info": {
                        "merchant_name": seller_name,
                        "merchant_id": merchant_id,
                    },
                    "allowed_payment_methods": [
                        {
                            "type": "CARD",
                            "parameters": {
                                "allowed_auth_methods": ["PAN_ONLY"],
                                "allowed_card_networks": [
                                    "AMEX",
                                    "DISCOVER",
                                    "JCB",
                                    "MASTERCARD",
                                    "VISA",
                                ],
                            },
                            "tokenization_specification": {
                                "type": "PAYMENT_GATEWAY",
                                "parameters": {
                                    "gateway": "example",
                                    "gatewayMerchantId": merchant_id,
                                },
                            },
                        }
                    ],
                },
            }
        ]
    }


def build_ucp_profile(
    base_url: str | None = None,
    seller_name: str | None = None,
    merchant_id: str | None = None,
) -> dict[str, Any]:
    """
    Builds the full /.well-known/ucp document.

    base_url: this server's public HTTPS origin (e.g.
        "https://storemind-ingest-444891828026.us-central1.run.app").
        Falls back to STOREMIND_UCP_BASE_URL if not passed explicitly —
        ucp_routes.py should normally pass this in from request context
        or deploy config rather than rely on the env fallback alone,
        since a profile served with the wrong base_url silently breaks
        every agent that discovers it.
    seller_name: falls back to STOREMIND_SELLER_NAME (already a real
        configured value in deploy.env per the prior session's handoff —
        "Zara" at time of writing). Used only in the payment handler's
        merchant_info, matching the spec's own example shape; StoreMind
        has no separate "platform name" to distinguish from seller name
        in this single-tenant configuration.
    merchant_id: a Google Pay merchant identifier. In TEST environment
        this can be any stable string — it is never used to actually
        route a real payment (see complete_session's docstring: no
        token is charged). Falls back to STOREMIND_UCP_MERCHANT_ID.

    Raises no exceptions on missing config — falls back to clearly
    placeholder-looking values rather than crashing profile generation,
    since a profile that fails to serve at all is worse than one that
    honestly shows it's not fully configured. Callers running toward the
    real demo should set the env vars rather than rely on these.
    """
    base_url = (base_url or os.environ.get("STOREMIND_UCP_BASE_URL", "")).rstrip("/")
    seller_name = seller_name or os.environ.get("STOREMIND_SELLER_NAME") or "StoreMind Seller (unconfigured)"
    merchant_id = merchant_id or os.environ.get("STOREMIND_UCP_MERCHANT_ID") or "UNCONFIGURED_TEST_MERCHANT_ID"

    endpoint = f"{base_url}/ucp/v1" if base_url else ""
    mcp_endpoint = f"{base_url}/ucp/mcp" if base_url else ""

    return {
        "ucp": {
            "version": UCP_VERSION,
            "services": {
                "dev.ucp.shopping": [
                    {
                        "version": UCP_VERSION,
                        "spec": f"https://ucp.dev/{UCP_VERSION}/specification/overview",
                        "transport": "rest",
                        "endpoint": endpoint,
                        "schema": f"https://ucp.dev/{UCP_VERSION}/services/shopping/rest.openapi.json",
                    },
                    {
                        "version": UCP_VERSION,
                        "spec": f"https://ucp.dev/{UCP_VERSION}/specification/overview",
                        "transport": "mcp",
                        "endpoint": mcp_endpoint,
                        "schema": f"https://ucp.dev/{UCP_VERSION}/services/shopping/mcp.openrpc.json",
                    },
                ]
            },
            "capabilities": {
                "dev.ucp.shopping.catalog.search": [
                    {
                        "version": UCP_VERSION,
                        "spec": f"https://ucp.dev/{UCP_VERSION}/specification/shopping/catalog/search",
                        "schema": f"https://ucp.dev/{UCP_VERSION}/schemas/shopping/catalog_search.json",
                    }
                ],
                "dev.ucp.shopping.catalog.lookup": [
                    {
                        "version": UCP_VERSION,
                        "spec": f"https://ucp.dev/{UCP_VERSION}/specification/shopping/catalog/lookup",
                        "schema": f"https://ucp.dev/{UCP_VERSION}/schemas/shopping/catalog_lookup.json",
                    }
                ],
                "dev.ucp.shopping.checkout": [
                    {
                        "version": UCP_VERSION,
                        "spec": f"https://ucp.dev/{UCP_VERSION}/specification/shopping/checkout",
                        "schema": f"https://ucp.dev/{UCP_VERSION}/schemas/shopping/checkout.json",
                    }
                ],
            },
            "payment_handlers": build_payment_handlers(seller_name=seller_name, merchant_id=merchant_id),
        }
    }


def is_configured(profile: dict[str, Any]) -> bool:
    """
    True if build_ucp_profile() produced a profile with real config
    (not the placeholder fallbacks). Useful for a startup check/warning
    in main.py — a profile serving UNCONFIGURED_TEST_MERCHANT_ID to a
    real agent is a silent demo-breaker, worth failing loudly on rather
    than discovering live.
    """
    endpoint = profile["ucp"]["services"]["dev.ucp.shopping"][0]["endpoint"]
    merchant_id = profile["ucp"]["payment_handlers"]["com.google.pay"][0]["id"]
    return bool(endpoint) and merchant_id != "UNCONFIGURED_TEST_MERCHANT_ID"

