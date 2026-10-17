"""
Reflexion evaluator — classifies WHY a structuring attempt fell short,
so the reflexion loop (reflexion.py) only ever retries what a retry can
actually fix.

This is the module the whole design hinges on. A naive reflexion loop
retries until an evaluator is satisfied — but several of StoreMind's own
evaluators (readiness_score.py, acp_schema.py, ondc_mapping.py) fail
products because the SOURCE data is thin, not because Gemini mis-read
it. If a retry loop hands Gemini "manufacture_date is missing, try
again" with no further guidance, the cheapest way for the model to
satisfy that is to invent one — which is exactly the fabrication this
project's whole design (source_richness's dominant weight, the
omit-if-absent STRUCTURING_PROMPT fields, ondc_mapping's refusal to
guess a statutory date) exists to prevent. A reflexion loop that isn't
careful about this optimizes directly against the rest of the codebase.

So every gap gets classified into exactly one of two buckets before any
retry happens:

    model-fixable   the source data plausibly HAS the answer and the
                     model missed it, mangled it, or under-delivered.
                     Retried, with a specific note about what to look
                     at again — never "fix this" with no further
                     guidance, which just invites a guess.
    source-limited   the information genuinely isn't in the source (or
                     is seller/store config, not per-row data at all).
                     NEVER retried — surfaced as a normal remediation
                     gap instead, same as it would be without reflexion.

Two further guards, both load-bearing:

    check_grounding()          After a retry, any NEW value for
                                net_quantity, manufacture_date, or a
                                non-vision brand must be traceable to
                                the raw row's own text. A value that
                                isn't is rejected outright — the caller
                                falls back to whatever the previous
                                attempt had (usually absent), never
                                trusts the new one. This is what stops
                                a retry from quietly fabricating its
                                way to a passing score.

    ceiling_unreachable         source_richness is 45% of the readiness
    (readiness_score.           score and is computed from the RAW row —
     source_richness_ceiling)   no retry of the structuring step can
                                move it. Passed in by the caller so
                                readiness-only gaps aren't retried once
                                even a perfect attempt couldn't clear
                                the bar. ONDC/ACP extraction gaps are a
                                separate axis and are still retried even
                                when readiness itself is unreachable
                                this batch.

Deliberately offline and dependency-free (stdlib `re`, not the `regex`
package readiness_score.py uses for Unicode-script detection — this
module doesn't need script-aware word counting, only plain substring/
token matching), so it's fully unit-testable with no network and no
Gemini call, the same standard the rest of this project holds its
pure-logic modules to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Shared source of truth with readiness_score.py's own source_richness
# check — a generic placeholder name ("Item", "Product") must be judged
# "not real content" the same way in both places, or _has_real_content
# below would treat a row readiness_score itself scores as fabricated-
# risk as if it had genuine source material to draw a retry from.
from readiness_score import GENERIC_NAME_WORDS

# ACP fields a retry can plausibly recover by re-reading the SAME source
# data more carefully. Deliberately excludes brand, price, currency,
# image_url, url, seller_name — each of those is either extract-only
# from a source that may genuinely lack it (brand — see
# brand_resolution.py's cascade, which already exhausts source/vision/
# inferred/seller_name before giving up), a hard number that must never
# be guessed (price, currency), or store-level config a per-row retry
# has no way to supply (seller_name, url, image_url).
MODEL_FIXABLE_ACP_FIELDS = frozenset({"description", "keywords", "attributes", "category", "name"})

# Tags that exist ONLY because of the readiness score, as opposed to an
# ACP/ONDC extraction gap — see ceiling_unreachable handling below.
_READINESS_ONLY_TAGS = frozenset({"thin_keywords", "thin_attributes", "thin_description", "non_strict_json"})

# Same patterns catalog_agent_mock.py uses to simulate the real
# STRUCTURING_PROMPT's net_quantity/manufacture_date extraction — kept
# as an independent copy here (not imported from the mock backend,
# which is test scaffolding, not something production evaluation logic
# should depend on) but intentionally aligned with it so "does the
# source plausibly contain this" means the same thing in both places.
_NET_QUANTITY_HINT = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:g|kg|ml|l|gram|grams|kilogram|kilograms|litre|litres|liter|liters)\b"
    r"|\bpack of \d+\b"
    r"|\b\d+\s?(?:piece|pieces|unit|units)\b",
    re.IGNORECASE,
)
_MANUFACTURE_DATE_HINT = re.compile(r"\b(?:0[1-9]|1[0-2])/20\d{2}\b")

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _source_text(raw_row: dict[str, Any]) -> str:
    return f"{raw_row.get('name', '')} {raw_row.get('description', '')}"


def _has_real_content(raw_row: dict[str, Any]) -> bool:
    """Cheap floor check — enough real source content that "the model
    under-delivered" is a plausible explanation at all. Deliberately
    coarser than readiness_score.py's own _real_content_length (which
    is script-aware via the `regex` package) — this only needs to
    distinguish "there's something here" from "there's nothing here",
    not score it."""
    name = str(raw_row.get("name") or "").strip()
    desc = str(raw_row.get("description") or "").strip()
    name_is_real = len(name) >= 2 and name.lower() not in GENERIC_NAME_WORDS
    return name_is_real or len(desc) >= 10


@dataclass
class GapFinding:
    tag: str
    detail: str


@dataclass
class AttemptClassification:
    model_fixable: list[GapFinding] = field(default_factory=list)
    source_limited: list[GapFinding] = field(default_factory=list)

    @property
    def fixable_tags(self) -> list[str]:
        return [f.tag for f in self.model_fixable]

    @property
    def is_retriable(self) -> bool:
        return bool(self.model_fixable)


def classify_attempt(
    *,
    raw_row: dict[str, Any],
    structured: dict[str, Any],
    extraction_method: str,
    acp_missing_fields: list[str],
    acp_invalid_fields: list[str],
    ondc_blockers: list[str],
    ceiling_unreachable: bool = False,
) -> AttemptClassification:
    """Classifies every gap found in ONE structuring attempt. Pure —
    takes already-computed evaluator output (ACP conformance fields,
    ONDC blockers) rather than recomputing any of it, so this can never
    silently drift from what acp_schema.py / ondc_mapping.py actually
    decided; it only decides which of THEIR findings are worth a retry.
    """
    model_fixable: list[GapFinding] = []
    source_limited: list[GapFinding] = []
    source_text = _source_text(raw_row)

    # -- the extraction layer itself: a real parse failure, not a
    #    content gap, but plausibly re-triable with a stricter prompt --
    if extraction_method in ("relaxed_schema", "loose_text"):
        model_fixable.append(
            GapFinding(
                "non_strict_json",
                f"Your previous response was not valid JSON on the first parse "
                f"(recovered via '{extraction_method}'). Respond with ONLY a single, "
                f"strict JSON object this time — no markdown fences, no commentary, "
                f"no trailing text.",
            )
        )

    # -- ACP missing fields. Gated on _has_real_content, same as the
    # readiness-derived thin_keywords/thin_attributes checks below —
    # a field NAME being in MODEL_FIXABLE_ACP_FIELDS means a retry
    # COULD fix it in principle, not that this particular row's
    # source has anything more to give. Found necessary against a
    # real acp_schema.py that flags a too-short keywords list as
    # missing/invalid: on a genuinely thin row (empty description),
    # richer mode has no more text to draw from, so without this gate
    # the SAME finding gets reported every attempt, is never resolved,
    # and the loop burns every retry before giving up instead of
    # recognizing immediately that the source, not the model, is the
    # limit — exactly the failure mode this whole design exists to
    # avoid. --
    for f in acp_missing_fields:
        if f in MODEL_FIXABLE_ACP_FIELDS and _has_real_content(raw_row):
            model_fixable.append(
                GapFinding(
                    f"acp_missing_{f}",
                    f"'{f}' is missing from your structured output. Re-read the raw "
                    f"product data below and extract it if it genuinely states one — "
                    f"omit it again if it truly doesn't.",
                )
            )
        else:
            source_limited.append(
                GapFinding(
                    f"acp_missing_{f}",
                    f"'{f}' is missing and not recoverable from the source data or "
                    f"seller configuration — a seller-side gap, not a structuring one.",
                )
            )

    # -- ACP invalid fields: same gate. A formatting mistake IS worth
    # one more look when there's real source content to re-check
    # against, but not every invalid field is even the RIGHT KIND of
    # gap for a structuring retry to fix (a malformed price/currency
    # needs a different fix entirely, not a re-read) — restricting to
    # MODEL_FIXABLE_ACP_FIELDS here, not every field name, same as the
    # missing-fields branch above. --
    for f in acp_invalid_fields:
        if f in MODEL_FIXABLE_ACP_FIELDS and _has_real_content(raw_row):
            model_fixable.append(
                GapFinding(
                    f"acp_invalid_{f}",
                    f"'{f}' was present but invalid or malformed. Check its format "
                    f"against the source data and correct it.",
                )
            )
        else:
            source_limited.append(
                GapFinding(
                    f"acp_invalid_{f}",
                    f"'{f}' was present but invalid, and the source has nothing "
                    f"further to re-check it against — not a structuring-retry fix.",
                )
            )

    # -- ONDC statutory gaps: only net_quantity / manufacture_date are
    #    ever retried, and only when the raw text plausibly contains
    #    one — everything else (packer identity, consumer care,
    #    fulfillment policy) is seller/store config a per-row
    #    structuring retry has no way to supply. --
    for blocker in ondc_blockers:
        if "no net quantity" in blocker:
            if _NET_QUANTITY_HINT.search(source_text):
                model_fixable.append(
                    GapFinding(
                        "ondc_net_quantity",
                        "The source text appears to state a net quantity, weight, or "
                        "volume (e.g. '250g', 'pack of 3') that was not extracted into "
                        "'net_quantity'. Look again — extract it only if it's really there.",
                    )
                )
            else:
                source_limited.append(GapFinding("ondc_net_quantity", "No net quantity is stated anywhere in the source data."))
        elif "no manufacture" in blocker or "no manufacture/packing date" in blocker:
            if _MANUFACTURE_DATE_HINT.search(source_text):
                model_fixable.append(
                    GapFinding(
                        "ondc_manufacture_date",
                        "The source text appears to state a manufacture/packing date "
                        "in MM/YYYY format that was not extracted into "
                        "'manufacture_date'. Look again — extract it only if it's "
                        "really there, in MM/YYYY format.",
                    )
                )
            else:
                source_limited.append(
                    GapFinding("ondc_manufacture_date", "No manufacture/packing date is stated anywhere in the source data.")
                )
        else:
            # packer identity / consumer care / fulfillment policy —
            # seller-wide config, never a per-row retry target.
            source_limited.append(GapFinding("ondc_seller_config", blocker))

    # -- readiness: keyword/attribute/description richness, only when
    #    there's plausibly real source content to draw more from --
    keywords = structured.get("keywords") or []
    if len(keywords) < 3 and _has_real_content(raw_row):
        model_fixable.append(
            GapFinding(
                "thin_keywords",
                "Fewer than 3 search keywords were produced. Draw more from the "
                "product's actual name, description, and any attributes you find.",
            )
        )

    attributes = structured.get("attributes") or {}
    if not attributes and _has_real_content(raw_row):
        model_fixable.append(
            GapFinding(
                "thin_attributes",
                "No structured attributes (material, size, color, etc.) were "
                "extracted. Re-check the source description for any such details.",
            )
        )

    description = str(structured.get("description") or "")
    raw_description = str(raw_row.get("description") or "").strip()
    if len(description) < 20:
        if len(raw_description) >= 20:
            model_fixable.append(
                GapFinding(
                    "thin_description",
                    "The structured description is very short even though the "
                    "source description has real content — write a fuller 1-2 "
                    "sentence description grounded in it.",
                )
            )
        else:
            source_limited.append(
                GapFinding(
                    "thin_source_description",
                    "The source itself has little to no description — a fuller "
                    "structured description would be invented, not extracted.",
                )
            )

    # -- ceiling gate: once even a perfect structuring attempt couldn't
    #    clear the readiness threshold (see
    #    readiness_score.source_richness_ceiling), retrying PURELY for
    #    readiness is wasted — demote those specific findings to
    #    source-limited. ONDC/ACP extraction findings are a separate
    #    axis and stay retriable regardless. --
    if ceiling_unreachable:
        demoted = [f for f in model_fixable if f.tag in _READINESS_ONLY_TAGS]
        if demoted:
            model_fixable = [f for f in model_fixable if f.tag not in _READINESS_ONLY_TAGS]
            source_limited.extend(demoted)
            source_limited.append(
                GapFinding(
                    "readiness_ceiling_unreachable",
                    f"Source data is too thin for this product to ever clear the "
                    f"readiness threshold regardless of structuring quality — "
                    f"{len(demoted)} readiness-only issue(s) were not retried for "
                    f"that reason.",
                )
            )

    return AttemptClassification(model_fixable=model_fixable, source_limited=source_limited)


def check_grounding(*, before: dict[str, Any] | None, after: dict[str, Any], raw_row: dict[str, Any]) -> list[str]:
    """Guards against the exact failure mode a naive reflexion loop
    invites: a retry that "resolves" a reported gap by inventing a
    value instead of extracting one. Only checks fields this project's
    own anti-fabrication design already treats as extract-only —
    net_quantity and manufacture_date (see catalog_agent.py's
    STRUCTURING_PROMPT), and brand when NOT vision-sourced (a
    vision-read brand can't be text-grounded the same way — see
    brand_resolution.py).

    Returns the field names in `after` that should be REJECTED — the
    caller's job is to reset each one back to whatever `before` had
    (usually absent), never to trust the new value.

    Known, accepted limitation: this is a token-overlap check against
    the raw row's own text, not real entailment. A correct but
    reworded value (source says "September 2026", model correctly
    normalizes to "09/2026") can be flagged as ungrounded and
    rejected. That's the deliberately safer failure mode — a wrongly
    rejected correct value falls back to being reported as a gap,
    exactly as it would without reflexion; a wrongly accepted
    fabricated one becomes a false statutory declaration. Worth
    tuning with real catalog data before treating the rejection rate
    as final.
    """
    before = before or {}
    source_tokens = _tokens(_source_text(raw_row))
    rejected: list[str] = []

    for f in ("net_quantity", "manufacture_date"):
        new_value = after.get(f)
        old_value = before.get(f)
        if new_value and new_value != old_value:
            value_tokens = _tokens(str(new_value))
            if value_tokens and not value_tokens & source_tokens:
                rejected.append(f)

    new_brand = after.get("brand")
    old_brand = before.get("brand")
    if new_brand and new_brand != old_brand and after.get("brand_source") != "vision":
        if _tokens(str(new_brand)) and not _tokens(str(new_brand)) & source_tokens:
            rejected.append("brand")

    return rejected


def build_reflection_note(
    classification: AttemptClassification, attempt_number: int, prior_notes: "list[str] | None" = None
) -> str:
    """Deterministic, templated reflection — not a second Gemini call.
    A conscious choice, not a shortcut: this project's structuring task
    is narrow and well-specified (unlike the open-ended agentic
    trajectories the original Reflexion paper targets), the evaluator
    already knows EXACTLY which fields are wrong and why, and a
    template is cheaper, faster, and can't itself hallucinate a
    plausible-sounding but wrong diagnosis. build_reflection_note's
    signature (classification in, note string out) is a clean seam if
    an LLM-generated reflector is ever worth swapping in later — the
    loop in reflexion.py doesn't know or care which one produced the
    string.

    prior_notes (added): validated corrections retrieved from OTHER,
    previously ingested products in the same category and sharing at
    least one of this attempt's own gap tags (see
    reflection_memory.ReflectionMemory.retrieve) — this row's own
    diagnosis always comes first; prior notes are appended as a
    clearly separate, lower-priority section so a stale or weakly-
    related past correction can never be mistaken for instructions
    about THIS specific product.
    """
    if not classification.model_fixable:
        return ""
    lines = [
        f"REFLECTION on attempt {attempt_number} of your own previous response:",
        "The issues below were found. The raw product data is included again "
        "below this note — fix ONLY what these points describe, using that data. "
        "If something genuinely isn't stated there, continue to omit it rather "
        "than inventing a value.",
    ]
    lines.extend(f"- {f.detail}" for f in classification.model_fixable)
    if prior_notes:
        lines.append("")
        lines.append(
            "Also relevant — corrections that worked on OTHER, different products "
            "in this category with a similar issue (context only, not about this "
            "product specifically):"
        )
        lines.extend(f"- {n}" for n in prior_notes)
    return "\n".join(lines)


def build_prior_context_note(prior_notes: "list[str]") -> str:
    """A DIFFERENT framing from build_reflection_note, used BEFORE the
    first attempt — there is no previous attempt of THIS row's own to
    critique yet, only accumulated learning from OTHER, previously
    ingested products in the same category (see
    reflection_memory.ReflectionMemory.retrieve called with no tags,
    ranked by recency rather than gap-tag overlap, since no gap has
    been classified yet). Never phrased as "your previous attempt" —
    phrased as advisory context to apply only if genuinely relevant,
    same caution build_reflection_note's own prior_notes section
    carries.
    """
    if not prior_notes:
        return ""
    lines = [
        "CONTEXT from previously corrected products in this same category "
        "(not a critique of a previous attempt of your own — there wasn't one. "
        "Apply only if genuinely relevant to the product data below; never copy "
        "a value from a different product):",
    ]
    lines.extend(f"- {n}" for n in prior_notes)
    return "\n".join(lines)

