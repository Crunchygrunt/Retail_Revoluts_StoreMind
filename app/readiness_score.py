"""
Agent Readiness Score.

FAIL-CLOSED as of Phase 2b — storefront.checkout() enforces this threshold.

Six weighted components, each 0-1 before weighting:
    source_richness (45%)          NEW — scores the RAW input, before
                                    Gemini touches it. This is the
                                    dominant weight on purpose: see below.
    completeness (10%)             required fields present in the
                                    STRUCTURED output
    structuring_confidence (10%)   which fallback-parser layer produced
                                    this record — strict_json scores best
    description_quality (15%)      structured description length sanity
    keyword_richness (10%)         search keywords an agent would match on
    attribute_richness (10%)       structured attributes (material, size...)

Why source_richness exists and carries the most weight: every other
component measures the SHAPE of Gemini's output — valid JSON, fields
present, plausible lengths. Gemini is very good at fabricating output
that looks complete from almost no input (a product named "Item" with no
description can still come back with a full description, category,
keywords, and attributes). None of the shape-based checks can tell real
structuring apart from confident fabrication. source_richness is the one
component that looks at what you actually gave the model, so a product
with a real name and a real source description scores well even if
Gemini's phrasing changes it, while a near-empty input scores low even
if Gemini's output looks polished.

Weights are a starting point, not a claimed-optimal formula — the
source_richness weight in particular is worth tuning against a larger,
more varied real catalog before you lock the number for the demo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import regex

WEIGHTS = {
    "source_richness": 0.45,
    "completeness": 0.10,
    "structuring_confidence": 0.10,
    "description_quality": 0.15,
    "keyword_richness": 0.10,
    "attribute_richness": 0.10,
}

EXTRACTION_CONFIDENCE = {
    "strict_json": 1.0,
    "relaxed_schema": 0.7,
    "loose_text": 0.4,
    "failed": 0.0,
}

REQUIRED_FOR_COMPLETENESS = ["name", "category", "description", "price"]

# Names that identify nothing about the actual product — a strong signal
# that whatever Gemini produced was invented, not extracted.
GENERIC_NAME_WORDS = {"item", "product", "thing", "misc", "miscellaneous", "goods", "stuff", "unit"}

DEFAULT_THRESHOLD = 70.0


# Scripts written without whitespace between words ("scriptio continua").
# A whitespace word-count can't judge name specificity for these — it
# would flag every product name in one of these scripts as a single
# generic "word", same penalty as if the raw name had literally been
# "Item". Originally handled for CJK only via hand-rolled codepoint
# ranges; extended to Thai/Khmer/Lao/Myanmar using the `regex` package's
# Unicode Script property (https://github.com/mrabarnett/mrab-regex)
# instead of researching four more block ranges by hand — this also
# means script extension blocks and combining marks outside the core
# range (which a hand-rolled range can miss) are covered automatically,
# and adding another script later is a one-line addition.
_SCRIPTIO_CONTINUA_PATTERN = regex.compile(
    r"[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}"
    r"\p{Script=Thai}\p{Script=Khmer}\p{Script=Lao}\p{Script=Myanmar}]"
)


def _is_scriptio_continua(text: str) -> bool:
    """True if `text` contains a character from a script that doesn't use
    whitespace to separate words (CJK, Thai, Khmer, Lao, Myanmar)."""
    return bool(_SCRIPTIO_CONTINUA_PATTERN.search(text))


# Matches runs of letters/digits in any script, via Unicode General
# Category rather than a hand-picked character set — verified against
# real category data: emoji and pictographs are category "So" (Symbol,
# other), "#" is "Po" (Punctuation, other), "_" is "Pc" (Punctuation,
# connector), "-" is "Pd" (Punctuation, dash) — none of those are
# \p{L}/\p{N}, so none of them count as "word content" or as a
# same-word joiner. Used to make source_richness content-aware instead
# of counting raw characters or splitting on whitespace only:
#   - social-commerce exports commonly join real words with "_" or "-"
#     instead of a space ("Premium_Cotton_Kurta") — a whitespace split
#     saw that as ONE word and penalized it as generic; this correctly
#     sees three.
#   - conversely, emoji/hashtag padding around an actually-generic name
#     ("\U0001F525 ITEM \U0001F525") used to inflate the whitespace
#     word count past the "specific enough" threshold; this correctly
#     doesn't count decorative symbols as words at all.
# Doesn't recover word boundaries from a fully unbroken string with NO
# separator at all ("NICELONGYIFORMEN") — that's real word segmentation,
# a much harder problem this targeted fix doesn't attempt.
_WORD_TOKEN_PATTERN = regex.compile(r"[\p{L}\p{N}]+")


def _descriptive_word_count(text: str) -> int:
    """Counts word-tokens that contain at least one letter. A bare
    numeric token ('1', '999') doesn't make a name more specific on its
    own — so a generic placeholder like "ITEM_1" still reads as one
    real word, not two — but a token mixing digits and letters
    ('500ml') does count, since that's genuinely descriptive."""
    return sum(1 for tok in _WORD_TOKEN_PATTERN.findall(text) if any(ch.isalpha() for ch in tok))


