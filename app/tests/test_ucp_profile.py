import os
import pytest
from ucp_profile import build_ucp_profile, is_configured, UCP_VERSION


def test_explicit_params():
    p = build_ucp_profile(
        base_url="https://storemind-ingest-444891828026.us-central1.run.app",
        seller_name="Zara",
        merchant_id="MERCH123",
    )
    assert p["ucp"]["version"] == UCP_VERSION
    assert (
        p["ucp"]["services"]["dev.ucp.shopping"][0]["endpoint"]
        == "https://storemind-ingest-444891828026.us-central1.run.app/ucp/v1"
    )


def test_trailing_slash_stripped():
    p = build_ucp_profile(base_url="https://example.com/", seller_name="X", merchant_id="Y")
    assert p["ucp"]["services"]["dev.ucp.shopping"][0]["endpoint"] == "https://example.com/ucp/v1"


def test_mcp_transport_declared_alongside_rest():
    """
    The MCP binding adds a SECOND services entry, not a replacement —
    a business can offer both transports. Confirmed shape from
    ucp.dev's own MCP binding spec: a plain HTTPS endpoint (Streamable
    HTTP), separate from the REST endpoint, same origin.
    """
    p = build_ucp_profile(base_url="https://example.com", seller_name="X", merchant_id="Y")
    services = p["ucp"]["services"]["dev.ucp.shopping"]
    assert len(services) == 2
    rest_entry = next(s for s in services if s["transport"] == "rest")
    mcp_entry = next(s for s in services if s["transport"] == "mcp")
    assert rest_entry["endpoint"] == "https://example.com/ucp/v1"
    assert mcp_entry["endpoint"] == "https://example.com/ucp/mcp"
    assert mcp_entry["endpoint"] != rest_entry["endpoint"]


def test_only_implemented_capabilities_declared():
    p = build_ucp_profile(base_url="https://example.com", seller_name="X", merchant_id="Y")
    assert set(p["ucp"]["capabilities"].keys()) == {
        "dev.ucp.shopping.catalog.search",
        "dev.ucp.shopping.catalog.lookup",
        "dev.ucp.shopping.checkout",
    }


@pytest.mark.parametrize(
    "capability",
    [
        "dev.ucp.shopping.fulfillment",
        "dev.ucp.shopping.discount",
        "dev.ucp.shopping.order",
        "dev.ucp.common.identity_linking",
        "dev.ucp.shopping.cart",
    ],
)
def test_unimplemented_capabilities_not_declared(capability):
    p = build_ucp_profile(base_url="https://example.com", seller_name="X", merchant_id="Y")
    assert capability not in p["ucp"]["capabilities"]


def test_no_signing_keys_fabricated():
    p = build_ucp_profile(base_url="https://example.com", seller_name="X", merchant_id="Y")
    assert "signing_keys" not in p


def test_payment_handler_shape():
    p = build_ucp_profile(base_url="https://example.com", seller_name="Zara", merchant_id="MERCH123")
    handler = p["ucp"]["payment_handlers"]["com.google.pay"][0]
    assert handler["config"]["environment"] == "TEST"
    assert handler["config"]["merchant_info"]["merchant_name"] == "Zara"
    assert handler["id"] == "MERCH123"


def test_env_var_fallback(monkeypatch):
    monkeypatch.setenv("STOREMIND_UCP_BASE_URL", "https://from-env.example.com")
    monkeypatch.setenv("STOREMIND_SELLER_NAME", "EnvSeller")
    monkeypatch.setenv("STOREMIND_UCP_MERCHANT_ID", "ENV_MERCH")
    p = build_ucp_profile()
    assert p["ucp"]["services"]["dev.ucp.shopping"][0]["endpoint"] == "https://from-env.example.com/ucp/v1"
    assert p["ucp"]["payment_handlers"]["com.google.pay"][0]["config"]["merchant_info"]["merchant_name"] == "EnvSeller"


def test_unconfigured_profile_builds_without_raising(monkeypatch):
    monkeypatch.delenv("STOREMIND_UCP_BASE_URL", raising=False)
    monkeypatch.delenv("STOREMIND_SELLER_NAME", raising=False)
    monkeypatch.delenv("STOREMIND_UCP_MERCHANT_ID", raising=False)
    p = build_ucp_profile()
    assert p is not None
    assert p["ucp"]["payment_handlers"]["com.google.pay"][0]["id"] == "UNCONFIGURED_TEST_MERCHANT_ID"


def test_is_configured():
    configured = build_ucp_profile(base_url="https://example.com", seller_name="X", merchant_id="Y")
    assert is_configured(configured)

    unconfigured = build_ucp_profile()
    assert not is_configured(unconfigured)
    
    