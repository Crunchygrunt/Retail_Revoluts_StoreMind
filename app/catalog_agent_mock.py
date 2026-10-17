"""
Mock Catalog Structuring Agent — zero dependencies, zero network calls.

Simulates Gemini well enough to test the ENTIRE pipeline (Layer-0 ->
structuring -> fallback parser -> store) with nothing installed beyond the
Python standard library. Deliberately rotates through response formats
(clean JSON, fenced JSON, single-quoted JSON, and prose) so a batch run
exercises every layer of FallbackParser — this is not just a stub that
always succeeds, it's a stub that proves the fallback logic matters.

Same public interface as catalog_agent.CatalogStructuringAgent
(structure_row / structure_batch / StructuringResult), so main.py can swap
between them with one environment variable and zero code changes.

REFLEXION (added): structure_row() takes an optional reflection_note.
Unlike the two live backends (where the note is prose prepended to a
real Gemini prompt), the mock has no model to persuade — so richer=True
whenever a reflection_note is present, meaning: keywords are drawn from
name AND description (not just name), and material search scans
description too. This is a DELIBERATE first-pass under-delivery, not an
oversight: keywords on a fresh row are name-only and capped at the
FIRST word, so any multi-word product name reliably trips
reflexion_evaluator's thin_keywords check on attempt 1, giving the mock
backend a guaranteed, reproducible reflexion retry cycle to demo fully
offline, with zero dependency on a real model's non-determinism.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from fallback_parser import FallbackParser
from length_repair import repair_field_length
from acp_schema import ACP_LENGTH_LIMITS

# A tiny, deterministic set of brand names the mock "recognizes" from a
# product image. There's no real vision model here, so this simulates
# the vision cascade stage just enough to exercise the control flow
# (does stage 2 get tried at all, does brand_source get tagged "vision")
# without claiming to do actual image understanding — see
# catalog_agent.py for the real Gemini vision call this stands in for.
MOCK_VISIBLE_BRANDS = ("Tata", "Nike", "Woodland", "Fabindia")

CATEGORY_KEYWORDS = {
    "bag": "bags", "tote": "bags",
    "mug": "kitchenware", "cup": "kitchenware", "coaster": "kitchenware",
    "hook": "home decor", "vase": "home decor", "runner": "home decor",
    "candle": "home decor", "doormat": "home decor",
    "cutlery": "kitchenware",
}
MATERIAL_KEYWORDS = ["cotton", "ceramic", "brass", "glass", "bamboo", "jute", "linen", "wood", "wooden"]

# Added alongside catalog_agent.py's STRUCTURING_PROMPT update (net_quantity/
# manufacture_date as named, optional fields) for backend parity — this file
# has no real prompt to update, so the equivalent simulation is regex
# extraction from the row's own text. Deliberately mirrors the real
# instruction's shape: only produce a value when the SOURCE TEXT actually
# states one, never fabricate one from category/price/whatever else is
# available — same omit-if-absent discipline as the real prompt, and the
# same reason MATERIAL_KEYWORDS below can legitimately produce an EMPTY
# result rather than always finding something.
_NET_QUANTITY_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:g|kg|ml|l|gram|grams|kilogram|kilograms|litre|litres|liter|liters)\b"
    r"|\bpack of \d+\b"
    r"|\b\d+\s?(?:piece|pieces|unit|units)\b",
    re.IGNORECASE,
)
# MM/YYYY only (matches STRUCTURING_PROMPT's requested format exactly) -
# deliberately narrow rather than a loose date-like pattern, so this can't
# accidentally match an unrelated number sequence in a product name/price.
_MANUFACTURE_DATE_PATTERN = re.compile(r"\b(?:0[1-9]|1[0-2])/20\d{2}\b")


def _keywords_from(name_lower: str, description: Any, richer: bool) -> list[str]:
    """richer=True only when a reflection_note is present (i.e. only on
    a retry, because SOMETHING was flagged as fixable) — see module
    docstring for why the first pass is deliberately name-only,
    first-word-only."""
    words = re.findall(r"[a-zA-Z]+", name_lower)
    if richer:
        words += re.findall(r"[a-zA-Z]+", str(description).lower())
        return list(dict.fromkeys(words))[:5] or ["product"]
    return words[:1] or ["product"]


def _materials_from(name_lower: str, description: Any, richer: bool) -> list[str]:
    haystack = name_lower if not richer else f"{name_lower} {str(description).lower()}"
    return [m for m in MATERIAL_KEYWORDS if m in haystack]


@dataclass
class StructuringResult:
    row_id: str
    success: bool
    structured: dict[str, Any] | None
    extraction_method: str
    warnings: list[str]
    raw_model_output: str


class MockCatalogStructuringAgent:
    """Drop-in stand-in for CatalogStructuringAgent. No GCP, no API key."""

    def __init__(self, *_, **__):
        # accepts and ignores the same constructor args as the real agent
        # (project, location, model_name) so it's a true drop-in swap
        self.parser = FallbackParser()
        self._call_count = 0

    def _fake_gemini_output(self, cleaned_row: dict[str, Any], reflection_note: str | None = None) -> str:
        """Builds a plausible structured record from the row, then renders
        it in one of four formats — cycling deterministically by call count
        so repeated runs are reproducible and every fallback layer gets
        exercised across a batch of any size >= 4.

        reflection_note (added): drives `richer` — see module docstring
        and _keywords_from / _materials_from above for exactly what
        changes. The four-way format rotation below is UNCHANGED by
        reflection_note; only the CONTENT richness changes, not which
        parser layer will end up recovering it."""
        name = str(cleaned_row.get("name", "")).strip()
        price = cleaned_row.get("price")
        name_lower = name.lower()
        description = cleaned_row.get("description", "")
        richer = bool(reflection_note)

        category = next((v for k, v in CATEGORY_KEYWORDS.items() if k in name_lower), "general")
        materials = _materials_from(name_lower, description, richer)
        attributes = {"material": materials[0]} if materials else {}
        keywords = _keywords_from(name_lower, description, richer)

        # Scans name + description together for quantity/date regardless
        # of `richer` — these were always source-text scans (see the
        # module docstring on net_quantity/manufacture_date above), not
        # something the mock ever under-delivered on the first pass.
        source_text = f"{name} {description}"
        net_quantity_match = _NET_QUANTITY_PATTERN.search(source_text)
        manufacture_date_match = _MANUFACTURE_DATE_PATTERN.search(source_text)

        payload = {
            "name": name,
            "category": category,
            "description": f"{name} — a {category} item." if name else "Unnamed product.",
            "attributes": attributes,
            "keywords": keywords,
            "price": price,
        }
        if net_quantity_match:
            payload["net_quantity"] = net_quantity_match.group(0)
        if manufacture_date_match:
            payload["manufacture_date"] = manufacture_date_match.group(0)

        style = self._call_count % 4
        self._call_count += 1

        if style == 0:
            return json.dumps(payload)
        if style == 1:
            return "```json\n" + json.dumps(payload, indent=2) + "\n```"
        if style == 2:
            # single-quoted, python-repr-ish — the kind of thing a model
            # occasionally produces when asked for JSON but drifts
            body = json.dumps(payload).replace('"', "'")
            return body
        # style == 3: prose fallback, forces layer-3 loose text extraction.
        # Deliberately includes description/material/keywords lines (not
        # just name/category/price) so this exercises the SAME recovery
        # paths a real Gemini prose degradation would need, rather than
        # simulating an artificially thin worst case.
        lines = [
            f"Name: {payload['name']}",
            f"Category: {payload['category']}",
            f"Price: {payload['price']}",
            f"Description: {payload['description']}",
        ]
        if materials:
            lines.append(f"Material: {materials[0]}")
        if keywords:
            lines.append(f"Keywords: {', '.join(keywords)}")
        if net_quantity_match:
            lines.append(f"Net quantity: {net_quantity_match.group(0)}")
        if manufacture_date_match:
            lines.append(f"Manufacture date: {manufacture_date_match.group(0)}")
        return "Here is the structured product:\n" + "\n".join(lines)

    def structure_row(
        self, cleaned_row: dict[str, Any], row_id: str, reflection_note: str | None = None
    ) -> StructuringResult:
        # Only passed through when actually present — a monkeypatched
        # test double stubbing _fake_gemini_output against the PRE-
        # reflexion signature (cleaned_row only) must keep working
        # unchanged for every call that does not use reflexion, which
        # is every call any existing test makes. Confirmed necessary
        # via a real pytest run: passing reflection_note=None as an
        # explicit kwarg unconditionally broke
        # TestMockBackend._agent()'s force_style0(cleaned_row) stub in
        # test_catalog_agent_acp_repair.py with a TypeError, even
        # though the VALUE was None — the extra keyword argument alone
        # was the incompatibility, not what it held.
        if reflection_note:
            raw_text = self._fake_gemini_output(cleaned_row, reflection_note=reflection_note)
        else:
            raw_text = self._fake_gemini_output(cleaned_row)
        parsed = self.parser.parse(raw_text)

        structured = None
        if parsed.success:
            structured = dict(parsed.data or {})
            if "price" in cleaned_row:
                structured["price"] = cleaned_row["price"]
            # Same currency propagation as catalog_agent.py — trusts
            # Layer-0's detection (cleaned_row["currency"]) over asking
            # this mock backend's own fake output for one.
            structured["currency"] = cleaned_row.get("currency")
            structured.setdefault("name", cleaned_row.get("name"))
            structured["sku"] = cleaned_row.get("sku", row_id)

            # -- brand cascade, stages 1-2 (see catalog_agent.py for the
            # real vision call this simulates; stages 3-4 — majority-
            # vote inference and the "Unbranded" fallback — run once per
            # batch in main.py via brand_resolution.py, identically for
            # every backend). --
            raw_brand = cleaned_row.get("brand")
            source_name = str(cleaned_row.get("name", ""))
            image_ref = cleaned_row.get("image_url") or cleaned_row.get("image")
            if raw_brand and str(raw_brand).strip():
                structured["brand"] = str(raw_brand).strip()
                structured["brand_source"] = "source"
            elif image_ref:
                visible = next((b for b in MOCK_VISIBLE_BRANDS if b.lower() in source_name.lower()), None)
                if visible:
                    structured["brand"] = visible
                    structured["brand_source"] = "vision"

            # -- ACP length limits (title/description/brand). No real
            # LLM here, so compress_fn=None — goes straight to
            # length_repair's guaranteed deterministic truncation.
            # Exercises that fallback path without needing a live
            # model; catalog_agent.py exercises the compression path
            # with a real Gemini call instead. --
            repair_notes: list[str] = []
            for field_name, limit in ACP_LENGTH_LIMITS.items():
                value = structured.get(field_name)
                if isinstance(value, str) and len(value) > limit:
                    result = repair_field_length(value, limit, compress_fn=None)
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
            results.append(self.structure_row(row, row_id))
        return results
    