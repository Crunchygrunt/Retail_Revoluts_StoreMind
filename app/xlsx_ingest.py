"""
XLSX parsing — converts an uploaded .xlsx file into the same row-object
shape /ingest already expects, so it slots into the exact same pipeline
as pasted JSON or an uploaded CSV. Parsing only: no Layer-0, no Gemini,
no store write happens here — see main.py's /parse/xlsx route, which is
a thin wrapper around parse_xlsx_bytes() below.

WHY THIS IS SERVER-SIDE, UNLIKE CSV (see IngestPage.jsx's client-side
CSV path via PapaParse): checked the actual current state of the
JavaScript .xlsx ecosystem before choosing, and every option had a real
problem, not just an aesthetic one:
  - SheetJS (`xlsx` on npm) is the standard choice, but the public npm
    registry is stuck on v0.18.5 — the project stopped publishing
    current versions there and moved to its own CDN
    (cdn.sheetjs.com), which `npm install xlsx` does not know how to
    reach. Getting a current version means either a non-standard
    tarball-URL install or trusting one of several unofficial
    third-party republishers on npm.
  - ExcelJS (MIT, the usual fallback) has had no meaningful release
    since October 2023 and its own maintainers call it inactive.
  - A newer MIT alternative (@office-kit/xlsx) looks promising on paper
    but is unproven — no multi-year production track record, and the
    claims about it come from its own README.
openpyxl has none of this: MIT, actively maintained (release history
current as of this session), zero known vulnerabilities, installs from
the standard PyPI index with a plain `pip install`. Also checked
whether a managed GCP service (Document AI, Sheets/Drive API) was a
better fit than "a library inside Cloud Run" — it isn't: Document AI is
built for OCR on unstructured documents, not cell-accurate structured
spreadsheets, and Sheets/Drive API would mean OAuth or a service-account
Drive round-trip for a plain file-upload-and-parse use case. GCP's own
tooling treats this as a library problem, run inside compute you already
have — which is exactly Cloud Run here.

SECURITY: xlsx is a zip of XML files, and untrusted uploads parsing XML
is a textbook XXE risk. openpyxl opportunistically uses `defusedxml`
when it's installed, hardening XML parsing against that — install both
(see requirements.txt), not openpyxl alone.
"""

from __future__ import annotations

import datetime
import io
from typing import Any

import openpyxl

MAX_XLSX_BYTES = 10 * 1024 * 1024  # sanity cap, same spirit as image_hosting.MAX_SOURCE_BYTES


class XlsxParseError(ValueError):
    """Raised for any problem with the uploaded file itself — a bad
    upload must produce a clear message, never an unhandled 500."""


def _clean_header(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _clean_cell(value: Any) -> Any:
    """Normalizes one cell value for the JSON row dict.

    - Blank/whitespace-only strings become "not present" (the key is
      dropped by the caller), matching the CSV upload path's own
      cleanup so a sparse real-world catalog doesn't fill every row
      with empty-string noise.
    - Numbers pass through as real Python int/float, not strings — this
      already matches how Layer-0's own price parser accepts either
      form, so no coercion is needed for a numeric price cell.
    - date/datetime cells (openpyxl returns real datetime objects, not
      strings) are coerced to ISO 8601 text so the row is JSON-
      serializable. Confirmed against real openpyxl: a cell containing
      a plain date still comes back as `datetime.datetime` with a
      midnight time component, never a bare `datetime.date` — so the
      ISO string this produces always includes a time part
      ("2026-10-04T00:00:00"), even for a column that's conceptually
      date-only. Nothing in this project's row schema currently uses a
      date field, but a seller's real spreadsheet could still have one
      in an unexpected column, and silently 500ing on it would be a
      worse failure than an ISO string with a redundant midnight time.
    """
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def parse_xlsx_bytes(data: bytes) -> dict[str, Any]:
    """Parses the ACTIVE worksheet of an .xlsx file into row-object
    dicts, headers taken from the first non-empty row. Never raises for
    a malformed upload — raises XlsxParseError with a message meant to
    be shown directly to the person who uploaded the file; anything
    else escaping is a real bug, not a bad-input case.

    Returns {"rows": [...], "sheet_name": str, "row_count": int} —
    "rows" is exactly the shape /ingest expects, so the caller can pass
    it straight through, same as the CSV/JSON upload paths.
    """
    if len(data) > MAX_XLSX_BYTES:
        raise XlsxParseError(f"file exceeds the {MAX_XLSX_BYTES // (1024 * 1024)}MB limit")

    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=True
        )
    except Exception as exc:  # noqa: BLE001 — any bad/corrupt upload lands here
        raise XlsxParseError(f"could not read this file as .xlsx: {exc}") from exc

    try:
        sheet = workbook.active
        if sheet is None:
            raise XlsxParseError("the workbook has no active worksheet")

        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            raise XlsxParseError("the sheet is empty")

        headers = [_clean_header(h) for h in header_row]
        if not any(headers):
            raise XlsxParseError("the first row has no column headers")

        rows: list[dict[str, Any]] = []
        for raw_row in rows_iter:
            if raw_row is None or all(v is None for v in raw_row):
                continue  # a fully blank row -- common at the end of a real export
            row: dict[str, Any] = {}
            for header, raw_value in zip(headers, raw_row):
                if not header:
                    continue
                value = _clean_cell(raw_value)
                if value is None:
                    continue
                row[header] = value
            if row:
                rows.append(row)

        return {"rows": rows, "sheet_name": sheet.title, "row_count": len(rows)}
    finally:
        workbook.close()
        
        