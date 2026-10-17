"""
Live agent bridge: Gemini function-calling + a REAL MCP client call
against this project's own mounted /ucp/mcp endpoint. This is the
server-side orchestration behind the frontend's "ask the catalog a
question" widget — the same workflow Gemini CLI demonstrated against
production, just driven from a browser instead of a terminal.

*** GENUINELY UNVERIFIED — READ THIS BEFORE TRUSTING IT, SAME AS
    ucp_mcp_server.py's OWN BANNER. ***

What's REUSED, not reinvented, and therefore lower-risk:
  - Gemini client construction: genai.Client(vertexai=True, project=,
    location=) — copied directly from catalog_agent.py's _get_client(),
    same env vars (GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION,
    STOREMIND_VERTEXAI_MODEL / gemini-3.6-flash default). Not a second,
    parallel way of authenticating.
  - The MCP client pattern: ClientSession + streamable_http_client (NOT
    streamablehttp_client — see the note below) copied line-for-line
    from test_mcp_http_client.py, the one file in this project actually
    confirmed working over real HTTP, twice, per the original handoff.
  - types.Part.from_bytes(...) as precedent that the types.Part.from_X
    classmethod family is real and used in this exact SDK version —
    catalog_agent.py already calls it for image attachments.

What's GENUINELY NEW and NOT independently verified:
  - Gemini function-calling (tool use) itself. catalog_agent.py never
    does this — every call there is plain text-in/JSON-out. The exact
    shapes below (types.FunctionDeclaration, types.Schema field names,
    types.GenerateContentConfig(tools=...), how a function_call Part is
    read back, types.Part.from_function_response(...) for sending the
    result back) are my best-effort construction from the google-genai
    SDK's documented conventions and the from_bytes precedent above —
    NOT confirmed by running it, the way every MCP mounting decision in
    ucp_mcp_server.py eventually was. This SDK surface has already
    caused real, confirmed naming mistakes once in this project (the
    fastmcp vs mcp.server.fastmcp confusion) — treat this the same way:
    a real risk, not a formality.
  - The multi-turn conversation history management (appending the
    model's function_call turn, then a function_response turn, back
    into `contents` for the next call) — constructed from the SDK's
    documented multi-turn pattern, not run.

TEST THIS BEFORE A JUDGE EVER SEES IT. test_mcp_agent_bridge.py (sibling
file) is a standalone script — run it locally, same two-terminal setup
test_mcp_http_client.py already needed (server running in one terminal,
this in another), BEFORE wiring the frontend widget to it, exactly the
same cheapest-first discipline ucp_mcp_server.py's own docstring lays
out. If the function-calling shape is wrong, the error will be a clear
AttributeError/TypeError from the google-genai SDK, not a silent wrong
answer — fix forward from that real error rather than guessing again
in advance.

CONFIRMED REAL DETAIL worth restating: ucp_mcp_server.py's docstring
prose says "streamablehttp_client" but test_mcp_http_client.py's actual
CODE — the file confirmed run successfully, twice, per the original
handoff — imports "streamable_http_client" (with the underscore). The
code that was actually run is the truth here; this file matches it.
"""

from __future__ import annotations

import json
import os
from typing import Any

# Loopback URL for this project's OWN mounted MCP endpoint. Uses Cloud
# Run's own PORT env var convention (falls back to 8080, matching this
# project's documented local-dev default) rather than the service's
# public HTTPS URL — same-process localhost call, no dependency on
# knowing the deployed URL from inside itself, and no extra network hop
# through Cloud Run's own load balancer for a call to itself.
# WORTH CONFIRMING: this assumes Cloud Run's container listens on
# 127.0.0.1 at $PORT, which is the platform's standard contract, but
# wasn't independently re-verified against this specific service's
# actual startup command.
def _mcp_url() -> str:
    port = os.environ.get("PORT", "8080")
    return f"http://127.0.0.1:{port}/ucp/mcp"


# Fixed rather than introspected via session.list_tools() at request
# time — one fewer round trip, and these three shapes are already
# CONFIRMED (not guessed) from ucp_mcp_server.py's own docstring
# (real request/response shapes, sourced from ucp.dev's spec and
# Shopify's real implementation) and ucp_catalog.py's actual function
# signatures. If the real tool schemas ever diverge from these
# declarations, Gemini will still only ever send arguments matching
# what it's told exists here — the MCP tool call itself would then
# fail loudly (a real MCP error, not a silent wrong answer), not
# silently produce a plausible-looking bad result.
def _tool_declarations():
    from google.genai import types

    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="search_catalog",
                description=(
                    "Search the store's product catalog by free-text query. "
                    "Use this to find products matching what the shopper is "
                    "asking about, e.g. a product name, category, or material."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "query": types.Schema(
                            type="STRING",
                            description="Free-text search query, e.g. 'cotton kurta' or 'running shoes'.",
                        ),
                    },
                    required=["query"],
                ),
            ),
            types.FunctionDeclaration(
                name="lookup_catalog",
                description="Batch lookup one or more products by their exact SKU/product ID.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "ids": types.Schema(
                            type="ARRAY",
                            items=types.Schema(type="STRING"),
                            description="One or more exact product SKUs/IDs to look up.",
                        ),
                    },
                    required=["ids"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_product",
                description=(
                    "Get full detail (price, description, stock status, product URL) "
                    "for exactly one product by its exact SKU/product ID."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "id": types.Schema(type="STRING", description="The exact product SKU/ID."),
                    },
                    required=["id"],
                ),
            ),
        ]
    )


