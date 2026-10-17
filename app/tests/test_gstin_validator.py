"""
Tests for gstin_validator.py.

Both known-good GSTINs below are independently-sourced worked examples
(not invented): 33AAACC1206D1ZN and 27AAPFU0939F1ZV. The algorithm was
only trusted after BOTH checked out against the Luhn-mod-36 formula, and
27AAPFU0939F1ZV was separately cross-confirmed against python-stdnum's
own GSTIN module, an independently-implemented open-source library.
"""

import pytest

from gstin_validator import validate_gstin


@pytest.mark.parametrize(
    "gstin,expected_holder",
    [
        ("33AAACC1206D1ZN", "Company"),
        ("27AAPFU0939F1ZV", "Firm / Limited Liability Partnership"),
        ("27aapfu0939f1zv", "Firm / Limited Liability Partnership"),  # lowercase normalizes
    ],
)
def test_valid_gstin_passes_and_decodes_holder_type(gstin, expected_holder):
    r = validate_gstin(gstin)
    assert r.valid is True
    assert r.holder_type == expected_holder
    assert r.normalized == gstin.strip().upper()


@pytest.mark.parametrize(
    "gstin",
    [
        "33AAACC1206D1ZM",  # single check-digit typo of a known-valid GSTIN
        "27AAPFU0939F1ZW",  # same, different known-valid GSTIN
        "23AAPFU0939F1ZV",  # mid-string digit swap
    ],
)
def test_single_character_typo_is_rejected(gstin):
    r = validate_gstin(gstin)
    assert r.valid is False
    assert "check digit mismatch" in r.reasons[0]


@pytest.mark.parametrize(
    "gstin",
    [None, "", "   ", "27AAPFU0939F1Z", "27AAPFU0939F1ZVX", "AB1234567890123", "271234567890123"],
)
def test_structurally_malformed_input_is_rejected_without_raising(gstin):
    r = validate_gstin(gstin)
    assert r.valid is False
    assert len(r.reasons) == 1


def test_all_ten_pan_holder_type_letters_decode_correctly():
    """Embedded PAN's 4th character -> holder type, for every letter the
    Income Tax Department's own published table defines. Constructs a
    checksum-correct GSTIN per letter (state+PAN+entity+'Z' = 14 chars,
    then the real check digit) rather than hardcoding 10 more magic
    GSTINs — this caught a real off-by-one in the original scratch
    version of this test (I'd forgotten the fixed 'Z' before computing
    the checksum), which is exactly the kind of mistake worth guarding
    against with a generated rather than copy-pasted table.
    """
    from gstin_validator import _compute_check_digit

    expected = {
        "P": "Individual", "C": "Company", "F": "Firm / Limited Liability Partnership",
        "H": "Hindu Undivided Family (HUF)", "T": "Trust", "A": "Association of Persons (AOP)",
        "B": "Body of Individuals (BOI)", "L": "Local Authority",
        "J": "Artificial Juridical Person", "G": "Government Agency",
    }
    for letter, label in expected.items():
        body = "07" + "AAA" + letter + "A" + "1234" + "D" + "1"  # state+PAN+entity = 13 chars
        first14 = body + "Z"
        full = first14 + _compute_check_digit(first14)
        r = validate_gstin(full)
        assert r.valid is True, f"constructed GSTIN for holder {letter} failed checksum: {full}"
        assert r.holder_type == label
        