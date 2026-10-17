"""
Catalog Structuring Agent — Google AI Studio backend.

Use this to test against a REAL Gemini model before you have GCP project
access. Google AI Studio issues a free API key with no billing account, no
project setup, and no IAM — just a Google account:

    1. Go to https://aistudio.google.com/apikey
    2. Create an API key
    3. export GEMINI_API_KEY=your-key-here
    4. export STOREMIND_LLM_BACKEND=aistudio

Requires: pip install google-genai

Same public interface as catalog_agent.CatalogStructuringAgent and
catalog_agent_mock.MockCatalogStructuringAgent, so main.py swaps between
all three with one environment variable and zero code changes. When you
get GCP access, switch to STOREMIND_LLM_BACKEND=vertexai — the prompt and
parsing logic are identical, only the transport changes.

REFLEXION (added): structure_row() takes an optional reflection_note —
identical change to catalog_agent.py's, see that file's module docstring
for the full reasoning. embed_text() is new too, used only by
reflection_memory.FirestoreVectorReflectionMemory.
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
from catalog_agent import STRUCTURING_PROMPT, COMPRESSION_PROMPT  # reuse the exact same prompts

RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "rate limit", "quota")
DAILY_QUOTA_MARKER = "PerDay"  # e.g. "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
RETRY_DELAY_PATTERN = re.compile(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s")
MAX_RETRIES = 1  # per-minute throttling only — see structure_row
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


class GeminiAIStudioAgent:
    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Get a free key at https://aistudio.google.com/apikey"
            )
        self.model_name = model_name or os.environ.get("STOREMIND_AISTUDIO_MODEL", "gemini-3.6-flash")
        self.parser = FallbackParser()
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        # Imported lazily so this module loads fine without google-genai
        # installed, matching the lazy-import pattern used elsewhere.
        from google import genai

        self._client = genai.Client(api_key=self.api_key)
        return self._client

    # Same brand-cascade / length-repair helpers as catalog_agent.py,
    # kept as near-identical copies rather than shared via import — same
    # reasoning as this file's existing duplication of the retry logic:
    # the two backends are meant to stay independently readable, and
    # the AI Studio backend is explicitly the one to test protocol
    # changes against before GCP access is available (see module
    # docstring), so it shouldn't depend on catalog_agent.py for
    # anything beyond the shared prompt text.
    @staticmethod
    def _fetch_image_bytes(image_ref: str, timeout: float = 5.0) -> tuple[bytes, str] | None:
        """See catalog_agent.CatalogStructuringAgent._fetch_image_bytes
        for the full docstring and sandbox-verification note — identical
        logic, duplicated rather than imported (see class-level comment
        above)."""
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
        except Exception:  # noqa: BLE001
            return None

    def _compress_field(self, text: str, limit: int) -> str:
        client = self._get_client()
        prompt = COMPRESSION_PROMPT.format(limit=limit, text=text)
        response = client.models.generate_content(model=self.model_name, contents=prompt)
        return (response.text or "").strip()

    def embed_text(self, text: str) -> list[float] | None:
        """See catalog_agent.CatalogStructuringAgent.embed_text — identical
        logic and identical sandbox-verification caveat, duplicated
        rather than imported for the same reason as the two helpers
        above."""
        try:
            client = self._get_client()
            from google.genai.types import EmbedContentConfig

            response = client.models.embed_content(
                model="gemini-embedding-001",
                contents=[text],
                config=EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", output_dimensionality=768),
            )
            return list(response.embeddings[0].values)
        except Exception:  # noqa: BLE001
            return None

    def structure_row(
        self, cleaned_row: dict[str, Any], row_id: str, reflection_note: str | None = None
    ) -> StructuringResult:
        client = self._get_client()
        prompt = STRUCTURING_PROMPT.format(raw_row=json.dumps(cleaned_row, ensure_ascii=False))
        if reflection_note:
            # See catalog_agent.py's structure_row for the full reasoning
            # — prepended, never merged into the raw_row JSON below.
            prompt = f"{reflection_note}\n\n---\n\n{prompt}"

        raw_brand = cleaned_row.get("brand")
        has_explicit_brand = bool(raw_brand and str(raw_brand).strip())
        image_ref = cleaned_row.get("image_url") or cleaned_row.get("image")
        vision_attempted = False

        contents: Any = prompt
        if not has_explicit_brand and image_ref:
            fetched = self._fetch_image_bytes(str(image_ref))
            if fetched is not None:
                image_bytes, mime_type = fetched
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
                # Daily quota exhaustion: no backoff fixes this within the
                # same day (resets at midnight Pacific), so fail immediately
                # instead of wasting time retrying.
                raise

        parsed = self.parser.parse(raw_text)

        structured = None
        if parsed.success:
            structured = dict(parsed.data or {})
            if "price" in cleaned_row:
                structured["price"] = cleaned_row["price"]
            # Same currency propagation as catalog_agent.py — trusts
            # Layer-0's detection (cleaned_row["currency"]) over asking
            # Gemini for one.
            structured["currency"] = cleaned_row.get("currency")
            structured.setdefault("name", cleaned_row.get("name"))
            structured["sku"] = cleaned_row.get("sku", row_id)

            # Same brand-cascade + length-repair logic as
            # catalog_agent.py — see that file for the full reasoning.
            if has_explicit_brand:
                structured["brand"] = str(raw_brand).strip()
                structured["brand_source"] = "source"
            elif vision_attempted and structured.get("brand"):
                structured["brand"] = str(structured["brand"]).strip()
                structured["brand_source"] = "vision"
            else:
                structured.pop("brand", None)

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
            except Exception as exc:  # noqa: BLE001
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
    
    