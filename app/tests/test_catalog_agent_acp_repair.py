"""
Tests for the brand cascade (stages 1-2) and length-repair wiring inside
catalog_agent.py, catalog_agent_aistudio.py, and catalog_agent_mock.py.

Stages 3-4 of the brand cascade live in brand_resolution.py and are
covered by test_brand_resolution.py — these tests only cover what each
catalog agent does BEFORE that: does it pass through an explicit source
brand, does it correctly attempt (or skip) vision extraction, does it
refuse to trust a brand Gemini returned without an image actually being
sent, and does it repair an oversized field.

The two live backends (catalog_agent.py, catalog_agent_aistudio.py) need
a `google.genai` client — deliberately NOT assumed to be installed here
(matching this project's existing "imports cleanly without google-genai
installed" contract for local mock-only testing), so a lightweight fake
SDK is injected via sys.modules for the duration of these tests only.
"""

from __future__ import annotations

import json
import sys
import types as pytypes

import pytest


# ---------------------------------------------------------------------
# Fake google.genai SDK — only used by the two live-backend test classes
# below. Injected via sys.modules so catalog_agent.py's `from google
# import genai` / `from google.genai import types` succeed regardless of
# whether the real package is installed in this environment.
# ---------------------------------------------------------------------

class _FakePart:
    def __init__(self, data, mime_type):
        self.data = data
        self.mime_type = mime_type

    @staticmethod
    def from_bytes(data, mime_type):
        return _FakePart(data, mime_type)


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, script):
        self.script = script  # list of canned responses, or a callable(model, contents)
        self.calls = []

    def generate_content(self, model, contents):
        self.calls.append({"model": model, "contents": contents})
        if callable(self.script):
            return _FakeResponse(self.script(model, contents))
        return _FakeResponse(self.script[len(self.calls) - 1])


class _FakeClient:
    def __init__(self, script):
        self.models = _FakeModels(script)


@pytest.fixture(autouse=True, scope="module")
def _install_fake_genai_sdk():
    fake_google = pytypes.ModuleType("google")
    fake_genai = pytypes.ModuleType("google.genai")
    fake_genai_types = pytypes.ModuleType("google.genai.types")
    fake_genai_types.Part = _FakePart
    fake_google.genai = fake_genai

    originals = {name: sys.modules.get(name) for name in ("google", "google.genai", "google.genai.types")}
    sys.modules["google"] = fake_google
    sys.modules["google.genai"] = fake_genai
    sys.modules["google.genai.types"] = fake_genai_types
    yield
    for name, mod in originals.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def _agent_with_script(agent_cls, script, **env):
    for key, value in env.items():
        __import__("os").environ.setdefault(key, value)
    agent = agent_cls()
    agent._client = _FakeClient(script)
    return agent, agent._client


LONG_NAME = (
    "Premium Handwoven Cotton Kurta With Intricate Embroidery And Traditional "
    "Design Elements For Festive Wear Occasions And Everyday Comfort Fit Sizes "
    "Available In Multiple Colors And Regional Styles"
)
assert len(LONG_NAME) > 150, "test fixture must exceed the ACP title limit"


class TestVertexAIBackend:
    """catalog_agent.CatalogStructuringAgent"""

    def _agent(self, script):
        from catalog_agent import CatalogStructuringAgent

        return _agent_with_script(CatalogStructuringAgent, script, GOOGLE_CLOUD_PROJECT="test-project")

    def test_explicit_brand_never_triggers_an_image_call(self):
        payload = json.dumps({"name": "Kurta", "category": "apparel", "description": "A kurta.", "attributes": {}, "keywords": ["kurta"], "price": 999})
        agent, client = self._agent([payload])
        result = agent.structure_row({"name": "Kurta", "price": 999, "brand": "Fabindia", "sku": "SKU1"}, "SKU1")
        assert result.structured["brand"] == "Fabindia"
        assert result.structured["brand_source"] == "source"
        assert len(client.models.calls) == 1
        assert isinstance(client.models.calls[0]["contents"], str)

    def test_vision_stage_attaches_image_and_trusts_a_grounded_read(self):
        payload = json.dumps({"name": "Namak", "category": "grocery", "description": "Salt.", "attributes": {}, "keywords": ["salt"], "price": 45, "brand": "Tata"})
        agent, client = self._agent([payload])
        agent._fetch_image_bytes = staticmethod(lambda ref, timeout=5.0: (b"fake-bytes", "image/jpeg"))
        result = agent.structure_row({"name": "Loose salt", "price": 45, "image_url": "https://example.com/s.jpg", "sku": "SKU2"}, "SKU2")
        assert result.structured["brand"] == "Tata"
        assert result.structured["brand_source"] == "vision"
        assert len(client.models.calls[0]["contents"]) == 2  # prompt + image part

    def test_image_fetch_failure_falls_through_without_crashing(self):
        payload = json.dumps({"name": "Namak", "category": "grocery", "description": "Salt.", "attributes": {}, "keywords": ["salt"], "price": 45, "brand": "Tata"})
        agent, client = self._agent([payload])
        agent._fetch_image_bytes = staticmethod(lambda ref, timeout=5.0: None)
        result = agent.structure_row({"name": "Loose salt", "price": 45, "image_url": "https://example.com/s.jpg", "sku": "SKU3"}, "SKU3")
        assert isinstance(client.models.calls[0]["contents"], str)
        assert result.structured.get("brand_source") != "vision"

    def test_hallucinated_brand_without_an_image_is_stripped(self):
        """The prompt tells Gemini to omit brand when no image is sent.
        If it ignores that, the code must not trust it anyway."""
        payload = json.dumps({"name": "Namak", "category": "grocery", "description": "Salt.", "attributes": {}, "keywords": ["salt"], "price": 45, "brand": "Tata"})
        agent, client = self._agent([payload])
        result = agent.structure_row({"name": "Loose salt", "price": 45, "sku": "SKU4"}, "SKU4")
        assert isinstance(client.models.calls[0]["contents"], str)
        assert "brand" not in result.structured

    def test_oversized_name_triggers_the_real_compressor_and_lands_under_limit(self):
        payload = json.dumps({"name": LONG_NAME, "category": "apparel", "description": "A kurta.", "attributes": {}, "keywords": ["kurta"], "price": 999})
        calls = {"n": 0}

        def script(model, contents):
            calls["n"] += 1
            return payload if calls["n"] == 1 else LONG_NAME[:150]

        agent, client = self._agent(script)
        result = agent.structure_row({"name": LONG_NAME, "price": 999, "sku": "SKU5"}, "SKU5")
        assert len(result.structured["name"]) <= 150
        assert calls["n"] == 2
        assert any("compressed" in n for n in result.structured["acp_repair_notes"])


