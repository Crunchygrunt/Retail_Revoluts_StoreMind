"""
MCP binding for the UCP Catalog capability: search_catalog,
lookup_catalog, get_product exposed as Model Context Protocol tools —
the "MCP catalog binding" Tier B stretch item from the build spec.

*** GENUINELY UNVERIFIED — READ THIS BEFORE TRUSTING IT. ***

ucp_routes.py was unverified-but-written-with-high-confidence, because
FastAPI is a long-stable API I know well from training. This file
started as a bigger category of risk: the `mcp` Python SDK's exact
mounting mechanics are newer and move faster than FastAPI's. Some of
that risk is now resolved with real sources, not just confidence —
Google's own Cloud Run docs confirm Streamable HTTP MCP servers are
directly supported and explicitly recommend FastMCP for building one
(docs.cloud.google.com/run/docs/host-mcp-servers), and FastMCP's own
integration docs confirm the exact mount method used below
(gofastmcp.com/integrations/fastapi). What's NOT resolved: whether
FastAPI's plain app.mount() picks up FastMCP's lifespan/session-
manager startup with zero further glue code, which I have no way to
execute and confirm here. The tool-wrapping logic below (the actual
business logic) is low-risk regardless — it's a thin pass-through to
ucp_catalog.py's existing, tested functions, nothing new. THE MOUNTING
CODE in main.py (see bottom of this docstring) is still the part most
likely to need a small correction. Test it standalone (see TESTING
below) before wiring a real agent to it.

CONFIRMED REAL DETAILS (not guessed), from ucp.dev/specification/
shopping/catalog/mcp/ and Shopify's real Storefront Catalog MCP
implementation (which explicitly implements this same binding):

  - Tool names: search_catalog, lookup_catalog, get_product — same
    names this project's REST functions already use.
  - Every tool call's arguments are wrapped:
        {"meta": {"ucp-agent": {"profile": "<caller's own UCP profile URL>"}},
         "catalog": {...the actual parameters...}}
    The "catalog" key holds exactly what ucp_catalog.py's REST
    functions already expect (query/filters/pagination for search;
    ids for lookup; id for get_product). This project does not
    currently validate or use meta.ucp-agent.profile for anything (no
    per-agent behavior differs) — accepted without erroring, never
    rejected, since a real MCP client will always send it.
  - Response shape is CONFIRMED IDENTICAL to the REST response —
    "products with title, description, price range (minor units),
    media, and variants; and cursor-based pagination." This is
    exactly what ucp_catalog.py already returns. This file adds ZERO
    new business logic — it imports and calls the exact same
    functions ucp_routes.py's REST endpoints already use, for the
    same single-source-of-truth reason every other gate in this
    project reuses one canonical implementation.
  - Profile declaration: a SECOND entry in services.dev.ucp.shopping
    (transport: "mcp", its own endpoint, a plain HTTPS URL — Streamable
    HTTP, not the older two-endpoint SSE transport). Already added to
    ucp_profile.py, alongside the existing "rest" entry — a business
    can offer both.

TESTING, cheapest-first (do NOT jump straight to a real agent client).
CORRECTED AGAIN from an earlier version of this docstring: that
version recommended `from mcp import Client`, a v2-only class. This
project uses mcp 1.30.0 (downgraded from 2.x specifically because v2
renamed FastMCP to MCPServer and moved its module — confirmed from the
SDK's own migration guide). On 1.x there is no confirmed in-process
shortcut without additional, unverified packages — so this goes
straight to the one path confirmed across multiple independent,
dated (2026) sources on exactly this version range: the two-step
streamablehttp_client + ClientSession client, over real HTTP.
  1. pip install mcp==1.30.0 (or whatever 1.x version avoids the
     FastMCP rename)
  2. Run this file's __main__ block to sanity-check the tools register
     and the store is wired correctly, with no MCP protocol or HTTP
     involved at all. CONFIRMED WORKING on a real run this session.
  3. Wire the mount into main.py (below), run the server locally
     (uvicorn main:app --reload --port 8080), and run
     test_mcp_http_client.py in a second terminal — this uses the
     confirmed streamablehttp_client + ClientSession pattern to
     exercise the real MCP protocol layer AND the app.mount()
     integration together, since 1.x offers no way to cleanly test
     one without the other.
  4. Only then point Claude Desktop / Claude Code / Gemini at the live
     deployed endpoint.

MOUNTING, in main.py — CORRECTED A SECOND TIME, this time with the
missing piece that would have caused a real, confirmed failure mode.
The method IS mcp.streamable_http_app() — that was the original guess
in this file, and it was RIGHT; a later "correction" to .http_app()
was wrong, sourced from gofastmcp.com's documentation of a different,
separately-versioned standalone package, not the mcp.server.fastmcp.
FastMCP class actually imported here. But there's a real requirement
neither version of this docstring had: streamable_http_app() wires
its OWN session-manager startup into a lifespan — but that lifespan
belongs to the Starlette app streamable_http_app() itself returns, and
a MOUNTED sub-application's lifespan never runs. Skip this and the
mount resolves, tools/list may even work, and the first real tool call
fails with "RuntimeError: Task group is not initialized." — confirmed
from the official SDK's own ASGI-mounting docs, not guessed. The
parent app's OWN lifespan must explicitly start it:

    import contextlib
    from fastapi import FastAPI
    from ucp_mcp_server import configure_mcp_store, mcp as ucp_mcp

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with ucp_mcp.session_manager.run():
            yield

    app = FastAPI(title="StoreMind — Catalog Ingestion", lifespan=lifespan)
    # ... existing main.py code, including store = _build_store() ...
    configure_mcp_store(store)
    app.mount("/ucp/mcp", ucp_mcp.streamable_http_app())

This means the FastAPI() constructor call itself needs the lifespan
parameter added — not just three new lines appended somewhere, an
actual change to how `app` is created. If main.py ever needs its own
lifespan for something else later, combine both into one function
rather than passing two lifespan= arguments.

Test with test_mcp_http_client.py (streamablehttp_client + ClientSession
— the confirmed mcp 1.x client API, not the v2-only Client class an
earlier version of this docstring wrongly pointed to).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ucp_catalog import (
    search_catalog as _rest_search_catalog,
    lookup_catalog as _rest_lookup_catalog,
    get_product as _rest_get_product,
)

mcp = FastMCP(
    "storemind-ucp-catalog",
    # REAL PRODUCTION BUG, found via a live 421 on the deployed Cloud
    # Run URL, not anticipated in advance: FastMCP() with no `host`
    # argument defaults to host="127.0.0.1", which auto-enables DNS-
    # rebinding protection restricted to a localhost-only allowlist —
    # confirmed as the exact, well-documented cause across many other
    # real MCP+Cloud-Run deployments hitting this identical symptom
    # (works on 127.0.0.1, 421 "Invalid Host header" on any real
    # hostname). That protection defends a browser-reachable local dev
    # server against malicious JS-driven DNS rebinding — not relevant
    # here, since this is a public HTTPS API with no localhost-only
    # trust boundary, same posture as every other unauthenticated
    # endpoint already in this project. Disabling it explicitly is the
    # documented, correct fix for this deployment shape, confirmed by
    # the official SDK's own "Deploy & scale" guide: "Behind a reverse
    # proxy that already controls the Host header, switching the check
    # off is the honest configuration."
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# Module-level store, set once at startup via configure_mcp_store().
# Not the (store, session_store) pattern build_ucp_router uses — that
# takes them as explicit parameters because it's a factory function
# called once per app. FastMCP's @mcp.tool()-decorated functions are
# plain module-level functions with no equivalent factory call site in
# the patterns this SDK documents with high stability, so a module
# global is the lower-risk choice here over guessing at FastMCP's
# Context/lifespan-state injection API, which I have less confidence
# precisely recalling.
_store: Any = None


def configure_mcp_store(store: Any) -> None:
    """Call once at startup, right after main.py's store = _build_store()."""
    global _store
    _store = store


