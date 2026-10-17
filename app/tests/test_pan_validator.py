"""
Tests for pan_validator.py — STRUCTURE ONLY, no checksum. See the
module's own docstring for why: python-stdnum, a maintained open-source
library that DOES implement checksums for hundreds of ID formats, calls
PAN's own check digit "an undocumented checksum algorithm" and validates
structure only. These tests hold this file to the same honesty: they
confirm structural correctness, never a checksum that doesn't exist.
"""

import pytest

from pan_validator import validate_pan


@pytest.mark.parametrize(
    "pan,expected_holder",
    [
        ("ACUPA7085R", "Individual"),  # python-stdnum's own doctest example
        ("AAPFU0939F", "Firm / Limited Liability Partnership"),  # embedded in a known-valid GSTIN
        ("aapfu0939f", "Firm / Limited Liability Partnership"),  # lowercase normalizes
    ],
)
def test_valid_pan_passes_and_decodes_holder_type(pan, expected_holder):
    r = validate_pan(pan)
    assert r.valid is True
    assert r.holder_type == expected_holder


@pytest.mark.parametrize(
    "pan",
    [
        None, "", "AAPFU0939",       # too short
        "AAPFU09390",                 # last char is a digit, not a letter
        "12345ABCDE",                 # digits/letters in wrong positions
        "AAPFU00939F",                # too long
    ],
)
def test_malformed_pan_is_rejected_without_raising(pan):
    r = validate_pan(pan)
    assert r.valid is False
    assert r.holder_type is None
    