def _build_gemini_client():
    """Copied directly from catalog_agent.CatalogStructuringAgent._get_client
    — same env vars, same construction, so this widget authenticates
    exactly the way the rest of the app's real Vertex AI calls already
    do, not a second parallel setup."""
    from google import genai

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT not set — same requirement as catalog_agent.py."
        )
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    model_name = os.environ.get("STOREMIND_VERTEXAI_MODEL", "gemini-3.6-flash")
    client = genai.Client(vertexai=True, project=project, location=location)
    return client, model_name


class AgentBridgeError(RuntimeError):
    """Raised for a clean, explainable failure (couldn't reach a final
    answer, MCP call failed) — main.py catches this and returns a
    normal error response rather than a 500, same "never let one bad
    call crash the request" instinct as catalog_agent.structure_batch."""


# Same pattern as ucp_mcp_server.configure_mcp_store — a module-level
# reference set once at startup, called with the SAME store object
# main.py already built (not a second, separate construction). Needed
# for the readiness cross-check below: the MCP catalog mapping itself
# (ucp_mapping.to_ucp_catalog_product) only excludes on price/currency
# mappability, not readiness — a deliberate two-tier design shared with
# ACP's own Tier 3 "discovery only" concept, confirmed by reading
# ucp_eligibility.py's own docstring. That's the right call for a
# generic catalog-browsing agent, but this widget speaks in the
# shopper's voice ("yes, we have X") — so it needs its own, stricter
# gate on top, applied here rather than inside ucp_mapping.py, to avoid
# touching the shared, already-conformance-tested (42/43, independently
# audited) code every REST endpoint also depends on.
_store: Any = None


def configure_bridge_store(store: Any) -> None:
    """Call once at startup, right alongside configure_mcp_store(store)."""
    global _store
    _store = store


def _meets_readiness(sku: str) -> bool:
    """Fail-closed: a SKU the store doesn't recognize, or whose
    readiness lookup itself errors, is treated as NOT ready — same
    "unknown means not safe to recommend" stance as everywhere else in
    this project, never the reverse."""
    if _store is None or not sku:
        return False
    try:
        record = _store.get_product(sku)
    except Exception:  # noqa: BLE001 — a lookup failure must not crash the whole answer
        return False
    return bool(record and record.get("meets_readiness_threshold"))


def _filter_ucp_response_for_readiness(raw_json_text: str) -> str:
    """Applied to every MCP tool result before Gemini ever sees it — a
    HARD filter, not a prompt instruction telling the model to ignore
    ineligible items. A prompt instruction is a soft guarantee the
    model could still slip past; removing the item from what it's
    shown at all is the same "enforced, not scored" principle this
    whole project is built on, applied here instead of relying on the
    model to behave.

    Handles both real UCP response shapes: a "products" list (search /
    lookup) and a single "product" (get_product). Malformed/non-JSON
    input passes through unchanged rather than raising — this is a
    safety net on top of an already-working response, not something
    that should itself become a new failure mode.
    """
    try:
        data = json.loads(raw_json_text)
    except (json.JSONDecodeError, TypeError):
        return raw_json_text

    if isinstance(data.get("products"), list):
        kept, dropped = [], 0
        for product in data["products"]:
            if _meets_readiness(str(product.get("id") or "")):
                kept.append(product)
            else:
                dropped += 1
        data["products"] = kept
        if dropped:
            data.setdefault("messages", []).append(
                {
                    "type": "info",
                    "code": "not_shown_below_readiness_threshold",
                    "content": f"{dropped} product(s) omitted: below the readiness threshold",
                }
            )
        return json.dumps(data)

    if isinstance(data.get("product"), dict):
        product = data["product"]
        if not _meets_readiness(str(product.get("id") or "")):
            return json.dumps(
                {
                    "ucp": data.get("ucp", {}),
                    "messages": [
                        {
                            "type": "error",
                            "code": "not_ucp_ready",
                            "content": f"{product.get('id')}: below the readiness threshold — not eligible for checkout",
                            "severity": "unrecoverable",
                        }
                    ],
                }
            )
        return raw_json_text

    return raw_json_text


