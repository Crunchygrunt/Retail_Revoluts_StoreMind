"""
Agent-Facing Storefront — search + fail-closed checkout.

This is the live before/after demo moment: a simulated shopping agent
queries the catalog StoreMind just structured, and transacts against it.

checkout() is now FAIL-CLOSED: a product scoring below the readiness
threshold is rejected, not silently allowed through. This is the headline
differentiation claim from the build plan — "enforced, not just scored."
Every gate decision (blocked or passed) is written to the decision log so
the demo can show *why* a checkout succeeded or failed, not just that it
did.

Store-agnostic: takes the already-selected store module (local_store or
firestore_client — see main.py) as a parameter, so this logic is
identical whether you're testing offline or running against GCP.
"""

from __future__ import annotations

from typing import Any

from readiness_score import DEFAULT_THRESHOLD


def search_products(store, query: str = "", category: str = "", limit: int = 200) -> list[dict[str, Any]]:
    products = store.list_products(limit=limit)
    q = query.lower().strip()
    cat = category.lower().strip()

    def matches(p: dict[str, Any]) -> bool:
        if cat and cat not in str(p.get("category", "")).lower():
            return False
        if not q:
            return True
        haystack = " ".join(
            [
                str(p.get("name", "")),
                str(p.get("description", "")),
                " ".join(p.get("keywords") or []),
            ]
        ).lower()
        return q in haystack

    return [p for p in products if matches(p)]


def checkout(store, sku: str, quantity: int = 1, threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    product = store.get_product(sku)
    if product is None:
        return {"success": False, "reason": "product not found", "sku": sku}

    score = product.get("readiness_score")

    if score is None:
        store.log_decision(sku, "gate_blocked", "no readiness_score computed yet")
        return {
            "success": False,
            "reason": "no readiness score computed for this product yet",
            "sku": sku,
            "readiness_score": None,
            "threshold": threshold,
        }

    if score < threshold:
        store.log_decision(sku, "gate_blocked", f"score {score} below threshold {threshold}")
        return {
            "success": False,
            "reason": f"readiness score {score} is below the required threshold ({threshold})",
            "sku": sku,
            "readiness_score": score,
            "threshold": threshold,
        }

    store.log_decision(sku, "gate_passed", f"score {score} met threshold {threshold}, quantity={quantity}")
    return {
        "success": True,
        "sku": sku,
        "quantity": quantity,
        "product_name": product.get("name"),
        "readiness_score": score,
        "threshold": threshold,
    }
