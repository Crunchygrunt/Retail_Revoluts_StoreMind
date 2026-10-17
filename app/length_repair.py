"""
ACP/UCP field-length repair — bounded compress-and-recheck loop with a
guaranteed deterministic fallback.

This is the self-repair LOOP of the ACP conformance work (the brand
cascade in brand_resolution.py is a fallback CHAIN — each stage tried
once, since a failed vision read won't succeed on retry without new
input). An LLM compression pass isn't guaranteed to land under a
character limit on the first attempt, so this retries a bounded number
of times, then falls back to deterministic truncation that's GUARANTEED
to satisfy the limit no matter what any model produces.

Built on two accomplished implementations rather than hand-rolled:

  - `tenacity` (https://github.com/jd/tenacity, Apache 2.0) for the
    retry loop. Its `retry_if_result` is the specific feature that
    matters here: this loop retries on an unsatisfactory RETURN VALUE
    (text still over the limit), not just on exceptions, which is the
    less common retry shape that most hand-rolled loops get subtly
    wrong. `stop_after_attempt` bounds it; `retry_error_callback`
    returns the last value instead of raising RetryError, so an
    exhausted loop falls through to truncation rather than blowing up
    the ingest batch.

  - `textwrap.shorten` (stdlib) for word-boundary truncation, with ONE
    documented exception below.

Why textwrap.shorten is the primary path but not the only one: verified
in testing, `shorten` returns ONLY the placeholder when the text
contains no break opportunity within the width — a 300-character
unbroken string comes back as a single "…", losing all content. That's
exactly the scriptio-continua case this codebase already handles
elsewhere (see readiness_score.py's note on "NICELONGYIFORMEN" and its
Thai/Khmer/Lao/Myanmar script detection): a product name in a script
that doesn't use whitespace has no break opportunity at all, so a Thai
product title would be annihilated rather than truncated. `shorten`
also collapses runs of whitespace and newlines. So it's used first, and
a hard character-slice fallback catches the no-break-opportunity case.
That fallback is kept custom because the failure it covers is
documented and reproducible, not because a library wasn't looked for.

The "ask an LLM to shorten this" call is backend-specific (needs a live
Gemini client), so it's injected as `compress_fn` rather than imported.
That keeps this module's loop/bound/fallback logic fully unit-testable
with no network: catalog_agent.py and catalog_agent_aistudio.py pass a
real Gemini call, catalog_agent_mock.py passes compress_fn=None (which
goes straight to truncation and proves that path independently).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from textwrap import shorten
from typing import Callable

from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
)
MAX_COMPRESSION_ATTEMPTS = 2
ELLIPSIS = "\u2026"  # reserved from the truncation budget so a cut field is visibly marked as cut

CompressFn = Callable[[str, int], str]  # (text, target_limit) -> shortened text


@dataclass
class RepairResult:
    text: str
    original_length: int
    final_length: int
    repaired: bool  # True if the text was changed at all
    method: str  # "none" | "compressed" | "truncated"
    attempts: int = 0
    notes: list[str] = field(default_factory=list)


def _return_last_value(retry_state: RetryCallState):
    """tenacity retry_error_callback. The documented idiom is
    `retry_state.outcome.result()`, but that RE-RAISES when the final
    attempt failed with an exception — which would defeat the whole
    point of falling through to truncation. Checking `.failed` first
    and returning None instead makes an exhausted-by-exception loop
    indistinguishable from an exhausted-by-bad-result loop at the call
    site: both mean "compression didn't work, truncate instead"."""
    outcome = retry_state.outcome
    if outcome is None or outcome.failed:
        return None
    return outcome.result()


def _deterministic_truncate(text: str, limit: int) -> str:
    """Guaranteed-under-limit truncation. textwrap.shorten first (proper
    word-boundary handling, stdlib); hard slice as the documented
    fallback for text with no break opportunity — see module docstring
    for why that case can't be left to shorten alone."""
    if limit <= 0:
        return ""

    try:
        candidate = shorten(text, width=limit, placeholder=ELLIPSIS)
    except ValueError:
        # shorten raises when width < len(placeholder).
        candidate = ""

    # Did shorten keep any ACTUAL content, or collapse to a bare
    # placeholder? The latter is the no-break-opportunity case.
    if candidate.replace(ELLIPSIS, "").strip():
        return candidate[:limit]

    budget = max(limit - len(ELLIPSIS), 0)
    if budget <= 0:
        return text[:limit]
    return (text[:budget].rstrip() + ELLIPSIS)[:limit]