class TestAIStudioBackend:
    """catalog_agent_aistudio.GeminiAIStudioAgent — same contract as the
    Vertex backend; kept as a separate, lighter set of checks to catch
    copy-paste drift between the two near-identical files."""

    def _agent(self, script):
        from catalog_agent_aistudio import GeminiAIStudioAgent

        return _agent_with_script(GeminiAIStudioAgent, script, GEMINI_API_KEY="test-key")

    def test_explicit_brand_passthrough(self):
        payload = json.dumps({"name": "Kurta", "category": "apparel", "description": "A kurta.", "attributes": {}, "keywords": ["kurta"], "price": 999})
        agent, client = self._agent([payload])
        result = agent.structure_row({"name": "Kurta", "price": 999, "brand": "Fabindia", "sku": "SKU1"}, "SKU1")
        assert result.structured["brand"] == "Fabindia"
        assert result.structured["brand_source"] == "source"

    def test_hallucinated_brand_without_image_is_stripped(self):
        payload = json.dumps({"name": "Salt", "category": "grocery", "description": "Salt.", "attributes": {}, "keywords": ["salt"], "price": 45, "brand": "Tata"})
        agent, client = self._agent([payload])
        result = agent.structure_row({"name": "Loose salt", "price": 45, "sku": "SKU2"}, "SKU2")
        assert "brand" not in result.structured

    def test_oversized_field_triggers_real_compressor(self):
        payload = json.dumps({"name": LONG_NAME, "category": "apparel", "description": "A kurta.", "attributes": {}, "keywords": ["kurta"], "price": 999})
        calls = {"n": 0}

        def script(model, contents):
            calls["n"] += 1
            return payload if calls["n"] == 1 else LONG_NAME[:150]

        agent, client = self._agent(script)
        result = agent.structure_row({"name": LONG_NAME, "price": 999, "sku": "SKU3"}, "SKU3")
        assert len(result.structured["name"]) <= 150
        assert calls["n"] == 2


class TestMockBackend:
    """catalog_agent_mock.MockCatalogStructuringAgent — zero external
    dependencies, so this is the always-runnable coverage of the
    cascade's control flow, independent of google-genai being
    installed."""

    def _agent(self):
        from catalog_agent_mock import MockCatalogStructuringAgent

        agent = MockCatalogStructuringAgent()

        def force_style0(cleaned_row):
            name = str(cleaned_row.get("name", "")).strip()
            price = cleaned_row.get("price")
            payload = {"name": name, "category": "general", "description": f"{name} item.", "attributes": {}, "keywords": ["product"], "price": price}
            return json.dumps(payload)

        agent._fake_gemini_output = force_style0
        return agent

    def test_explicit_brand_passthrough(self):
        agent = self._agent()
        result = agent.structure_row({"name": "Cotton Kurta", "price": 999, "brand": "Fabindia", "sku": "SKU1"}, "SKU1")
        assert result.structured["brand"] == "Fabindia"
        assert result.structured["brand_source"] == "source"

    def test_mock_vision_extracts_a_recognizable_brand_from_name_plus_image(self):
        agent = self._agent()
        result = agent.structure_row({"name": "Tata Namak 1kg pkt", "price": 45, "image_url": "https://example.com/i.jpg", "sku": "SKU2"}, "SKU2")
        assert result.structured["brand"] == "Tata"
        assert result.structured["brand_source"] == "vision"

    def test_no_source_and_no_image_leaves_brand_unset_for_main_py_to_resolve(self):
        agent = self._agent()
        result = agent.structure_row({"name": "Loose sugar packet", "price": 45, "sku": "SKU3"}, "SKU3")
        assert "brand" not in result.structured

    def test_image_present_but_no_recognizable_brand_makes_no_guess(self):
        agent = self._agent()
        result = agent.structure_row({"name": "Loose rice 5kg", "price": 250, "image_url": "https://example.com/i.jpg", "sku": "SKU4"}, "SKU4")
        assert "brand" not in result.structured

    def test_oversized_name_is_deterministically_truncated_under_the_limit(self):
        long_name = "Premium Handwoven Cotton Kurta " * 6  # well over 150
        agent = self._agent()
        result = agent.structure_row({"name": long_name, "price": 999, "sku": "SKU5"}, "SKU5")
        assert len(result.structured["name"]) <= 150
        assert any("truncated" in n for n in result.structured["acp_repair_notes"])
        
        