def _catalog_args(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Unwraps the "catalog" key every real UCP MCP catalog call sends
    its actual parameters inside — see module docstring."""
    return (arguments or {}).get("catalog") or {}


@mcp.tool(name="search_catalog")
def mcp_search_catalog(catalog: dict[str, Any] | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Search the store's product catalog by free-text query and/or filters."""
    params = catalog or {}
    # search_catalog can return (400, {...}) for a malformed request
    # (CAT-009/010, empty query + no filters). KNOWN SIMPLIFICATION:
    # the status code isn't mapped into MCP's own tool-error convention
    # (raising an exception, or setting isError on the result) — the
    # body is returned as-is either way, which still carries the
    # explanatory `messages` array, but a well-behaved MCP client won't
    # see this as a distinct "the tool call failed" signal the way a
    # real HTTP 400 would. Acceptable for a hackathon demo; a real
    # implementation should raise on non-2xx here.
    _status, response = _rest_search_catalog(
        _store,
        query=params.get("query", ""),
        filters=params.get("filters"),
        pagination=params.get("pagination"),
    )
    return response


@mcp.tool(name="lookup_catalog")
def mcp_lookup_catalog(catalog: dict[str, Any] | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Batch lookup products or variants by identifier."""
    params = catalog or {}
    ids = params.get("ids") or []
    return _rest_lookup_catalog(_store, ids)


@mcp.tool(name="get_product")
def mcp_get_product(catalog: dict[str, Any] | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Get full product detail by identifier."""
    params = catalog or {}
    product_id = params.get("id", "")
    return _rest_get_product(_store, product_id)


if __name__ == "__main__":
    # Cheapest possible sanity check — no HTTP, no transport, just
    # confirms the tools are registered and the wrapped functions run
    # against whatever store this is pointed at. Run with:
    #   STOREMIND_STORE_BACKEND=local python3 ucp_mcp_server.py
    import os

    if os.environ.get("STOREMIND_STORE_BACKEND") == "firestore":
        import firestore_client as _dev_store
    else:
        import local_store as _dev_store

    configure_mcp_store(_dev_store)
    print("Registered tools:", [t.name for t in mcp._tool_manager.list_tools()])
    print("(If this line itself errors, _tool_manager's exact attribute")
    print(" name may have moved in the installed mcp SDK version — check")
    print(" the installed package's own introspection API instead.)")
    
    