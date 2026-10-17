"""
Catalog Structuring Agent — Vertex AI backend.

Uses the unified google-genai SDK, NOT vertexai.generative_models — that
module was deprecated June 2025 and Google's SDK releases after June 24,
2026 remove it entirely. Code written against the old import would fail
with ModuleNotFoundError on a fresh install today. google-genai is the
same package already used by catalog_agent_aistudio.py; here it's pointed
at Vertex AI (vertexai=True) instead of a raw API key.

Authenticates via Application Default Credentials — no API key needed.
Locally: `gcloud auth application-default login`. On Cloud Run: the
service's attached service account (needs roles/aiplatform.user).

Requires (only imported when actually called, so this file still imports
cleanly without google-genai installed, e.g. for local testing of the
rest of the app with the mock backend):
    pip install google-genai
    GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION env vars set

Same public interface as catalog_agent_mock.MockCatalogStructuringAgent
and catalog_agent_aistudio.GeminiAIStudioAgent, so main.py swaps between
all three with one environment variable and zero code changes.

REFLEXION (added): structure_row() takes an optional reflection_note —
see reflexion.py / reflexion_evaluator.py. When present, it's prepended
to the prompt as clearly-separated meta-guidance about the agent's OWN
previous attempt, never merged into the raw_row JSON (which would make
it look like product data to structure, rather than guidance about how
to structure it). embed_text() is new too, used only by
reflection_memory.FirestoreVectorReflectionMemory for the cross-batch
memory layer — an enhancement, never something ingest depends on
succeeding.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from fallback_parser import FallbackParser
from length_repair import repair_field_length
from acp_schema import ACP_LENGTH_LIMITS

STRUCTURING_PROMPT = """You are structuring a single retail product record for an \
AI shopping agent to query. Given the raw product data below, respond with ONLY a \
JSON object (no markdown, no commentary) with exactly these fields:

- "name": cleaned product title (string)
- "category": best-guess product category (string)
- "description": a clear 1-2 sentence description suitable for an AI shopping \
agent to reason about (string)
- "attributes": key product attributes as a flat object, e.g. color, material, \
size (object)
- "keywords": 3-6 search keywords a shopper or shopping agent might use to find \
this product (array of strings)
- "price": the numeric price, no currency symbol (number)
- "brand": ONLY include this field if a product image is attached below AND a \
brand name or logo is clearly legible in it. If no image is attached, or no \
brand is clearly visible, OMIT this field entirely from your JSON — never guess \
a brand from the product name, category, or general styling.
- "net_quantity": the physical net quantity or measure of a single unit of this \
product, e.g. "250g", "500 ml", "1 unit", "pack of 3" — ONLY include this field \
if the raw product data below explicitly states a quantity, weight, volume, or \
measure. If none is stated, OMIT this field entirely — never guess or estimate \
one from the product name or category.
- "manufacture_date": the month and year this product (or its current batch) was \
manufactured or packed, in MM/YYYY format, e.g. "09/2026" — ONLY include this \
field if the raw product data below explicitly states one. If none is stated, \
OMIT this field entirely — never guess, estimate, or infer a date from context.

Raw product data:
{raw_row}
"""
# net_quantity/manufacture_date added for ondc_mapping.py's statutory gate:
# previously these were only ever incidentally present inside the free-form
# `attributes` dict (whatever key Gemini happened to choose), which meant
# ONDC eligibility depended on a coincidental match rather than a reliable
# extraction — the exact gap flagged when first building that gate. Named,
# top-level, and OPTIONAL, following brand's own proven anti-fabrication
# pattern: omit rather than guess. No new post-processing needed in
# structure_row() below — `structured = dict(parsed.data or {})` already
# passes any field Gemini returns straight through untouched, the same way
# it already does for `attributes`, `keywords`, etc.

COMPRESSION_PROMPT = """Shorten the following text to fit within {limit} characters \
while preserving its most important keywords and meaning. Respond with ONLY the \
shortened text — no quotes, no commentary, no explanation.