async def ask_catalog(question: str, max_tool_iterations: int = 5) -> dict[str, Any]:
    """Runs the full live loop: sends `question` to Gemini with the
    three catalog tools available, executes each tool call Gemini
    requests as a REAL MCP call against this project's own mounted
    /ucp/mcp endpoint (not a direct in-process call to ucp_catalog.py —
    see mcp_agent_bridge.py's module docstring for why that distinction
    is deliberate), feeds the result back, and repeats until Gemini
    returns a final text answer or max_tool_iterations is hit.

    Returns {"answer": str, "tool_calls": [{"tool": str, "arguments":
    dict}, ...]} — the tool_calls list is what lets the frontend show
    "the MCP searched the catalog" as a visible step, not just a final
    answer that could otherwise look like it came from nowhere.
    """
    from google.genai import types
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    client, model_name = _build_gemini_client()
    tool = _tool_declarations()
    # Confirmed working live (first real test): the function-calling
    # loop itself. What it exposed: with no guidance on HOW to answer,
    # Gemini handed back the model's full instinct to be thorough —
    # every field of get_product's result, even for a narrow
    # price/availability question. This system_instruction is the fix.
    # Much lower structural risk than the tool-calling shape above
    # (system_instruction is a standard parameter on nearly every
    # generative AI SDK) — but the WORDING is untuned prompt
    # engineering, not something to treat as finished on the first try.
    config = types.GenerateContentConfig(
        tools=[tool],
        system_instruction=(
            "You are a shopping assistant answering a shopper's question about "
            "a product catalog. Answer ONLY what was actually asked - if they "
            "ask about price and availability, give just those two things in a "
            "short, conversational sentence or two. Do not recite a full "
            "product spec sheet (every attribute, feature, description) unless "
            "the shopper explicitly asks for full details or a complete "
            "listing. If a search doesn't find a clear match, it's fine to try "
            "one or two different search terms, but if nothing turns up after "
            "that, tell the shopper honestly that you don't see that item in "
            "the catalog rather than continuing to retry indefinitely. "
            "IMPORTANT CAPABILITY LIMIT: none of your tools can filter or sort "
            "by a price range, or list 'everything over/under X' - they only "
            "support free-text search by name/category/keyword, and exact-ID "
            "lookup. If asked for something like 'items over 10k' or 'sort by "
            "price', say plainly that you can search by name or category but "
            "can't filter or sort by price range - do not attempt to work "
            "around this with repeated broad searches."
        ),
    )

    contents: list[Any] = [types.Content(role="user", parts=[types.Part(text=question)])]
    tool_calls_made: list[dict[str, Any]] = []

    async with streamable_http_client(_mcp_url()) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()

            for _ in range(max_tool_iterations):
                response = client.models.generate_content(
                    model=model_name, contents=contents, config=config
                )
                candidate_content = response.candidates[0].content
                contents.append(candidate_content)

                function_call = None
                for part in candidate_content.parts:
                    if getattr(part, "function_call", None):
                        function_call = part.function_call
                        break

                if function_call is None:
                    # No tool call this turn — Gemini is done. response.text
                    # concatenates all text parts, same accessor
                    # catalog_agent.py already relies on for its own
                    # (non-tool) calls.
                    return {"answer": (response.text or "").strip(), "tool_calls": tool_calls_made}

                tool_name = function_call.name
                tool_args = dict(function_call.args or {})
                tool_calls_made.append({"tool": tool_name, "arguments": tool_args})

                mcp_result = await session.call_tool(
                    tool_name,
                    {
                        "catalog": tool_args,
                        "meta": {"ucp-agent": {"profile": "https://storemind-ingest.example/agent-bridge.json"}},
                    },
                )
                # MCP tool results come back as a list of content blocks
                # (see test_mcp_http_client.py's own result.content) —
                # concatenating their text is the same defensive,
                # never-assume-one-shape handling used elsewhere in this
                # project (e.g. layer0_validator's fallback layers).
                result_text = "\n".join(
                    getattr(block, "text", "") for block in (mcp_result.content or [])
                ) or json.dumps({"error": "empty tool result"})

                # HARD filter, applied before Gemini ever sees this
                # result — see _filter_ucp_response_for_readiness's own
                # docstring for why this lives here rather than as a
                # prompt instruction, and why it's here rather than in
                # the shared ucp_mapping.py.
                result_text = _filter_ucp_response_for_readiness(result_text)

                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=tool_name, response={"result": result_text}
                            )
                        ],
                    )
                )

    raise AgentBridgeError(
        f"No final answer after {max_tool_iterations} tool calls — the model kept "
        f"calling tools without concluding. Tool calls made: {tool_calls_made}"
    )
    
    