# """
# Tests for seller_profile.py — the merchant-level ACP facts
# (`seller_name`, policy URLs) and the placeholder guard.
# """

# from __future__ import annotations

# import os

# from seller_profile import SellerProfile, load_seller_profile


# def _clear_env():
#     for key in (
#         "STOREMIND_SELLER_NAME",
#         "STOREMIND_SELLER_URL",
#         "STOREMIND_SELLER_PRIVACY_POLICY",
#         "STOREMIND_SELLER_TOS",
#     ):
#         os.environ.pop(key, None)


# def test_loads_a_full_profile_from_the_environment():
#     _clear_env()
#     os.environ["STOREMIND_SELLER_NAME"] = "Kurta House"
#     os.environ["STOREMIND_SELLER_PRIVACY_POLICY"] = "https://example.com/privacy"
#     os.environ["STOREMIND_SELLER_TOS"] = "https://example.com/terms"
#     profile = load_seller_profile()
#     assert profile.name == "Kurta House"
#     assert profile.is_checkout_ready is True
#     _clear_env()


# def test_an_unconfigured_profile_is_all_none_not_invented():
#     """No defaults. A missing seller name must become an honest,
#     reportable gap — never a fabricated value in a feed."""
#     _clear_env()
#     profile = load_seller_profile()
#     assert profile.name is None
#     assert profile.is_checkout_ready is False


# def test_checkout_readiness_needs_both_policy_urls():
#     assert SellerProfile("X", None, "https://p", None).is_checkout_ready is False
#     assert SellerProfile("X", None, None, "https://t").is_checkout_ready is False
#     assert SellerProfile(None, None, "https://p", "https://t").is_checkout_ready is False
#     assert SellerProfile("X", None, "https://p", "https://t").is_checkout_ready is True


# def test_a_configured_placeholder_is_treated_as_unset():
#     """The spec rejects these strings by name. Passing one through would
#     just move the failure downstream to feed ingestion, where it's much
#     harder to diagnose."""
#     _clear_env()
#     for placeholder in ("unknown", "N/A", "null", "  none  ", "Unbranded", "-"):
#         os.environ["STOREMIND_SELLER_NAME"] = placeholder
#         assert load_seller_profile().name is None, placeholder
#     _clear_env()


# def test_whitespace_is_stripped():
#     _clear_env()
#     os.environ["STOREMIND_SELLER_NAME"] = "  Kurta House  "
#     assert load_seller_profile().name == "Kurta House"
#     _clear_env()
    
    
    
    
    
"""
Tests for seller_profile.py.

Two scopes in this one file, on purpose - both test the same module,
so one file beats two competing for the same filename:

  1. The original merchant-level ACP facts (seller_name, policy URLs)
     and the placeholder guard - unchanged from the pre-existing test
     file, verbatim, since this session didn't touch that logic.
  2. Seller identity verification added this session: the either-or
     check across GSTIN / Udyam / UPI+PAN / UPI+WhatsApp - see
     seller_profile.py's own docstring for why GSTIN-only would
     exclude the fragmented, often-GSTIN-less segment this targets.

Confirmed before merging: all 5 original tests pass unmodified against
the updated SellerProfile (the new fields are all separately defaulted,
so the original 4-positional-arg SellerProfile(...) calls below are
untouched by anything added this session).
"""

from __future__ import annotations

import importlib
import os

import pytest

from seller_profile import SellerProfile, load_seller_profile


# --- Part 1: original scope (seller_name, policy URLs, placeholders) ---


def _clear_env():
    for key in (
        "STOREMIND_SELLER_NAME",
        "STOREMIND_SELLER_URL",
        "STOREMIND_SELLER_PRIVACY_POLICY",
        "STOREMIND_SELLER_TOS",
    ):
        os.environ.pop(key, None)


def test_loads_a_full_profile_from_the_environment():
    _clear_env()
    os.environ["STOREMIND_SELLER_NAME"] = "Kurta House"
    os.environ["STOREMIND_SELLER_PRIVACY_POLICY"] = "https://example.com/privacy"
    os.environ["STOREMIND_SELLER_TOS"] = "https://example.com/terms"
    profile = load_seller_profile()
    assert profile.name == "Kurta House"
    assert profile.is_checkout_ready is True
    _clear_env()


def test_an_unconfigured_profile_is_all_none_not_invented():
    """No defaults. A missing seller name must become an honest,
    reportable gap — never a fabricated value in a feed."""
    _clear_env()
    profile = load_seller_profile()
    assert profile.name is None
    assert profile.is_checkout_ready is False


def test_checkout_readiness_needs_both_policy_urls():
    assert SellerProfile("X", None, "https://p", None).is_checkout_ready is False
    assert SellerProfile("X", None, None, "https://t").is_checkout_ready is False
    assert SellerProfile(None, None, "https://p", "https://t").is_checkout_ready is False
    assert SellerProfile("X", None, "https://p", "https://t").is_checkout_ready is True


def test_a_configured_placeholder_is_treated_as_unset():
    """The spec rejects these strings by name. Passing one through would
    just move the failure downstream to feed ingestion, where it's much
    harder to diagnose."""
    _clear_env()
    for placeholder in ("unknown", "N/A", "null", "  none  ", "Unbranded", "-"):
        os.environ["STOREMIND_SELLER_NAME"] = placeholder
        assert load_seller_profile().name is None, placeholder
    _clear_env()


def test_whitespace_is_stripped():
    _clear_env()
    os.environ["STOREMIND_SELLER_NAME"] = "  Kurta House  "
    assert load_seller_profile().name == "Kurta House"
    _clear_env()


