"""
Tests for xlsx_ingest.py. Uses openpyxl itself to build real .xlsx
files in memory, then parses them back — genuine round-trip testing,
not mocked.
"""

from __future__ import annotations

import datetime
import io
import json

import openpyxl
import pytest

from xlsx_ingest import XlsxParseError, parse_xlsx_bytes


def make_workbook(rows, sheet_title="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_a_realistic_catalog_parses_into_the_row_shape_ingest_expects():
    data = make_workbook([
        ["name", "description", "price", "category", "brand", "availability"],
        ["Cotton Kurta", "Handwoven cotton kurta, blue.", 899, "Apparel", "Local Weaves", "in_stock"],
    ], sheet_title="Products")
    result = parse_xlsx_bytes(data)
    assert result["sheet_name"] == "Products"
    assert result["row_count"] == 1
    row = result["rows"][0]
    assert row == {
        "name": "Cotton Kurta", "description": "Handwoven cotton kurta, blue.",
        "price": 899, "category": "Apparel", "brand": "Local Weaves", "availability": "in_stock",
    }


def test_numeric_cells_stay_numeric_not_stringified():
    data = make_workbook([["name", "price"], ["Item", 899]])
    row = parse_xlsx_bytes(data)["rows"][0]
    assert row["price"] == 899
    assert isinstance(row["price"], int)


def test_a_fully_blank_row_is_skipped():
    data = make_workbook([
        ["name", "price"],
        ["Item A", 100],
        [None, None],
        ["Item B", 200],
    ])
    result = parse_xlsx_bytes(data)
    assert result["row_count"] == 2
    assert [r["name"] for r in result["rows"]] == ["Item A", "Item B"]


def test_blank_and_whitespace_only_cells_are_dropped_not_kept_as_empty_string():
    data = make_workbook([
        ["name", "description", "brand"],
        ["Item", "   ", None],
    ])
    row = parse_xlsx_bytes(data)["rows"][0]
    assert "description" not in row
    assert "brand" not in row
    assert row == {"name": "Item"}


def test_a_formula_cell_with_no_cached_value_is_absent_not_the_formula_text():
    """openpyxl only returns a formula's computed value if something
    (real Excel/LibreOffice) evaluated and cached it before save. A
    workbook built and saved by openpyxl itself has no cached value —
    this must come back as absent, never as the literal formula string
    "=B2*1.18", which would silently corrupt a price."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["name", "price"])
    ws.append(["Item", 100])
    ws["B3"] = "=B2*1.18"
    ws["A3"] = "Item With Formula Price"
    buf = io.BytesIO()
    wb.save(buf)

    result = parse_xlsx_bytes(buf.getvalue())
    formula_row = result["rows"][1]
    assert "price" not in formula_row or formula_row["price"] is None
    assert "=B2*1.18" not in str(formula_row.get("price", ""))


def test_date_cells_are_coerced_to_an_iso_string_and_stay_json_serializable():
    data = make_workbook([["name", "restock_date"], ["Item", datetime.date(2026, 10, 4)]])
    row = parse_xlsx_bytes(data)["rows"][0]
    assert row["restock_date"] == "2026-10-04T00:00:00"
    json.dumps(row)  # must not raise


def test_an_empty_workbook_raises_a_clean_error():
    data = make_workbook([])
    with pytest.raises(XlsxParseError, match="empty"):
        parse_xlsx_bytes(data)


def test_a_header_row_of_entirely_blank_cells_raises_a_clean_error():
    data = make_workbook([[None, None, ""], ["a", "b", "c"]])
    with pytest.raises(XlsxParseError, match="header"):
        parse_xlsx_bytes(data)


def test_garbage_bytes_raise_cleanly_rather_than_crashing_the_endpoint():
    with pytest.raises(XlsxParseError):
        parse_xlsx_bytes(b"this is not a zip file at all")


def test_an_oversized_file_is_refused_before_parsing():
    with pytest.raises(XlsxParseError, match="MB limit"):
        parse_xlsx_bytes(b"x" * (11 * 1024 * 1024))


def test_non_latin_script_survives_the_round_trip():
    """South-Asia relevance: a Thai product name must not be mangled."""
    thai_name = "\u0e1c\u0e49\u0e32\u0e44\u0e2b\u0e21\u0e44\u0e17\u0e22"
    data = make_workbook([["name", "price"], [thai_name, 650]])
    row = parse_xlsx_bytes(data)["rows"][0]
    assert row["name"] == thai_name


def test_extra_blank_trailing_columns_in_the_header_do_not_break_parsing():
    """A real export often has trailing blank columns from deleted
    fields. The row dict should just not carry keys for them."""
    data = make_workbook([["name", "price", None, None], ["Item", 100, None, None]])
    row = parse_xlsx_bytes(data)["rows"][0]
    assert row == {"name": "Item", "price": 100}
    
    