def _real_content_length(text: str) -> int:
    """Sums the length of word-tokens in `text`, ignoring emoji,
    hashtag markers, and other decorative punctuation — so a
    description padded with repeated emoji isn't scored as if it were
    that many characters of real content."""
    return sum(len(tok) for tok in _WORD_TOKEN_PATTERN.findall(text))


@dataclass
class ReadinessScoreResult:
    score: float
    breakdown: dict[str, float]
    meets_threshold: bool
    threshold: float
    notes: list[str] = field(default_factory=list)


def _source_richness(raw_row: dict[str, Any]) -> tuple[float, list[str]]:
    """Scores the RAW catalog row — before Gemini touches it — on whether
    there was enough real information to structure honestly. This is
    intentionally independent of what Gemini produced."""
    notes: list[str] = []
    parts: list[float] = []

    raw_desc = str(raw_row.get("description") or "").strip()
    desc_content_len = _real_content_length(raw_desc)
    if desc_content_len >= 20:
        parts.append(1.0)
    elif desc_content_len > 0:
        parts.append(0.4)
        notes.append("source description is very short")
    else:
        parts.append(0.0)
        notes.append("source has no description — any structured description is Gemini-invented, not sourced")

    raw_name = str(raw_row.get("name") or "").strip()
    if raw_name.lower() in GENERIC_NAME_WORDS:
        parts.append(0.2)
        notes.append(f"source name {raw_name!r} is too generic to identify the actual product")
    elif _is_scriptio_continua(raw_name):
        if len(raw_name) >= 4:
            parts.append(1.0)
        else:
            parts.append(0.5)
            notes.append(f"source name {raw_name!r} may be too short to identify the actual product")
    elif _descriptive_word_count(raw_name) >= 2:
        parts.append(1.0)
    else:
        parts.append(0.2)
        notes.append(f"source name {raw_name!r} is too generic to identify the actual product")

    raw_image = raw_row.get("image") or raw_row.get("image_url")
    if raw_image and str(raw_image).strip():
        parts.append(1.0)
    else:
        parts.append(0.3)
        notes.append("no source image provided")

    return sum(parts) / len(parts), notes