def repair_field_length(
    text: str,
    limit: int,
    compress_fn: CompressFn | None = None,
    max_attempts: int = MAX_COMPRESSION_ATTEMPTS,
) -> RepairResult:
    """Ensures `text` fits within `limit` characters.

    Returns it unchanged if it already does (method="none"). Otherwise
    tries compress_fn up to max_attempts times via tenacity, retrying
    on either an exception OR a still-too-long result. If no attempt
    lands under the limit — or compress_fn is None — deterministic
    truncation guarantees the limit regardless. This function never
    returns text over the limit, by any path.
    """
    original_length = len(text)
    if original_length <= limit:
        return RepairResult(
            text=text, original_length=original_length, final_length=original_length,
            repaired=False, method="none",
        )

    notes: list[str] = []
    state = {"current": text, "attempts": 0}

    if compress_fn is not None:
        def _attempt() -> str | None:
            """One compression attempt. Feeds the CURRENT text (i.e. the
            previous attempt's output, if any) back in, so a partial
            shortening isn't thrown away on retry."""
            state["attempts"] += 1
            candidate = compress_fn(state["current"], limit)
            if isinstance(candidate, str) and candidate.strip():
                state["current"] = candidate.strip()
                return state["current"]
            return None  # empty/whitespace-only -> treated as a failed attempt

        def _unsatisfactory(result) -> bool:
            return result is None or len(result) > limit

        def _record_attempt(retry_state: RetryCallState) -> None:
            """tenacity `after` callback — runs after each attempt that
            didn't satisfy the retry condition. Without this, tenacity
            swallows the per-attempt failure reason internally and the
            caller can't tell an API error apart from a model that
            simply didn't shorten enough. That distinction matters
            during a live demo, so it's recorded into notes here."""
            outcome = retry_state.outcome
            n = retry_state.attempt_number
            if outcome is None:
                return
            if outcome.failed:
                # tenacity's outcome is a Future subclass — .exception()
                # is the public accessor (never .result(), which would
                # re-raise here).
                notes.append(f"compression attempt {n} raised: {outcome.exception()!r}")
            else:
                value = outcome.result()
                if value is None:
                    notes.append(f"compression attempt {n} returned empty text")
                else:
                    notes.append(f"compression attempt {n} still over limit ({len(value)} > {limit})")

        retryer = Retrying(
            stop=stop_after_attempt(max_attempts),
            retry=(retry_if_result(_unsatisfactory) | retry_if_exception_type(Exception)),
            retry_error_callback=_return_last_value,
            after=_record_attempt,
            reraise=False,
        )

        try:
            outcome = retryer(_attempt)
        except Exception as exc:  # noqa: BLE001 — belt-and-braces; a failed repair must never kill ingest
            notes.append(f"compression loop raised unexpectedly: {exc}")
            outcome = None

        if isinstance(outcome, str) and len(outcome) <= limit:
            return RepairResult(
                text=outcome, original_length=original_length, final_length=len(outcome),
                repaired=True, method="compressed", attempts=state["attempts"], notes=notes,
            )
        notes.append(
            f"compression did not land under {limit} chars after {state['attempts']} attempt(s)"
        )

    truncated = _deterministic_truncate(state["current"], limit)
    notes.append(f"fell back to deterministic truncation after {state['attempts']} compression attempt(s)")
    return RepairResult(
        text=truncated, original_length=original_length, final_length=len(truncated),
        repaired=True, method="truncated", attempts=state["attempts"], notes=notes,
    )
    