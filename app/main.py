"""
StoreMind ingestion + storefront + fraud detection service.

Endpoints:
    GET  /health                liveness check for Cloud Run
    POST /ingest                 accepts a JSON array of raw catalog rows,
                                  runs Layer-0 -> Catalog Structuring Agent
                                  (now with a bounded reflexion retry loop
                                  — see reflexion.py) -> readiness scoring
                                  -> store, returns a summary the demo can
                                  narrate live
    GET  /products                lists structured products in the store
    GET  /storefront/search       agent-facing catalog search
    POST /storefront/checkout     fail-closed mock checkout — blocks
                                  below-threshold products
    GET  /decision-log            every gate decision, with the reason
    POST /fraud/check             assess a single return request
    GET  /fraud/test-harness      runs all 5 labeled scenarios, returns
                                  an accuracy readout (the actual demo
                                  evidence for the Fraud Signal Detector)

Run locally:
    uvicorn main:app --reload --port 8080

Deploy: see deploy.sh in the project root.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import contextlib
from ucp_mcp_server import configure_mcp_store, mcp as ucp_mcp

from dotenv import load_dotenv

load_dotenv("deploy.env", override=True)  # reads app/.env if present — see .env.example
# Separate file, same mechanism: deploy.env is infrastructure config
# (GCP project, LLM/store backend selection, model names) — this project's
# own operator, not the merchant. seller.env is business/identity config
# the MERCHANT'S OWN facts belong in (name, policies, GSTIN/PAN/Udyam/
# UPI/WhatsApp identity, ONDC packer + fulfillment policy) — kept in its
# own file so an infra change (swapping LLM backend, say) never risks
# touching merchant-specific config, and vice versa. override=True for
# the same reason as deploy.env's own call: the file is the authoritative
# source, not whatever happened to already be in the shell's environment.
load_dotenv("seller.env", override=True)

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from ucp_routes import build_ucp_router
from ucp_eligibility import derive_ucp_eligibility
from ondc_eligibility import derive_ondc_eligibility

from layer0_validator import Layer0Validator
from readiness_score import compute_readiness_score
from fraud_detector import FraudSignalDetector
from currency_inference import infer_currency, apply_inferred_currency
from brand_resolution import infer_brand_by_category, resolve_brand, BrandInferenceResult
from acp_schema import (
    DEFAULT_AVAILABILITY,
    check_acp_conformance,
    derive_eligibility,
)
from seller_profile import load_seller_profile
from image_hosting import LOCAL_MEDIA_DIR, build_image_store, resolve_image_url
from product_page import build_product_url, render_product_page
from xlsx_ingest import XlsxParseError, parse_xlsx_bytes
import storefront

# -- reflexion (added): self-correcting retry loop around structuring,
# see reflexion.py / reflexion_evaluator.py / reflection_memory.py for
# the full design. run_reflexive_structuring is the loop orchestrator;
# check_grounding is called inside /ingest's own attempt_fn (not here)
# so it runs BEFORE scoring, not after; ReflectionEntry/
# build_reflection_memory wire up the cross-batch memory layer at
# startup below. --
from reflexion import AttemptOutcome, run_reflexive_structuring
from reflexion_evaluator import check_grounding
from reflection_memory import ReflectionEntry, build_reflection_memory

from fastapi.middleware.cors import CORSMiddleware

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with ucp_mcp.session_manager.run():
        yield

app = FastAPI(title="StoreMind — Catalog Ingestion", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://storemind-508614.web.app"],       # tighten to your real frontend origin before demo day
    allow_methods=["*"],
    allow_headers=["*"],
)


validator = Layer0Validator()

# ---- backend selection --------------------------------------------------
# STOREMIND_LLM_BACKEND:   mock (default, offline) | aistudio | vertexai
# STOREMIND_STORE_BACKEND: local (default, offline) | firestore
#
# All three LLM backends and both store backends expose identical function
# signatures, so this is the ONLY place that needs to change when you move
# from local testing to real GCP — everything else in this file is backend-
# agnostic.

LLM_BACKEND = os.environ.get("STOREMIND_LLM_BACKEND", "mock")
STORE_BACKEND = os.environ.get("STOREMIND_STORE_BACKEND", "local")


def _build_agent():
    if LLM_BACKEND == "vertexai":
        from catalog_agent import CatalogStructuringAgent

        return CatalogStructuringAgent()
    if LLM_BACKEND == "aistudio":
        from catalog_agent_aistudio import GeminiAIStudioAgent

        return GeminiAIStudioAgent()
    from catalog_agent_mock import MockCatalogStructuringAgent

    return MockCatalogStructuringAgent()


def _build_store():
    if STORE_BACKEND == "firestore":
        import firestore_client as store
    else:
        import local_store as store
    return store


agent = _build_agent()
store = _build_store()

configure_mcp_store(store)
# Same store reference, not a second construction — see
# mcp_agent_bridge.py's own docstring on why this readiness cross-check
# lives here rather than inside the shared ucp_mapping.py.
from mcp_agent_bridge import configure_bridge_store

configure_bridge_store(store)
ucp_mcp.settings.streamable_http_path = "/"
app.mount("/ucp/mcp", ucp_mcp.streamable_http_app())

app.include_router(build_ucp_router(store, store))
# STOREMIND_IMAGE_BACKEND: local (default, offline) | gcs — same
# swappable-backend convention as the LLM and store selectors above, so
# the image-hosting path is fully demoable with no GCP access.
image_store = build_image_store()

# Merchant-level ACP facts (seller_name, policy URLs). Loaded once at
# startup, not per row — they're identical for every product this
# instance ingests. See seller_profile.py.
seller_profile = load_seller_profile()

# All five identity checks (GSTIN, Udyam, PAN, UPI, WhatsApp phone) run
# as PART of load_seller_profile() above (offline, no live API calls at
# all - see that module's docstring for why) — this just records the
# resolved outcome once, at startup, rather than re-checking (and
# re-logging) it on every /ingest call. "Unset" and "wrong" are
# deliberately NOT treated the same: nothing configured never blocks
# anything below; something configured that doesn't add up to a
# verified identity does, via seller_profile.blocks_ingestion in
# /ingest (unchanged — it reads the same property name as before, just
# backed by richer either-or logic now).
if seller_profile.verified:
    store.log_seller_event(
        "seller_verified",
        f"Seller identity verified via {seller_profile.verification_method}",
    )
elif seller_profile.any_verification_field_configured:
    store.log_seller_event(
        "seller_verification_failed",
        "Seller identity could not be verified: " + "; ".join(seller_profile.verification_reasons),
    )
# else: nothing configured at all — no log entry, matches how a missing
# seller_name/policy/tos is handled (reported as a gap where relevant,
# not actively logged as an event).

# -- reflexion memory (added): STOREMIND_REFLECTION_MEMORY = keyword
# (default) | firestore_vector | off — same swappable-backend
# convention as STOREMIND_LLM_BACKEND / STOREMIND_STORE_BACKEND /
# STOREMIND_IMAGE_BACKEND above. embed_fn is only available on the two
# LIVE catalog agents (see embed_text on catalog_agent.py /
# catalog_agent_aistudio.py) — the mock agent has none, so
# firestore_vector silently degrades to a no-op memory under the mock
# backend rather than erroring (see reflection_memory.build_reflection_memory).
# STOREMIND_REFLEXION_MAX_RETRIES bounds how many retries EACH row's
# own reflexion loop gets — 2 by default, same bound discussed for the
# feature's design.
REFLECTION_MEMORY_BACKEND = os.environ.get("STOREMIND_REFLECTION_MEMORY", "keyword")
REFLEXION_MAX_RETRIES = int(os.environ.get("STOREMIND_REFLEXION_MAX_RETRIES", "2"))
_embed_fn = getattr(agent, "embed_text", None)
reflection_memory = build_reflection_memory(REFLECTION_MEMORY_BACKEND, store, embed_fn=_embed_fn)

# FraudSignalDetector resolves its own backend from STOREMIND_LLM_BACKEND
# internally — no separate factory function needed, unlike the catalog
# agent's mock/aistudio/vertexai split across three files.
fraud_detector = FraudSignalDetector()
FRAUD_SCENARIOS_PATH = Path(__file__).parent / "fraud_scenarios.json"


class IngestRequest(BaseModel):
    rows: list[dict[str, Any]]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/parse/xlsx")
async def parse_xlsx(file: UploadFile = File(...)) -> dict[str, Any]:
    """Converts an uploaded .xlsx file into the same row-object shape
    /ingest expects. PARSING ONLY — no Layer-0, no Gemini, no store
    write happens here, mirroring how the frontend's CSV upload also
    only fills the review textarea rather than ingesting directly. The
    actual pipeline is only ever entered through POST /ingest, from
    pasted JSON, an uploaded CSV, or this endpoint's output — one
    canonical entry point regardless of how the rows arrived.

    See xlsx_ingest.py for why this is server-side (openpyxl) rather
    than a browser-side JS library, unlike the CSV path.
    """
    data = await file.read()
    try:
        result = parse_xlsx_bytes(data)
    except XlsxParseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return result


@app.post("/ingest")
def ingest(request: IngestRequest) -> dict[str, Any]:
    # Fail-closed when a seller identity field WAS configured but the
    # either-or verification (GSTIN / Udyam / UPI+PAN / UPI+WhatsApp —
    # see seller_profile.py) didn't add up — checked once at startup,
    # not re-validated per request. Nothing configured at all never
    # reaches this branch (blocks_ingestion is False whenever no
    # identity field was set) — this only fires for a genuine
    # configuration gap or error, the same severity class as a
    # malformed price, not a missing optional field.
    if seller_profile.blocks_ingestion:
        return JSONResponse(
            {
                "error": "seller identity could not be verified",
                "reason": "; ".join(seller_profile.verification_reasons),
                "submitted": len(request.rows),
                "layer0_accepted": 0,
                "layer0_rejected": 0,
            },
            status_code=403,
        )

    accepted, rejected = validator.validate_batch(request.rows)

    cleaned_rows = [r.cleaned_row for r in accepted]

    # Computed ONCE for the whole batch, not per row — unchanged from
    # before, and still correct with reflexion in place: a currency-less
    # row gets the SAME inferred answer on every attempt within its own
    # retry loop, since inferred_currency doesn't change mid-row. See
    # currency_inference.py.
    inferred_currency = infer_currency(store)
    # Same once-per-batch reasoning as inferred_currency above; see
    # brand_resolution.py for why this is infer_brand_by_category, not
    # infer_brand (category-scoping).
    inferred_brands_by_category = infer_brand_by_category(store)
    _no_brand_inference = BrandInferenceResult(brand=None, confidence=0.0, sample_size=0, applicable=False)

    structured_summary = []
    for cleaned_row in cleaned_rows:
        row_id = str(cleaned_row.get("sku") or cleaned_row.get("id") or "unknown")

        def attempt_fn(cleaned_row, row_id, reflection_note, previous_structured, _row_id=row_id):
            # _row_id default-arg trick: binds THIS iteration's row_id
            # into the closure rather than the loop variable, which
            # would otherwise be the same late-binding bug Python
            # closures-in-loops are famous for — every attempt_fn
            # would silently see the LAST row's id otherwise. Verified
            # against a two-row simulation before trusting this.
            result = agent.structure_row(cleaned_row, _row_id, reflection_note=reflection_note)

            if not result.success or result.structured is None:
                return AttemptOutcome(
                    success=False, structured=None, extraction_method=result.extraction_method,
                    warnings=result.warnings, raw_model_output=result.raw_model_output,
                    readiness_score=None, meets_readiness_threshold=None, readiness_notes=[],
                    acp_missing_fields=[], acp_invalid_fields=[], ondc_blockers=[],
                )

            # Grounding check FIRST, before any scoring below touches
            # the structured dict — see reflexion.py's own module
            # docstring for why the order matters: scoring a
            # fabricated value before stripping it would score the
            # fabrication, not the corrected result.
            grounding_rejected = check_grounding(before=previous_structured, after=result.structured, raw_row=cleaned_row)
            for f in grounding_rejected:
                if previous_structured and f in previous_structured:
                    result.structured[f] = previous_structured[f]
                else:
                    result.structured.pop(f, None)

            # -- everything below is EXACTLY the post-processing that
            # used to run once per row, now run once per ATTEMPT: same
            # currency/brand resolution, same ACP url/image_url
            # resolution, same conformance/eligibility derivation,
            # same readiness scoring. image_hosting.py's store() and
            # build_product_url() are both already content-addressed /
            # deterministic (see their own docstrings), so calling them
            # again on a retry re-checks rather than re-uploads or
            # re-derives anything — this relies on that EXISTING
            # idempotency, not new idempotency added here. --
            apply_inferred_currency(result.structured, inferred_currency)
            row_category = str(result.structured.get("category") or "").strip()
            inferred_brand = inferred_brands_by_category.get(row_category, _no_brand_inference)
            resolve_brand(result.structured, inferred_brand, seller_name=seller_profile.name)
            score_result = compute_readiness_score(result.structured, result.extraction_method, raw_row=cleaned_row)
            result.structured["readiness_score"] = score_result.score
            result.structured["readiness_breakdown"] = score_result.breakdown
            result.structured["meets_readiness_threshold"] = score_result.meets_threshold
            result.structured["gate_status"] = "eligible" if score_result.meets_threshold else "blocked"

            # -- ACP field resolution: url and image_url --
            # Both are ACP-required and both are things the target
            # segment typically doesn't have. Neither is invented: the
            # url is a page StoreMind actually serves (see /p/{sku}
            # below), and the image_url only exists if the seller
            # supplied real image bytes.
            # -- ACP required fields that aren't catalog data --
            # seller_name is required on every row; availability is
            # required, and per the real ACP spec an unrecognised
            # value REJECTS THE ROW — so a bad value must be passed
            # through AS-IS, not silently rewritten into something
            # valid. (An earlier version of this normalised anything
            # unrecognised to "unknown" here, which defeated
            # check_acp_conformance's own invalid-value detection
            # below before it ever got to run: by the time conformance
            # checked, the bad value had already been quietly turned
            # into a value the spec considers perfectly fine. Confirmed
            # live — a row with availability="maybe_in_stock" was
            # landing Tier 2 instead of the intended Tier 3.)
            if seller_profile.name:
                result.structured["seller_name"] = seller_profile.name
            raw_availability = str(cleaned_row.get("availability") or "").strip().lower()
            result.structured["availability"] = raw_availability or DEFAULT_AVAILABILITY

            url_result = build_product_url(cleaned_row, _row_id)
            result.structured["url"] = url_result.url
            result.structured["url_source"] = url_result.source

            image_result = resolve_image_url(cleaned_row, image_store)
            if image_result.url:
                result.structured["image_url"] = image_result.url
            result.structured["image_source"] = image_result.source

            # ACP/UCP conformance is REPORTED, never used to exclude.
            # Enforcement is in derive_eligibility, and its worst
            # outcome is discovery-only — still searchable, still
            # ad-eligible. See acp_schema.
            conformance = check_acp_conformance(result.structured, seller_profile=seller_profile)
            eligibility = derive_eligibility(
                result.structured, conformance, score_result.meets_threshold
            )
            result.structured["acp_conformant"] = conformance.conformant
            result.structured["acp_gaps"] = {
                "missing": conformance.missing_fields,
                "oversized": conformance.oversized_fields,
                "invalid": conformance.invalid_fields,
                "checkout_blockers": conformance.checkout_blockers,
                "remediation": conformance.remediation,
            }
            result.structured["acp_tier"] = eligibility.tier
            result.structured["is_eligible_search"] = eligibility.is_eligible_search
            result.structured["is_eligible_checkout"] = eligibility.is_eligible_checkout

            ucp_eligibility = derive_ucp_eligibility(result.structured)
            result.structured["ucp_tier"] = ucp_eligibility.ucp_tier
            result.structured["is_ucp_catalog_listable"] = ucp_eligibility.is_ucp_catalog_listable
            result.structured["is_ucp_checkout_eligible"] = ucp_eligibility.is_ucp_checkout_eligible
            result.structured["ucp_blockers"] = ucp_eligibility.ucp_blockers

            # Unlike ACP/UCP, ONDC needs seller_profile directly — the
            # statutory/policy gate depends on packer identity and
            # store-wide fulfillment policy, not just the structured
            # product itself. See ondc_mapping.py's own docstring for
            # why this is a single boolean, not a three-tier split.
            ondc_eligibility = derive_ondc_eligibility(result.structured, seller_profile)
            result.structured["is_ondc_listable"] = ondc_eligibility.is_ondc_listable
            result.structured["ondc_blockers"] = ondc_eligibility.ondc_blockers
            result.structured["ondc_domain"] = ondc_eligibility.ondc_domain

            return AttemptOutcome(
                success=True, structured=result.structured, extraction_method=result.extraction_method,
                warnings=result.warnings, raw_model_output=result.raw_model_output,
                readiness_score=score_result.score, meets_readiness_threshold=score_result.meets_threshold,
                readiness_notes=score_result.notes,
                acp_missing_fields=conformance.missing_fields, acp_invalid_fields=conformance.invalid_fields,
                ondc_blockers=ondc_eligibility.ondc_blockers, grounding_rejected_fields=grounding_rejected,
            )

        def _log_attempt(record, _row_id=row_id):
            store.log_decision(
                _row_id, "reflexion_attempt",
                f"attempt {record.attempt}: score={record.outcome.readiness_score}, "
                f"model_fixable={record.classification.fixable_tags}, "
                f"grounding_rejected={record.outcome.grounding_rejected_fields}",
            )

        reflexion_result = run_reflexive_structuring(
            cleaned_row=cleaned_row, raw_row=cleaned_row, row_id=row_id,
            attempt_fn=attempt_fn, max_retries=REFLEXION_MAX_RETRIES, on_attempt=_log_attempt,
            # This is the line that closes the loop this whole feature
            # was missing: reflection_memory.save() was already wiring
            # validated corrections INTO the store, but nothing was
            # ever reading them back out. Without this, memory would
            # fill up forever and never once influence a future row.
            retrieve_memory=reflection_memory.retrieve,
        )
        best = reflexion_result.best
        result = best.outcome

        # Save to cross-batch memory ONLY when a retry ACTUALLY paid
        # off cleanly: it retried, the best attempt beat the first
        # one, and the winning attempt itself introduced no ungrounded
        # value — see reflection_memory.py's own poisoning-guard note.
        # A reflection that itself needed correcting is not something
        # a future row should imitate.
        if (
            reflexion_result.retried and reflexion_result.improved
            and not best.outcome.grounding_rejected_fields and best.reflection_note
            and result.structured
        ):
            reflection_memory.save(ReflectionEntry(
                category=str(result.structured.get("category") or ""),
                tags=best.classification.fixable_tags or reflexion_result.attempts[0].classification.fixable_tags,
                note=best.reflection_note,
                score_before=reflexion_result.attempts[0].outcome.readiness_score or 0.0,
                score_after=best.outcome.readiness_score or 0.0,
                sku=row_id,
            ))

        if result.success and result.structured:
            store.write_structured_product(
                result.structured, sku=row_id, extraction_method=result.extraction_method
            )
        structured_summary.append(
            {
                "row_id": row_id,
                "success": result.success,
                "extraction_method": result.extraction_method,
                "warnings": result.warnings,
                "readiness_score": result.readiness_score,
                "meets_readiness_threshold": result.meets_readiness_threshold,
                "readiness_notes": result.readiness_notes,
                "currency_inferred": result.structured.get("currency_inferred", False) if result.success else False,
                "brand": result.structured.get("brand") if result.success else None,
                "brand_source": result.structured.get("brand_source") if result.success else None,
                "acp_repair_notes": result.structured.get("acp_repair_notes", []) if result.success else [],
                "acp_conformant": result.structured.get("acp_conformant") if result.success else None,
                "acp_gaps": result.structured.get("acp_gaps") if result.success else None,
                "acp_tier": result.structured.get("acp_tier") if result.success else None,
                "is_eligible_search": result.structured.get("is_eligible_search") if result.success else None,
                "is_eligible_checkout": result.structured.get("is_eligible_checkout") if result.success else None,
                "ucp_tier": result.structured.get("ucp_tier") if result.success else None,
                "is_ucp_catalog_listable": result.structured.get("is_ucp_catalog_listable") if result.success else None,
                "is_ucp_checkout_eligible": result.structured.get("is_ucp_checkout_eligible") if result.success else None,
                "is_ondc_listable": result.structured.get("is_ondc_listable") if result.success else None,
                "ondc_blockers": result.structured.get("ondc_blockers") if result.success else None,
                "ondc_domain": result.structured.get("ondc_domain") if result.success else None,
                "url": result.structured.get("url") if result.success else None,
                "image_url": result.structured.get("image_url") if result.success else None,
                "image_source": result.structured.get("image_source") if result.success else None,
                # -- reflexion transparency block, for the demo: shows
                # the retry trail, not just the final outcome. --
                "reflexion": {
                    "attempts": len(reflexion_result.attempts),
                    "retried": reflexion_result.retried,
                    "improved": reflexion_result.improved,
                    "stopped_reason": reflexion_result.stopped_reason,
                    "score_before": reflexion_result.attempts[0].outcome.readiness_score,
                    "score_after": best.outcome.readiness_score,
                    "fabrication_blocked": reflexion_result.fabrication_blocked,
                    "reflection_notes": [a.reflection_note for a in reflexion_result.attempts if a.reflection_note],
                },
            }
        )

    for r in rejected:
        store.write_rejected_row(r.row_id, r.reasons, raw_row={})

    return {
        "llm_backend": LLM_BACKEND,
        "store_backend": STORE_BACKEND,
        "submitted": len(request.rows),
        "layer0_accepted": len(accepted),
        "layer0_rejected": len(rejected),
        "rejections": [{"row_id": r.row_id, "reasons": r.reasons} for r in rejected],
        "structuring_results": structured_summary,
        "structured_ok": sum(1 for r in structured_summary if r["success"]),
        "structured_failed": sum(1 for r in structured_summary if not r["success"]),
    }


@app.get("/seller")
def get_seller() -> dict[str, Any]:
    """Current seller identity verification status — resolved once at
    startup (see seller_profile.py's either-or logic across GSTIN /
    Udyam / UPI+PAN / UPI+WhatsApp), returned here so the frontend can
    show a persistent status banner without re-deriving it from
    /decision-log's event stream. Read-only; nothing here changes state.

    Deliberately does NOT echo the raw configured identifier values
    (no GSTIN/PAN/UPI ID/phone digits in this response) — this is a
    public, unauthenticated endpoint, and the verification outcome
    (method + reasons + holder type) is everything a status display
    needs without also republishing the seller's actual business
    identifiers. Note that verification_reasons CAN still include a
    raw value when a field was structurally malformed (not just a
    checksum mismatch) — see gstin_validator.py / pan_validator.py /
    udyam_validator.py's own reason strings — acceptable for a
    hackathon demo's own misconfigured input, not vetted as a hardened
    stance for real seller PII in production.
    """
    return {
        "name": seller_profile.name,
        "verified": seller_profile.verified,
        "verification_method": seller_profile.verification_method,
        "verification_reasons": list(seller_profile.verification_reasons),
        "any_verification_field_configured": seller_profile.any_verification_field_configured,
        "gstin_holder_type": seller_profile.gstin_holder_type,
        "pan_holder_type": seller_profile.pan_holder_type,
    }


@app.get("/products")
def get_products(limit: int = 50) -> dict[str, Any]:
    products = store.list_products(limit=limit)
    return {"count": len(products), "products": products}


@app.get("/storefront/search")
def storefront_search(q: str = "", category: str = "") -> dict[str, Any]:
    """The 'agent queries the catalog' half of the live demo."""
    results = storefront.search_products(store, query=q, category=category)
    return {"query": q, "category": category, "count": len(results), "products": results}


class CheckoutRequest(BaseModel):
    sku: str
    quantity: int = 1


@app.post("/storefront/checkout")
def storefront_checkout(request: CheckoutRequest) -> dict[str, Any]:
    """The 'agent transacts against the catalog' half of the live demo.
    Fail-closed: below-threshold products are rejected. See storefront.checkout()."""
    return storefront.checkout(store, sku=request.sku, quantity=request.quantity)


class McpAskRequest(BaseModel):
    question: str


@app.post("/mcp/ask")
async def mcp_ask(request: McpAskRequest) -> dict[str, Any]:
    """The MCP-native sibling of /storefront/search above: instead of a
    keyword query, a real natural-language question, answered by
    Gemini deciding whether and how to call the SAME mounted /ucp/mcp
    endpoint a real external agent (Gemini CLI) already proved works
    against this production catalog. See mcp_agent_bridge.py's module
    docstring — the function-calling piece here is NOT independently
    verified the way the rest of this file's endpoints are; test it
    with test_mcp_agent_bridge.py before trusting it live.

    AgentBridgeError (e.g. the model never reached a final answer) is
    returned as a normal JSON error rather than a 500 — same "one bad
    call doesn't crash the request" instinct as every other endpoint
    in this file.
    """
    from mcp_agent_bridge import AgentBridgeError, ask_catalog

    # Separate from ask_catalog's own max_tool_iterations cap: that
    # bounds HOW MANY round trips happen, this bounds HOW LONG they're
    # allowed to take in total. A slow-but-under-the-iteration-cap
    # request (several genuine Gemini + MCP round trips, each with
    # real network latency) could otherwise run past what a browser's
    # own fetch will wait for — which is exactly what a real "Failed
    # to fetch" with no server-side error at all looks like, giving
    # zero diagnostic information. 60s is a generous-but-bounded
    # guess, not tuned against a real measured p99 - adjust once
    # you've seen how long a genuine multi-tool-call answer actually
    # takes in practice.
    try:
        return await asyncio.wait_for(ask_catalog(request.question), timeout=60.0)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(
                "The catalog agent took longer than 60s to answer — most likely "
                "a question needing a capability no tool supports (e.g. a price "
                "range or sort), causing repeated searches rather than one "
                "failing fast. See mcp_agent_bridge.py's system_instruction."
            ),
        ) from None
    except AgentBridgeError as exc:
        # HTTPException, not a hand-rolled JSONResponse: produces
        # {"detail": ...}, the FIRST key api.js's error handling checks
        # — confirmed live that {"error": ...} (what this used to
        # return) fell through to a generic "Request failed with
        # status 502" instead of AgentBridgeError's actual, specific
        # message (which includes exactly which tool calls were made
        # before giving up — real diagnostic value that was being
        # silently discarded).
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/p/{sku}", response_class=HTMLResponse)
def product_page(sku: str):
    """The canonical product page backing ACP's `url` field.

    This is what makes the generated URL real rather than a placeholder:
    a seller with no website of their own gets a genuine, crawlable page
    carrying schema.org Product JSON-LD — which is the machine-readable
    contract shopping agents actually parse. See product_page.py.
    """
    product = store.get_product(sku)
    if product is None:
        return HTMLResponse(
            "<!DOCTYPE html><html><body><h1>Product not found</h1></body></html>",
            status_code=404,
        )
    return HTMLResponse(render_product_page(product))


@app.get("/media/{filename}")
def media(filename: str):
    """Serves locally-hosted product images (STOREMIND_IMAGE_BACKEND=local).

    Only needed offline — with the gcs backend, image URLs point at
    storage.googleapis.com and never reach this route. Filenames are
    content-addressed hex digests plus an extension, so the pattern
    check below is a tight whitelist rather than a general path
    sanitizer: anything containing a separator or traversal sequence
    simply doesn't match.
    """
    if not re.fullmatch(r"[0-9a-f]{8,64}\.jpg", filename):
        return JSONResponse({"error": "not found"}, status_code=404)

    path = (LOCAL_MEDIA_DIR / filename).resolve()
    media_root = LOCAL_MEDIA_DIR.resolve()
    if media_root not in path.parents or not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    return FileResponse(path, media_type="image/jpeg")


@app.get("/decision-log")
def get_decision_log(limit: int = 100) -> dict[str, Any]:
    """Every gate decision, for demo narration: what was blocked, what
    passed, and why."""
    entries = store.list_decision_log(limit=limit)
    return {"count": len(entries), "entries": entries}


class FraudCheckRequest(BaseModel):
    scenario: dict[str, Any]


@app.post("/fraud/check")
def fraud_check(request: FraudCheckRequest) -> dict[str, Any]:
    """Assess a single return request. For the demo evidence, prefer
    /fraud/test-harness — one flagged example proves nothing on its own."""
    result = fraud_detector.check_return(request.scenario)
    return {
        "return_id": result.return_id,
        "risk_level": result.risk_level,
        "flagged": result.flagged,
        "reasoning": result.reasoning,
        "signals": result.signals,
        "extraction_method": result.extraction_method,
        "warnings": result.warnings,
    }


@app.get("/fraud/test-harness")
def fraud_test_harness() -> dict[str, Any]:
    """Runs all 5 prepared, labeled return scenarios and returns an
    accuracy readout — this IS the demo evidence for the Fraud Signal
    Detector, per the build plan."""
    with open(FRAUD_SCENARIOS_PATH, encoding="utf-8") as f:
        scenarios = json.load(f)
    return fraud_detector.run_test_harness(scenarios)


@app.get("/reflexion/ab-harness")
def reflexion_ab_harness() -> dict[str, Any]:
    """Runs the labeled reflexion A/B comparison and returns a summary
    — the same demo role /fraud/test-harness plays for the Fraud
    Signal Detector: real evidence the feature helps, not just an
    assertion that it does.

    Passes _build_agent ITSELF (this file's own factory function, not
    a copy) as the agent factory — see reflexion_ab_harness.py's own
    module docstring for exactly what "without reflexion" means
    (max_retries=0, literally the same loop, never a second code
    path) and why each arm gets a fresh agent instance rather than a
    shared one (a real, found-by-running-it bug: a shared mock agent's
    internal output-style counter made the two arms unfairly
    non-comparable).
    """
    from reflexion_ab_harness import load_dataset, run_ab_comparison

    result = run_ab_comparison(load_dataset(), _build_agent, seller_profile)
    return result.to_dict()

