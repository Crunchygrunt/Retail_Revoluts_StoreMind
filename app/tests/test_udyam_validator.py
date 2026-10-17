"""
Tests for udyam_validator.py — STRUCTURE ONLY. Format sourced directly
from udyamregistration.gov.in's own login-form field description:
"Udyam should have first 5 letters, followed by one (-) after that 2
letters, followed by one (-) after that 2 digits, followed by one (-)
and last 7 of numeric." No checksum exists in the published format.
"""

import pytest

from udyam_validator import validate_udyam


@pytest.mark.parametrize(
    "raw,expected_state,expected_district",
    [
        ("UDYAM-MH-00-0000123", "MH", "00"),
        ("udyam-dl-05-1234567", "DL", "05"),  # lowercase normalizes
    ],
)
def test_valid_udyam_passes_and_decodes_fields(raw, expected_state, expected_district):
    r = validate_udyam(raw)
    assert r.valid is True
    assert r.state_code == expected_state
    assert r.district_code == expected_district


@pytest.mark.parametrize(
    "raw",
    [
        None, "",
        "UDYAM-MH-00-12345",       # too few sequence digits
        "UDYAM-M-00-0000123",      # 1-letter state code
        "MH-00-0000123",           # missing UDYAM- prefix
        "UDYAM-MH-00-00001234",    # too many sequence digits
    ],
)
def test_malformed_udyam_is_rejected_without_raising(raw):
    r = validate_udyam(raw)
    assert r.valid is False
    assert r.state_code is None
    