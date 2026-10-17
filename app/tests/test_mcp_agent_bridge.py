"""
Standalone sanity check for mcp_agent_bridge.py — the GENUINELY
UNVERIFIED piece (Gemini function-calling), same "test before trusting
it" discipline test_mcp_http_client.py already established for the MCP
mounting itself.

Cannot be run in a sandbox with no network/GCP credentials — this is
written for YOU to run, not something already confirmed working the
way test_mcp_http_client.py's result is.

PREREQUISITE: same as test_mcp_http_client.py — the server already
running with the MCP mount live:
    uvicorn main:app --reload --port 8080
And in a SECOND terminal:
    python test_mcp_agent_bridge.py

If this fails, the error will point at exactly which part of the
function-calling shape was guessed wrong (an AttributeError or
TypeError from the google-genai SDK itself, not a silent bad answer) —
fix that specific piece rather than re-guessing the whole thing.
"""

import asyncio
import os

from mcp_agent_bridge import ask_catalog


async def main():
    # Point at whichever port the local server is actually running on —
    # matches mcp_agent_bridge._mcp_url()'s own PORT env var convention.
    os.environ.setdefault("PORT", "8080")

    question = "Do you have any cotton kurtas, and what do they cost?"
    print(f"Asking: {question!r}\n")

    result = await ask_catalog(question)

    print("Tool calls made:")
    for call in result["tool_calls"]:
        print(f"  - {call['tool']}({call['arguments']})")

    print("\nFinal answer:")
    print(result["answer"])

    assert result["tool_calls"], (
        "Expected at least one tool call — if this is empty, Gemini answered "
        "without ever searching the catalog, which means either the question "
        "needs to be more obviously catalog-related, or the tool declarations "
        "aren't reaching the model correctly."
    )
    assert result["answer"], "Expected a non-empty final answer."

    print("\nPASSED — tool calling + real MCP round trip both worked.")
    print("Only after this passes, wire the frontend widget to /mcp/ask.")


if __name__ == "__main__":
    asyncio.run(main())
    