def compute_readiness_score(
    structured: dict[str, Any],
    extraction_method: str,
    raw_row: dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
) -> ReadinessScoreResult:
    notes: list[str] = []

    # -- source richness (dominant weight — see module docstring) --
    source_richness, source_notes = _source_richness(raw_row)
    notes.extend(source_notes)

    # -- completeness (structured output) --
    present = sum(1 for f in REQUIRED_FOR_COMPLETENESS if structured.get(f) not in (None, "", []))
    completeness = present / len(REQUIRED_FOR_COMPLETENESS)
    if completeness < 1.0:
        missing = [f for f in REQUIRED_FOR_COMPLETENESS if structured.get(f) in (None, "", [])]
        notes.append(f"missing fields: {', '.join(missing)}")

    # -- structuring confidence --
    structuring_confidence = EXTRACTION_CONFIDENCE.get(extraction_method, 0.0)
    if structuring_confidence < 1.0:
        notes.append(f"structured via fallback layer '{extraction_method}', not strict JSON")

    # -- description quality (structured output, length sanity only —
    #    source_richness is what catches fabricated-but-well-formed text) --
    desc = str(structured.get("description") or "")
    if len(desc) == 0:
        description_quality = 0.0
        notes.append("description is empty")
    elif len(desc) < 20:
        description_quality = 0.3
        notes.append("description is very short")
    elif len(desc) > 400:
        description_quality = 0.6
        notes.append("description is unusually long for agent consumption")
    else:
        description_quality = 1.0

    # -- keyword richness --
    keywords = structured.get("keywords") or []
    keyword_richness = min(len(keywords) / 5, 1.0)
    if len(keywords) < 3:
        notes.append("fewer than 3 search keywords")

    # -- attribute richness --
    attributes = structured.get("attributes") or {}
    attribute_richness = min(len(attributes) / 3, 1.0)
    if len(attributes) == 0:
        notes.append("no structured attributes")

    breakdown = {
        "source_richness": round(source_richness * 100, 1),
        "completeness": round(completeness * 100, 1),
        "structuring_confidence": round(structuring_confidence * 100, 1),
        "description_quality": round(description_quality * 100, 1),
        "keyword_richness": round(keyword_richness * 100, 1),
        "attribute_richness": round(attribute_richness * 100, 1),
    }

    score = (
        source_richness * WEIGHTS["source_richness"]
        + completeness * WEIGHTS["completeness"]
        + structuring_confidence * WEIGHTS["structuring_confidence"]
        + description_quality * WEIGHTS["description_quality"]
        + keyword_richness * WEIGHTS["keyword_richness"]
        + attribute_richness * WEIGHTS["attribute_richness"]
    ) * 100

    return ReadinessScoreResult(
        score=round(score, 1),
        breakdown=breakdown,
        meets_threshold=score >= threshold,
        threshold=threshold,
        notes=notes,
    )


def source_richness_ceiling(raw_row: dict[str, Any]) -> float:
    """The highest readiness score this row could EVER reach, no matter
    how well it gets structured — source_richness is 45% of the total
    and is computed from the RAW row alone (see module docstring), so
    no amount of retrying the STRUCTURING step can move it; only
    richer SOURCE data could.

    Added for reflexion_evaluator.py: without this, a reflexion loop
    retrying a thin row purely to chase the readiness threshold would
    burn Gemini calls diagnosing a seller-data problem as if it were a
    model problem. reflexion_evaluator.classify_attempt uses this to
    demote readiness-only findings (thin keywords/attributes/
    description) to source-limited once even a PERFECT structured
    output couldn't clear the bar — while still retrying ACP/ONDC
    extraction gaps, which are a separate axis unaffected by this
    ceiling.

    Reuses _source_richness directly, not a re-derivation, so this can
    never silently disagree with what compute_readiness_score itself
    would score on a well-structured version of the same raw_row —
    every other component simply assumed at its maximum (1.0).
    """
    richness, _ = _source_richness(raw_row)
    best_possible = (
        richness * WEIGHTS["source_richness"]
        + 1.0
        * (
            WEIGHTS["completeness"]
            + WEIGHTS["structuring_confidence"]
            + WEIGHTS["description_quality"]
            + WEIGHTS["keyword_richness"]
            + WEIGHTS["attribute_richness"]
        )
    ) * 100
    return round(best_possible, 1)

