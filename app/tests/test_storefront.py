import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp_store = Path(tempfile.gettempdir()) / "storemind_test_store.json"
if _tmp_store.exists():
    _tmp_store.unlink()
os.environ["STOREMIND_LOCAL_STORE_PATH"] = str(_tmp_store)

import local_store  # noqa: E402
from storefront import search_products, checkout  # noqa: E402


def _seed():
    local_store.reset()
    local_store.write_structured_product(
        {
            "name": "Cotton Tote Bag",
            "category": "Bags",
            "description": "A tote bag.",
            "keywords": ["tote", "bag", "cotton"],
            "attributes": {"material": "cotton"},
            "price": 499,
            "readiness_score": 85.0,
        },
        sku="SKU-001",
        extraction_method="strict_json",
    )
    local_store.write_structured_product(
        {
            "name": "Ceramic Mug",
            "category": "Kitchenware",
            "description": "A mug.",
            "keywords": ["mug"],
            "attributes": {},
            "price": 199,
            "readiness_score": 55.0,
        },
        sku="SKU-002",
        extraction_method="loose_text",
    )


def test_search_by_keyword():
    _seed()
    results = search_products(local_store, query="tote")
    assert len(results) == 1
    assert results[0]["sku"] == "SKU-001"


def test_search_by_category():
    _seed()
    results = search_products(local_store, category="kitchenware")
    assert len(results) == 1
    assert results[0]["sku"] == "SKU-002"


def test_search_empty_query_returns_all():
    _seed()
    results = search_products(local_store, query="")
    assert len(results) == 2


def test_checkout_success():
    _seed()
    result = checkout(local_store, "SKU-001", quantity=2)
    assert result["success"]
    assert result["quantity"] == 2
    assert result["readiness_score"] == 85.0


def test_checkout_missing_product():
    _seed()
    result = checkout(local_store, "SKU-999")
    assert not result["success"]


def test_checkout_blocks_below_threshold_score():
    _seed()
    # SKU-002 has readiness_score 55.0, below the 70.0 default threshold —
    # checkout should now be BLOCKED. This replaces the old fail-open test:
    # the gate is live as of Phase 2b.
    result = checkout(local_store, "SKU-002", quantity=1)
    assert not result["success"], "checkout should be blocked for a below-threshold product"
    assert result["readiness_score"] == 55.0
    assert result["threshold"] == 70.0


def test_checkout_passes_above_threshold_score():
    _seed()
    result = checkout(local_store, "SKU-001", quantity=1)
    assert result["success"]
    assert result["readiness_score"] == 85.0


def test_checkout_custom_threshold():
    _seed()
    # SKU-002 (score 55.0) passes if the threshold is lowered below its score.
    result = checkout(local_store, "SKU-002", quantity=1, threshold=50.0)
    assert result["success"]


def test_gate_decisions_are_logged():
    _seed()
    checkout(local_store, "SKU-001", quantity=1)  # passes
    checkout(local_store, "SKU-002", quantity=1)  # blocked
    log = local_store.list_decision_log()
    event_types = {entry["product_sku"]: entry["event_type"] for entry in log}
    assert event_types["SKU-001"] == "gate_passed"
    assert event_types["SKU-002"] == "gate_blocked"


if __name__ == "__main__":
    tests = [f for name, f in list(globals().items()) if name.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS  {t.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed")
    _tmp_store.unlink(missing_ok=True)
