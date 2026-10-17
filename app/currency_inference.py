"""
Currency inference — a deterministic fallback for products whose price
carried no currency marker at all ("2,000", no symbol, no code, nothing
for Layer-0 to detect).

NOT a Gemini task: this is a majority-vote count over data the store
already has, which a five-line deterministic function does correctly,
for free, with no non-determinism — the same argument already made
against using Gemini for currency parsing in general (see chat).

Store-agnostic, same pattern as storefront.py: takes the already-
selected store module as a parameter, so this works identically
against local_store.py or real Firestore with zero code difference.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# Starting points, not claimed-optimal figures — same caveat as
# DEFAULT_THRESHOLD in readiness_score.py and the HIGH_VALUE_THRESHOLD_
# BY_CURRENCY figures in fraud_detector.py. No real-catalog data was
# available to calibrate these; worth tuning once there is some.
DEFAULT_MIN_CONFIDENCE = 0.8
# Below this many already-tagged products, a "majority" isn't
# meaningful — 2/2 products sharing a currency proves nothing. Same
# reasoning as the `orders >= 3` / `cod_orders >= 3` guards already
# used in fraud_detector.py to avoid drawing conclusions from too
# small a sample.
MIN_SAMPLE_SIZE = 5
# Caps the store query rather than requesting an unbounded list —
# firestore_client.py's list_products() implementation isn't in front
# of me, so this avoids assuming an arbitrarily large limit is cheap
# against real Firestore. 500 already-tagged products is plenty for a
# stable majority vote; this isn't trying to be exhaustive.
QUERY_LIMIT = 500


def infer_currency(store, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> tuple[str, float] | None:
    """Returns (currency, confidence) for the store's majority currency
    among already currency-tagged products, or None if there isn't
    enough data yet or no currency clears the confidence bar.
    confidence is the fraction of currency-tagged products using that
    currency — not the fraction of ALL products, so untagged ones
    (including the row currently being inferred for) don't dilute it."""
    products = store.list_products(limit=QUERY_LIMIT)
    currencies = [p.get("currency") for p in products if p.get("currency")]
    if len(currencies) < MIN_SAMPLE_SIZE:
        return None

    counts = Counter(currencies)
    currency, count = counts.most_common(1)[0]
    confidence = count / len(currencies)
    if confidence < min_confidence:
        return None
    return currency, round(confidence, 3)


def apply_inferred_currency(structured: dict[str, Any], inferred: tuple[str, float] | None) -> None:
    """Mutates `structured` in place using an ALREADY-COMPUTED inference
    result (see infer_currency) — deliberately takes the result, not the
    store, so a batch of N currency-less rows computes the majority ONCE
    and applies it consistently to all of them, rather than re-querying
    the store per row (which could both be wasteful and, if the store
    changes mid-batch, give different rows in the same batch different
    answers). No-op if structured already has a currency, or if
    inference didn't produce a result."""
    if structured.get("currency"):
        return
    if inferred is None:
        return
    currency, confidence = inferred
    structured["currency"] = currency
    structured["currency_inferred"] = True
    structured["currency_inference_confidence"] = confidence
    
    