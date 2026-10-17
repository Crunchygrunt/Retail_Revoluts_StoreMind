import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layer0_validator import Layer0Validator  # noqa: E402


def test_currency_detected_when_rs_marker_touches_digit_directly():
    """Regression test: \\b treats digits as word characters too, so the
    original \\bRs\\b\\.? boundary check missed any Rs/INR marker
    directly touching a digit with no space in between — confirmed
    live: 'Rs1,500' (and the equivalent 'INR999' style) fell through to
    price-parser instead of the INR/Babel path, and price-parser's own
    raw 'Rs' token (not resolved to INR) ended up as the stored
    currency, displaying as '1,500 Rs' on the Products page instead of
    a proper \u20b9 price."""
    v = Layer0Validator()
    for price_str in ("Rs1,500", "1,500Rs", "1500Rs.", "INR1,500"):
        result = v.validate_row({"name": "Cotton T-Shirt", "price": price_str})
        assert result.accepted, (price_str, result.reasons)
        assert result.cleaned_row["currency"] == "INR", (price_str, result.cleaned_row.get("currency"))
        assert result.cleaned_row["price"] == 1500.0, (price_str, result.cleaned_row["price"])


def test_currency_captured_for_inr():
    """Regression test for the gap found in chat: currency was detected
    only to pick a parser, then discarded — every price downstream was
    a bare number with no way to tell 125000 rupees from 125000 of
    anything else."""
    v = Layer0Validator()
    result = v.validate_row({"name": "Kurta", "price": "\u20b91,50,000"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["currency"] == "INR"


def test_currency_mapped_to_iso_code_for_known_symbol():
    v = Layer0Validator()
    result = v.validate_row({"name": "Batik Shirt", "price": "Rp 150.000"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["currency"] == "IDR"


def test_currency_falls_back_to_raw_token_when_unmapped():
    """An unrecognized currency token is preserved as-is rather than
    dropped — still more useful on a listing than no information."""
    v = Layer0Validator()
    result = v.validate_row({"name": "Vase", "price": "75 990,00 K\u010d"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["currency"] not in (None, "")


def test_currency_is_none_when_price_has_no_symbol():
    v = Layer0Validator()
    result = v.validate_row({"name": "Mug", "price": "500"})
    assert result.accepted, result.reasons
    assert result.cleaned_row.get("currency") is None


def test_explicit_currency_field_is_never_overwritten():
    v = Layer0Validator()
    result = v.validate_row({"name": "Mug", "price": "\u20b9500", "currency": "USD"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["currency"] == "USD"


def test_accepts_clean_row():
    v = Layer0Validator()
    result = v.validate_row({"sku": "A1", "name": "Cotton Tote Bag", "price": "19.99"})
    assert result.accepted
    assert result.cleaned_row["price"] == 19.99


def test_rejects_missing_name():
    v = Layer0Validator()
    result = v.validate_row({"sku": "A2", "name": "", "price": "10"})
    assert not result.accepted
    assert any("name" in r for r in result.reasons)


def test_rejects_missing_price():
    v = Layer0Validator()
    result = v.validate_row({"sku": "A3", "name": "Mug"})
    assert not result.accepted
    assert any("price" in r for r in result.reasons)


def test_rejects_garbage_price():
    v = Layer0Validator()
    result = v.validate_row({"sku": "A4", "name": "Mug", "price": "call for price"})
    assert not result.accepted
    assert any("price" in r for r in result.reasons)


def test_rejects_negative_price():
    v = Layer0Validator()
    result = v.validate_row({"sku": "A5", "name": "Mug", "price": "-5"})
    assert not result.accepted


def test_normalizes_currency_symbol_price():
    v = Layer0Validator()
    result = v.validate_row({"sku": "A6", "name": "Mug", "price": "₹499"})
    assert result.accepted
    assert result.cleaned_row["price"] == 499.0


def test_detects_mojibake_name():
    v = Layer0Validator()
    result = v.validate_row({"sku": "A7", "name": "Cott\ufffdn Bag", "price": "10"})
    assert not result.accepted
    assert any("encoding" in r for r in result.reasons)


def test_normalizes_indonesian_rupiah_dot_thousands_separator():
    """Regression test for the real bug: IDR commonly uses '.' as a
    thousands separator ('Rp 150.000' = 150,000). The original regex
    only ever captured a single decimal group of 1-2 digits, so this
    price was REJECTED outright at Layer-0 (confirmed by direct testing:
    the regex match returned None, not a silently wrong value) — a
    legitimate product would never have reached the Catalog Structuring
    Agent."""
    v = Layer0Validator()
    result = v.validate_row({"sku": "SEA1", "name": "Batik Shirt", "price": "Rp 150.000"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["price"] == 150000.0


def test_normalizes_vietnamese_dong_dot_thousands_separator():
    v = Layer0Validator()
    result = v.validate_row({"sku": "SEA2", "name": "Silk Scarf", "price": "1.200.000 \u20ab"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["price"] == 1200000.0


def test_normalizes_western_thousands_separator():
    """Same underlying bug, not SEA-specific: any price with a thousands
    separator and a 3+ digit group (e.g. an ordinary four-figure USD
    price) was rejected by the old regex too — this wasn't only a
    SEA-locale gap."""
    v = Layer0Validator()
    result = v.validate_row({"sku": "W1", "name": "Leather Jacket", "price": "$1,234.56"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["price"] == 1234.56


def test_negative_thousands_separated_price_still_rejected():
    """Sign handling must still compose correctly now that price-parser
    (which doesn't represent negative amounts itself) does the parsing —
    see the is_negative handling in _normalize_price."""
    v = Layer0Validator()
    result = v.validate_row({"sku": "NEG1", "name": "Mug", "price": "-150.000"})
    assert not result.accepted
    assert any("positive" in r for r in result.reasons)


def test_batch_of_sku_less_rows_get_distinct_skus():
    """Regression test for the real bug found via live testing: 4 sku-less
    rows submitted in one /ingest batch all structured and scored
    correctly, but only 1 survived in the store — because catalog_agent.py
    falls back to the literal string "unknown" for any row without a sku
    or id, so every sku-less row in a batch collided on the same storage
    key and silently overwrote the ones before it. cleaned_row must carry
    a unique sku so that collision can't happen downstream."""
    v = Layer0Validator()
    rows = [
        {"name": "Krama", "price": "25", "description": "a"},
        {"name": "Sinh", "price": "1850000", "description": ""},
        {"name": "Longyi", "price": "45000", "description": "b"},
        {"name": "Thai Silk", "price": "12500", "description": "c"},
    ]
    accepted, rejected = v.validate_batch(rows)
    assert len(accepted) == 4, rejected
    skus = [r.cleaned_row["sku"] for r in accepted]
    assert len(set(skus)) == 4, f"expected 4 distinct skus, got collisions: {skus}"


def test_explicit_sku_is_never_overwritten():
    v = Layer0Validator()
    result = v.validate_row({"sku": "REAL-SKU-1", "name": "Mug", "price": "10"})
    assert result.cleaned_row["sku"] == "REAL-SKU-1"


def test_id_field_used_as_sku_when_sku_absent():
    v = Layer0Validator()
    result = v.validate_row({"id": "ID-99", "name": "Mug", "price": "10"})
    assert result.cleaned_row["sku"] == "ID-99"


def test_standalone_calls_without_sku_dont_collide():
    """Same bug, different entry point: validate_row() called directly
    (not through validate_batch) with no sku/id previously fell back to
    the same literal "unknown" every time it was called — this is the
    path main.py's /fraud/check-style single-row flows would hit."""
    v = Layer0Validator()
    result_a = v.validate_row({"name": "Mug", "price": "10"})
    result_b = v.validate_row({"name": "Mug", "price": "10"})
    assert result_a.cleaned_row["sku"] != result_b.cleaned_row["sku"]


def test_two_separate_batches_of_sku_less_rows_dont_collide():
    """Regression test for the second half of the sku-collision bug:
    fixing the WITHIN-batch collision (every row landing on "unknown")
    left an ACROSS-batch collision in place, because the fallback was
    just the row's position ("row_0", "row_1", ...). Confirmed live:
    two separate real /ingest batches of 4 sku-less rows each landed on
    the identical row_0..row_3 fallback skus, and the second batch's
    products silently replaced the first's, product for product, in
    the store."""
    v = Layer0Validator()
    batch_a = [{"name": "Product A", "price": "10"}, {"name": "Product B", "price": "20"}]
    batch_b = [{"name": "Product C", "price": "30"}, {"name": "Product D", "price": "40"}]

    accepted_a, _ = v.validate_batch(batch_a)
    accepted_b, _ = v.validate_batch(batch_b)

    skus_a = {r.cleaned_row["sku"] for r in accepted_a}
    skus_b = {r.cleaned_row["sku"] for r in accepted_b}
    assert skus_a.isdisjoint(skus_b), f"cross-batch sku collision: {skus_a} vs {skus_b}"


def test_normalizes_indian_rupee_lakh_grouping():
    """Regression test: Indian lakh grouping (1,50,000 = 150,000) breaks
    the 3-digit-group assumption most price parsers are built around.
    Routed to Babel/en_IN instead of price-parser's general heuristic —
    verified against the real babel install, not a stub."""
    v = Layer0Validator()
    result = v.validate_row({"name": "Handloom Kurta", "price": "\u20b91,50,000"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["price"] == 150000.0


def test_normalizes_indian_rupee_lakh_grouping_with_paise():
    v = Layer0Validator()
    result = v.validate_row({"name": "Brass Lamp", "price": "Rs. 12,34,567.50"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["price"] == 1234567.50


def test_normalizes_indian_rupee_under_one_lakh():
    """Below one lakh, Indian grouping is identical to Western grouping —
    the easy baseline case."""
    v = Layer0Validator()
    result = v.validate_row({"name": "Silk Saree", "price": "\u20b94,999"})
    assert result.accepted, result.reasons
    assert result.cleaned_row["price"] == 4999.0


def test_negative_indian_rupee_lakh_price_still_rejected():
    """Sign handling must compose correctly with the INR/Babel path too,
    not just the price-parser path."""
    v = Layer0Validator()
    result = v.validate_row({"name": "Copper Bottle", "price": "-\u20b91,50,000"})
    assert not result.accepted
    assert any("positive" in r for r in result.reasons)


def test_malformed_indian_rupee_grouping_is_rejected():
    """The specific advantage of routing INR through Babel's strict mode
    over price-parser's generic heuristic: a grouping that doesn't match
    the lakh pattern at all (15,0,000 is not valid — compare 1,50,000)
    is rejected outright rather than silently guessed at."""
    v = Layer0Validator()
    result = v.validate_row({"name": "Mug", "price": "\u20b915,0,000"})
    assert not result.accepted
    assert any("not a parseable number" in r for r in result.reasons)


def test_batch_flags_duplicate_sku():
    v = Layer0Validator()
    rows = [
        {"sku": "DUP1", "name": "Mug", "price": "10"},
        {"sku": "DUP1", "name": "Mug v2", "price": "12"},
    ]
    accepted, rejected = v.validate_batch(rows)
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert any("duplicate sku" in r for r in rejected[0].reasons)


def test_batch_mixed_clean_and_broken():
    v = Layer0Validator()
    rows = [
        {"sku": "B1", "name": "Notebook", "price": "5.50"},
        {"sku": "B2", "name": "", "price": "5.50"},
        {"sku": "B3", "name": "Pen", "price": "not a price"},
    ]
    accepted, rejected = v.validate_batch(rows)
    assert len(accepted) == 1
    assert len(rejected) == 2


if __name__ == "__main__":
    import inspect

    tests = [f for name, f in list(globals().items()) if name.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS  {t.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed")
    