# --- Part 2: seller identity verification (added this session) ---

VALID_GSTIN = "27AAPFU0939F1ZV"
INVALID_GSTIN = "27AAPFU0939F1ZW"       # single check-digit typo
VALID_UDYAM = "UDYAM-MH-00-0000123"
VALID_PAN = "AAPFU0939F"
INVALID_PAN = "AAPFU09390"
VALID_UPI = "shop@oksbi"
VALID_WA = "9876543210"

_IDENTITY_VARS = [
    "STOREMIND_SELLER_GSTIN", "STOREMIND_SELLER_UDYAM", "STOREMIND_SELLER_PAN",
    "STOREMIND_SELLER_UPI_ID", "STOREMIND_SELLER_WHATSAPP_PHONE",
]


def _load_identity(monkeypatch, **env):
    for v in _IDENTITY_VARS:
        monkeypatch.delenv(v, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("STOREMIND_SELLER_NAME", "Test Seller")
    import seller_profile
    importlib.reload(seller_profile)
    return seller_profile.load_seller_profile()


def test_nothing_configured_never_blocks(monkeypatch):
    p = _load_identity(monkeypatch)
    assert p.verified is False
    assert p.blocks_ingestion is False
    assert p.verification_method is None


def test_gstin_alone_verifies(monkeypatch):
    p = _load_identity(monkeypatch, STOREMIND_SELLER_GSTIN=VALID_GSTIN)
    assert p.verified is True
    assert p.verification_method == "gstin"
    assert p.blocks_ingestion is False


def test_udyam_alone_verifies(monkeypatch):
    p = _load_identity(monkeypatch, STOREMIND_SELLER_UDYAM=VALID_UDYAM)
    assert p.verified is True
    assert p.verification_method == "udyam"


def test_upi_plus_pan_verifies(monkeypatch):
    p = _load_identity(monkeypatch, STOREMIND_SELLER_UPI_ID=VALID_UPI, STOREMIND_SELLER_PAN=VALID_PAN)
    assert p.verified is True
    assert p.verification_method == "upi+pan"


def test_upi_plus_whatsapp_verifies(monkeypatch):
    p = _load_identity(monkeypatch, STOREMIND_SELLER_UPI_ID=VALID_UPI, STOREMIND_SELLER_WHATSAPP_PHONE=VALID_WA)
    assert p.verified is True
    assert p.verification_method == "upi+whatsapp"


def test_upi_alone_is_not_enough_and_blocks_with_a_real_reason(monkeypatch):
    """UPI alone (no PAN, no WhatsApp) must NOT verify - and must block
    with an ACTUAL explanation, not a silent empty reasons tuple. This
    exact case was found empty during development and fixed; this test
    exists specifically so it can't regress silently."""
    p = _load_identity(monkeypatch, STOREMIND_SELLER_UPI_ID=VALID_UPI)
    assert p.verified is False
    assert p.blocks_ingestion is True
    assert len(p.verification_reasons) > 0
    assert "paired" in p.verification_reasons[0]


def test_pan_alone_is_not_enough_and_blocks_with_a_real_reason(monkeypatch):
    p = _load_identity(monkeypatch, STOREMIND_SELLER_PAN=VALID_PAN)
    assert p.verified is False
    assert p.blocks_ingestion is True
    assert len(p.verification_reasons) > 0


def test_invalid_gstin_alone_blocks(monkeypatch):
    p = _load_identity(monkeypatch, STOREMIND_SELLER_GSTIN=INVALID_GSTIN)
    assert p.verified is False
    assert p.blocks_ingestion is True
    assert "GSTIN" in p.verification_reasons[0]


def test_invalid_gstin_is_rescued_by_a_valid_fallback_path(monkeypatch):
    """An invalid GSTIN must NOT hard-fail the whole seller if another
    configured path (UPI+PAN here) independently verifies."""
    p = _load_identity(
        monkeypatch,
        STOREMIND_SELLER_GSTIN=INVALID_GSTIN,
        STOREMIND_SELLER_UPI_ID=VALID_UPI,
        STOREMIND_SELLER_PAN=VALID_PAN,
    )
    assert p.verified is True
    assert p.verification_method == "upi+pan"
    assert p.blocks_ingestion is False


def test_upi_valid_pan_invalid_blocks_with_pan_specific_reason(monkeypatch):
    p = _load_identity(monkeypatch, STOREMIND_SELLER_UPI_ID=VALID_UPI, STOREMIND_SELLER_PAN=INVALID_PAN)
    assert p.verified is False
    assert p.blocks_ingestion is True
    assert any("PAN" in r for r in p.verification_reasons)


@pytest.mark.parametrize("placeholder", ["n/a", "none", "null", "-", "unknown"])
def test_placeholder_identity_values_are_treated_as_fully_unconfigured(monkeypatch, placeholder):
    p = _load_identity(
        monkeypatch,
        STOREMIND_SELLER_GSTIN=placeholder,
        STOREMIND_SELLER_UDYAM=placeholder,
        STOREMIND_SELLER_PAN=placeholder,
        STOREMIND_SELLER_UPI_ID=placeholder,
        STOREMIND_SELLER_WHATSAPP_PHONE=placeholder,
    )
    assert p.any_verification_field_configured is False
    assert p.blocks_ingestion is False


def test_strongest_available_path_is_reported_when_multiple_would_work(monkeypatch):
    p = _load_identity(monkeypatch, STOREMIND_SELLER_GSTIN=VALID_GSTIN, STOREMIND_SELLER_UDYAM=VALID_UDYAM)
    assert p.verified is True
    assert p.verification_method == "gstin"  # GSTIN checked first - real checksum beats structure-only