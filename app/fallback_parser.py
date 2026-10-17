"""
Layered fallback parser.

Gemini is instructed to return strict JSON, but live-demo reality is that
model output occasionally arrives wrapped in markdown fences, with trailing
commas, single quotes, or — worst case — as prose instead of JSON at all.
This module exists so ONE bad extraction never takes down the live
before/after demo.

Three layers, tried in order, cheapest/strictest first:
    1. Strict JSON parse.
    2. Relaxed schema — strip code fences, fix trailing commas / smart
       quotes / single quotes, extract the outermost {...} block.
    3. Loose text extraction — regex over "Key: value" style lines, used
       only as a last resort so the demo degrades to a partially-filled
       record instead of crashing.

Every result reports which layer succeeded (`extraction_method`) so the
decision log / demo narration can show *how* a record was recovered.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
TRAILING_COMMA_PATTERN = re.compile(r",\s*([}\]])")
OUTER_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
KEY_VALUE_LINE_PATTERN = re.compile(r"^\s*[\"'\-\*]*\s*([A-Za-z_][A-Za-z0-9_ ]*)\s*[:\-]\s*(.+?)\s*$")

# Fields the loose-text layer will try to recover, mapped from common
# natural-language variants Gemini might use in prose fallback.
FIELD_ALIASES = {
    "title": "name",
    "product name": "name",
    "name": "name",
    "category": "category",
    "price": "price",
    "cost": "price",
    "description": "description",
    "summary": "description",
    "keywords": "keywords",
    "tags": "keywords",
    "attributes": "attributes",
    "color": "attributes",
    "material": "attributes",
}

# These labels alias to "attributes" but represent ONE sub-field, not the
# whole object — "Material: cotton" should become {"material": "cotton"},
# not overwrite attributes with the raw string "cotton". A raw string
# there would silently corrupt len(attributes)-based scoring downstream
# (a string's character count isn't its attribute count).
ATTRIBUTE_SUBFIELDS = {"color", "material"}


@dataclass
class ParseResult:
    success: bool
    data: dict[str, Any] | None
    extraction_method: str  # "strict_json" | "relaxed_schema" | "loose_text" | "failed"
    warnings: list[str]


class FallbackParser:
    def parse(self, raw_text: str) -> ParseResult:
        warnings: list[str] = []

        # Layer 1: strict JSON
        result = self._try_strict_json(raw_text)
        if result is not None:
            return ParseResult(True, result, "strict_json", warnings)

        # Layer 2: relaxed schema
        result, layer2_warnings = self._try_relaxed_schema(raw_text)
        if result is not None:
            return ParseResult(True, result, "relaxed_schema", layer2_warnings)
        warnings.extend(layer2_warnings)

        # Layer 3: loose text extraction
        result, layer3_warnings = self._try_loose_text(raw_text)
        if result:
            warnings.extend(layer3_warnings)
            warnings.append("recovered via loose text extraction — verify fields before trusting")
            return ParseResult(True, result, "loose_text", warnings)

        return ParseResult(False, None, "failed", warnings + ["all extraction layers failed"])

    # ---- layer 1 ------------------------------------------------------------

    @staticmethod
    def _try_strict_json(raw_text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw_text)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    # ---- layer 2 ------------------------------------------------------------

    @staticmethod
    def _try_relaxed_schema(raw_text: str) -> tuple[dict[str, Any] | None, list[str]]:
        warnings: list[str] = []
        text = FENCE_PATTERN.sub("", raw_text).strip()
        if text != raw_text.strip():
            warnings.append("stripped markdown code fence")

        match = OUTER_OBJECT_PATTERN.search(text)
        if match:
            text = match.group(0)

        # smart quotes -> straight quotes
        smart_quote_map = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"}
        for src, dst in smart_quote_map.items():
            text = text.replace(src, dst)

        # trailing commas before } or ]
        cleaned = TRAILING_COMMA_PATTERN.sub(r"\1", text)
        if cleaned != text:
            warnings.append("removed trailing commas")
        text = cleaned

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed, warnings
        except json.JSONDecodeError:
            pass

        # single-quoted keys/values are common in model prose — try once more
        # after a conservative single->double quote swap on obvious cases.
        single_to_double = re.sub(r"'([^'\"]*?)'", r'"\1"', text)
        try:
            parsed = json.loads(single_to_double)
            if isinstance(parsed, dict):
                warnings.append("converted single quotes to double quotes")
                return parsed, warnings
        except json.JSONDecodeError:
            pass

        return None, warnings

    # ---- layer 3 ------------------------------------------------------------

    @staticmethod
    def _try_loose_text(raw_text: str) -> tuple[dict[str, Any] | None, list[str]]:
        warnings: list[str] = []
        extracted: dict[str, Any] = {}

        for line in raw_text.splitlines():
            match = KEY_VALUE_LINE_PATTERN.match(line)
            if not match:
                continue
            raw_key, raw_value = match.groups()
            label = raw_key.strip().lower()
            key = FIELD_ALIASES.get(label)
            if not key or not raw_value:
                continue
            value = raw_value.strip().strip('"').strip("'")

            if key == "price":
                price_match = re.search(r"[0-9]+(?:\.[0-9]{1,2})?", value)
                if price_match:
                    extracted["price"] = float(price_match.group(0))
                continue

            if key == "keywords":
                extracted["keywords"] = [k.strip() for k in value.split(",") if k.strip()]
                continue

            if key == "attributes" and label in ATTRIBUTE_SUBFIELDS:
                extracted.setdefault("attributes", {})[label] = value
                continue

            extracted[key] = value

        if not extracted:
            warnings.append("no key: value lines matched known fields")
            return None, warnings

        if "name" not in extracted:
            warnings.append("could not recover a product name")

        return extracted, warnings
