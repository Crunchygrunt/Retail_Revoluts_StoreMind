"""
Real-HTTP test of the mounted MCP endpoint, using the CONFIRMED mcp
1.x client API — streamablehttp_client + ClientSession, NOT the
mcp.Client class (that's a v2-only addition; you're on 1.30.0).

This corrects two wrong guesses in a row on this exact surface
(.streamable_http_app() then mcp.Client) — both were v2-shaped APIs
guessed without checking your actual installed version first. This
pattern is instead corroborated across multiple independent, dated
(2026) sources specifically using mcp 1.29-1.30, including a GitHub
issue reproduction run on exactly 1.29.0. Real corroboration, not a
single source.

PREREQUISITE: the mount must already be wired into main.py and the
server already running locally:
    from ucp_mcp_server import configure_mcp_store, mcp
    configure_mcp_store(store)
    app.mount("/ucp/mcp", mcp.http_app())
Then, in one terminal:
    uvicorn main:app --reload --port 8080
And in a SECOND terminal, run this script:
    python test_mcp_http_client.py
"""

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# URL = "http://127.0.0.1:8080/ucp/mcp"
URL = "https://storemind-ingest-444891828026.us-central1.run.app/ucp/mcp"

async def main():
    async with streamable_http_client(URL) as (read, write, get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Session initialized. Session ID:", get_session_id())

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print("Tools visible over real HTTP:", tool_names)
            assert {"search_catalog", "lookup_catalog", "get_product"} <= set(tool_names), (
                "Expected all three catalog tools to be visible — if this fails but "
                "test_ucp_mcp_server.py's local logic tests pass, the problem is "
                "specifically in the app.mount() / lifespan integration, not the tools."
            )

            print("\n-- calling lookup_catalog over real HTTP --")
            result = await session.call_tool(
                "lookup_catalog",
                {
                    "catalog": {"ids": ["BRAND-SELLERNAME-01"]},
                    "meta": {"ucp-agent": {"profile": "https://example.com/test-agent.json"}},
                },
            )
            print("isError:", result.isError)
            print("content:", result.content)

    print("\nDone. If this ran with no errors and all three tools were listed,")
    print("the mount is confirmed working over real HTTP — the last unverified")
    print("piece of ucp_mcp_server.py. Only after this passes, try a real agent")
    print("(Claude Desktop / Claude Code / Gemini) against the live deployed URL.")


if __name__ == "__main__":
    asyncio.run(main())
    
    