Text:
{text}
"""

# Same rate-limit handling as catalog_agent_aistudio.py — proven against
# real 429 responses there, kept identical for consistency between backends.
RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "rate limit", "quota")
DAILY_QUOTA_MARKER = "PerDay"
RETRY_DELAY_PATTERN = re.compile(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s")
MAX_RETRIES = 1
DEFAULT_RETRY_WAIT_SECONDS = 20.0
MAX_RETRY_WAIT_SECONDS = 65.0


@dataclass
class StructuringResult:
    row_id: str
    success: bool
    structured: dict[str, Any] | None
    extraction_method: str
    warnings: list[str]
    raw_model_output: str


class CatalogStructuringAgent:
    def __init__(self, project: str | None = None, location: str | None = None, model_name: str | None = None):
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        if not self.project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT not set. Set it in .env or as an env var — "
                "this is your GCP project ID, not a display name."
            )
        self.model_name = model_name or os.environ.get("STOREMIND_VERTEXAI_MODEL", "gemini-3.6-flash")
        self.parser = FallbackParser()
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        # Imported lazily so this module loads fine without google-genai
        # installed, matching the lazy-import pattern used elsewhere.
        from google import genai

        self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    @staticmethod
    def _fetch_image_bytes(image_ref: str, timeout: float = 5.0) -> tuple[bytes, str] | None:
        """Fetches image bytes for vision-based brand extraction (cascade
        stage 2 — see brand_resolution.py for stages 3-4). Returns
        (bytes, mime_type), or None on ANY failure: a bad URL, a
        timeout, a non-image response, whatever. A fetch failure here
        must never crash the ingest batch — it just means brand
        resolution falls through to the next cascade stage exactly as
        if no image had been provided at all. Only http(s) URLs are
        attempted; a local file path in a catalog row isn't something a
        Cloud Run instance can read.

        SANDBOX NOTE: this couldn't be exercised against a real image
        URL in the assistant's sandbox (no network access there) — same
        category of limitation already flagged for price-parser/regex
        during the SEA-strengthening session. Written against
        documented urllib behavior; worth a live smoke test against a
        real image URL before the demo.
        """
        if not image_ref:
            return None
        ref = str(image_ref).strip()
        if not ref.lower().startswith(("http://", "https://")):
            return None
        try:
            req = urllib.request.Request(ref, headers={"User-Agent": "StoreMind/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    return None
                return resp.read(), content_type
        except Exception:  # noqa: BLE001 — see docstring: any failure just skips the vision stage
            return None

    def _compress_field(self, text: str, limit: int) -> str:
        """compress_fn for length_repair.repair_field_length. A real
        Gemini call, only reached when a structured field comes back
        over its ACP length limit — rare in practice, since
        STRUCTURING_PROMPT already asks for short fields. See
        length_repair.py for the bounded-retry loop and guaranteed-
        truncation fallback wrapped around this call; a failure here is
        caught by that wrapper, not here.

        SANDBOX NOTE: same limitation as _fetch_image_bytes — no live
        Gemini call was possible in the assistant's sandbox. Written
        against the same client.models.generate_content(...) call
        already proven working elsewhere in this file (structure_row's
        existing text-only call); worth a live smoke test on an
        intentionally oversized field before the demo.
        """
        client = self._get_client()
        prompt = COMPRESSION_PROMPT.format(limit=limit, text=text)
        response = client.models.generate_content(model=self.model_name, contents=prompt)
        return (response.text or "").strip()

    def embed_text(self, text: str) -> list[float] | None:
        """768-dim embedding of `text`, or None on ANY failure. Used
        only by reflection_memory.FirestoreVectorReflectionMemory (see
        that module for why 768 dims, and for the fail-open contract
        embedding is held to throughout this project's memory layer —
        this is an enhancement to reflexion's cross-batch memory,
        never something /ingest depends on succeeding.

        SANDBOX NOTE: same documented limitation as _fetch_image_bytes
        / _compress_field above — no live call was possible in the
        assistant's sandbox. Smoke-test against a real project before
        relying on it for the demo.
        """
        try:
            client = self._get_client()
            from google.genai.types import EmbedContentConfig

            response = client.models.embed_content(
                model="gemini-embedding-001",
                contents=[text],
                config=EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", output_dimensionality=768),
            )
            return list(response.embeddings[0].values)
        except Exception:  # noqa: BLE001 — see docstring
            return None

    def structure_row(
        self, cleaned_row: dict[str, Any], row_id: str, reflection_note: str | None = None
    ) -> StructuringResult:
        client = self._get_client()
        prompt = STRUCTURING_PROMPT.format(raw_row=json.dumps(cleaned_row, ensure_ascii=False))
        if reflection_note:
            # Prepended, never merged into the raw_row JSON above — kept
            # visibly separate so the model reads this as meta-guidance
            # about its OWN previous attempt, not as additional product
            # data to extract from. See
            # reflexion_evaluator.build_reflection_note for exactly what
            # this string contains (always specific, never a bare "try
            # again", which would just invite a guess).
            prompt = f"{reflection_note}\n\n---\n\n{prompt}"

        # -- brand cascade, stage 2 setup: only attempt vision if there's
        # no explicit brand already (stage 1) and a source image exists.
        # The image is attached to THIS SAME structuring call rather
        # than a separate round-trip — one Gemini call per row, same as
        # before this change. --
        raw_brand = cleaned_row.get("brand")
        has_explicit_brand = bool(raw_brand and str(raw_brand).strip())
        image_ref = cleaned_row.get("image_url") or cleaned_row.get("image")
        vision_attempted = False

        contents: Any = prompt
        if not has_explicit_brand and image_ref:
            fetched = self._fetch_image_bytes(str(image_ref))
            if fetched is not None:
                image_bytes, mime_type = fetched
                # Imported lazily, same reasoning as _get_client — this
                # module must still import cleanly without google-genai
                # installed (e.g. local testing against the mock backend).
                from google.genai import types

                contents = [prompt, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)]
                vision_attempted = True

        raw_text = None
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                )
                raw_text = response.text
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                err_str = str(exc)
                is_rate_limit = any(marker.lower() in err_str.lower() for marker in RATE_LIMIT_MARKERS)
                is_daily_quota = DAILY_QUOTA_MARKER in err_str
                if is_rate_limit and not is_daily_quota and attempt < MAX_RETRIES:
                    delay_match = RETRY_DELAY_PATTERN.search(err_str)
                    wait = float(delay_match.group(1)) if delay_match else DEFAULT_RETRY_WAIT_SECONDS
                    wait = min(wait, MAX_RETRY_WAIT_SECONDS)
                    time.sleep(wait)
                    continue
                raise

        parsed = self.parser.parse(raw_text)

        structured = None
        if parsed.success:
            structured = dict(parsed.data or {})
            if "price" in cleaned_row:
                structured["price"] = cleaned_row["price"]
            # Not asked of Gemini in STRUCTURING_PROMPT — deliberately.
            # cleaned_row["currency"] is Layer-0's own deterministic
            # detection (price-parser's extracted token, resolved to an
            # ISO code, or "INR" via the Babel path); trusting that is
            # more reliable than having the model guess a currency from
            # a price field it's told to treat as bare, symbol-free.
            structured["currency"] = cleaned_row.get("currency")
            structured.setdefault("name", cleaned_row.get("name"))
            structured["sku"] = cleaned_row.get("sku", row_id)

            # -- brand: explicit source always wins. A vision-extracted
            # brand is only trusted if an image was actually sent on
            # THIS call — if vision was never attempted but Gemini
            # returned a "brand" anyway, that's a guess made in
            # violation of the prompt instruction, and gets stripped
            # rather than trusted (same defensive-verification instinct
            # already applied to currency: never take the model's word
            # for something more reliably known another way). Stages
            # 3-4 (majority-vote inference, "Unbranded" fallback) run
            # once per batch in main.py — see brand_resolution.py.
            if has_explicit_brand:
                structured["brand"] = str(raw_brand).strip()
                structured["brand_source"] = "source"
            elif vision_attempted and structured.get("brand"):
                structured["brand"] = str(structured["brand"]).strip()
                structured["brand_source"] = "vision"
            else:
                structured.pop("brand", None)

            # -- ACP length limits (title/description/brand). Rare in
            # practice — the prompt already asks for short fields — but
            # never assumed; see length_repair.py for the bounded
            # compress-and-recheck loop and guaranteed truncation.
            repair_notes: list[str] = []
            for field_name, limit in ACP_LENGTH_LIMITS.items():
                value = structured.get(field_name)
                if isinstance(value, str) and len(value) > limit:
                    result = repair_field_length(
                        value, limit, compress_fn=lambda t, l: self._compress_field(t, l)
                    )
                    structured[field_name] = result.text
                    repair_notes.append(
                        f"{field_name}: {result.method} ({result.original_length}->{result.final_length} chars)"
                    )
            if repair_notes:
                structured["acp_repair_notes"] = repair_notes

        return StructuringResult(
            row_id=row_id,
            success=parsed.success,
            structured=structured,
            extraction_method=parsed.extraction_method,
            warnings=parsed.warnings,
            raw_model_output=raw_text,
        )

    def structure_batch(self, cleaned_rows: list[dict[str, Any]]) -> list[StructuringResult]:
        results = []
        for row in cleaned_rows:
            row_id = str(row.get("sku") or row.get("id") or "unknown")
            try:
                results.append(self.structure_row(row, row_id))
            except Exception as exc:  # noqa: BLE001 — one bad call must not kill the batch
                results.append(
                    StructuringResult(
                        row_id=row_id,
                        success=False,
                        structured=None,
                        extraction_method="failed",
                        warnings=[f"gemini call raised: {exc}"],
                        raw_model_output="",
                    )
                )
        return results
    
    