"""
Tests for seller_contact_validators.py — DELIBERATELY WEAK, format-only
signals for UPI VPA and Indian mobile numbers. Neither has a checksum;
these tests confirm FORMAT plausibility only, matching what the module
itself claims (format_valid, never *_verified — see its docstring).
"""

import pytest

from seller_contact_validators import check_upi_vpa_format, check_whatsapp_phone_format


@pytest.mark.parametrize(
    "vpa",
    ["9999999999@upi", "gauravkumar@exampleupi", "merchant.shop@oksbi", "xyz@freecharge"],
)
def test_plausible_vpa_passes_format_check(vpa):
    r = check_upi_vpa_format(vpa)
    assert r.format_valid is True


@pytest.mark.parametrize("vpa", [None, "", "no-at-sign", "@upi", "user@1"])
def test_malformed_vpa_fails_format_check(vpa):
    r = check_upi_vpa_format(vpa)
    assert r.format_valid is False


@pytest.mark.parametrize(
    "phone,expected_normalized",
    [
        ("9876543210", "+919876543210"),
        ("+919876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
    ],
)
def test_plausible_indian_mobile_passes_format_check(phone, expected_normalized):
    r = check_whatsapp_phone_format(phone)
    assert r.format_valid is True
    assert r.normalized == expected_normalized


@pytest.mark.parametrize("phone", [None, "", "1234567890", "98765432"])
def test_implausible_phone_fails_format_check(phone):
    r = check_whatsapp_phone_format(phone)
    assert r.format_valid is False