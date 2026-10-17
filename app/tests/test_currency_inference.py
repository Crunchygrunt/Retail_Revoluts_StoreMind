import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp_store = Path(tempfile.gettempdir()) / "storemind_test_currency_inference_store.json"
if _tmp_store.exists():
    _tmp_store.unlink()
os.environ["STOREMIND_LOCAL_STORE_PATH"] = str(_tmp_store)

import local_store  # noqa: E402
from currency_inference import infer_currency, apply_inferred_currency, MIN_SAMPLE_SIZE  # noqa: E402


def _seed(currencies):
    """Writes one product per currency in the list, sku SKU-0, SKU-1, ..."""
    local_store.reset()
    for i, currency in enumerate(currencies):
        local_store.write_structured_product(
            {"name": f"Product {i}", "price": 100, "currency": currency},
            sku=f"SKU-{i}",
            extraction_method="strict_json",
        )


def test_empty_store_infers_nothing():
    _seed([])
    assert infer_currency(local_store) is None


def test_sample_below_minimum_infers_nothing_even_at_100_percent():
    """3 products, all the same currency, is 100% agreement but too small
    a sample to trust — same reasoning as the orders >= 3 guards already
    used in fraud_detector.py."""
    assert MIN_SAMPLE_SIZE > 3, "test assumes 3 is below the real minimum"
    _seed(["INR", "INR", "INR"])
    assert infer_currency(local_store) is None


def test_clear_majority_is_inferred_with_correct_confidence():
    _seed(["INR"] * 9 + ["USD"])
    result = infer_currency(local_store)
    assert result == ("INR", 0.9)


def test_genuinely_mixed_catalog_does_not_meet_default_threshold():
    """60/40 is a real majority but not confident enough at the 0.8
    default — should refuse to guess rather than confidently pick a
    bare-majority currency for a genuinely mixed catalog."""
    _seed(["INR"] * 6 + ["USD"] * 4)
    assert infer_currency(local_store) is None


def test_lower_confidence_threshold_can_be_supplied():
    """The same 60/40 catalog SHOULD infer if the caller explicitly asks
    for a lower bar — confirms the threshold is a real parameter, not
    hardcoded."""
    _seed(["INR"] * 6 + ["USD"] * 4)
    result = infer_currency(local_store, min_confidence=0.5)
    assert result == ("INR", 0.6)


def test_apply_inferred_currency_tags_the_row_explicitly():
    """The inferred currency must never be silently indistinguishable
    from a real Layer-0 detection — currency_inferred and the
    confidence must both be present."""
    _seed(["INR"] * 9 + ["USD"])
    inferred = infer_currency(local_store)
    structured = {"name": "New Product", "price": 2000}
    apply_inferred_currency(structured, inferred)
    assert structured["currency"] == "INR"
    assert structured["currency_inferred"] is True
    assert structured["currency_inference_confidence"] == 0.9


def test_apply_inferred_currency_never_overwrites_a_real_detection():
    _seed(["INR"] * 9 + ["USD"])
    inferred = infer_currency(local_store)
    structured = {"name": "Another Product", "price": 500, "currency": "THB"}
    apply_inferred_currency(structured, inferred)
    assert structured["currency"] == "THB"
    assert "currency_inferred" not in structured


def test_apply_inferred_currency_is_a_noop_when_inference_fails():
    """A currency-less row in an empty (or unconfident) store just stays
    without a currency — no fabricated guess, no crash."""
    _seed([])
    inferred = infer_currency(local_store)
    structured = {"name": "First Ever Product", "price": 2000}
    apply_inferred_currency(structured, inferred)
    assert "currency" not in structured
    assert "currency_inferred" not in structured


def test_batch_applies_one_computed_result_to_multiple_rows_consistently():
    """The actual point of the split API: compute the majority ONCE,
    apply it to every currency-less row in a batch, rather than one
    store query per row."""
    _seed(["INR"] * 9 + ["USD"])
    inferred = infer_currency(local_store)  # one call, like main.py's /ingest will do
    rows = [{"name": f"Batch Item {i}", "price": 100} for i in range(3)]
    for row in rows:
        apply_inferred_currency(row, inferred)
    assert all(r["currency"] == "INR" for r in rows)
    assert all(r["currency_inference_confidence"] == 0.9 for r in rows)


if __name__ == "__main__":
    tests = [f for name, f in list(globals().items()) if name.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS  {t.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed")
    _tmp_store.unlink(missing_ok=True)
    
    