import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fallback_parser import FallbackParser  # noqa: E402


def test_strict_json_success():
    p = FallbackParser()
    raw = '{"name": "Cotton Tote Bag", "price": 19.99, "category": "bags"}'
    result = p.parse(raw)
    assert result.success
    assert result.extraction_method == "strict_json"
    assert result.data["name"] == "Cotton Tote Bag"


def test_markdown_fenced_json_recovers_at_layer2():
    p = FallbackParser()
    raw = '```json\n{"name": "Mug", "price": 12.5}\n```'
    result = p.parse(raw)
    assert result.success
    assert result.extraction_method == "relaxed_schema"
    assert result.data["name"] == "Mug"


def test_trailing_comma_recovers_at_layer2():
    p = FallbackParser()
    raw = '{"name": "Mug", "price": 12.5,}'
    result = p.parse(raw)
    assert result.success
    assert result.extraction_method == "relaxed_schema"


def test_single_quoted_json_recovers_at_layer2():
    p = FallbackParser()
    raw = "{'name': 'Mug', 'price': 12.5}"
    result = p.parse(raw)
    assert result.success
    assert result.extraction_method == "relaxed_schema"
    assert result.data["name"] == "Mug"


def test_prose_response_recovers_at_layer3():
    p = FallbackParser()
    raw = "Here is the structured product:\nName: Ceramic Mug\nPrice: $14.99\nCategory: Kitchenware\n"
    result = p.parse(raw)
    assert result.success
    assert result.extraction_method == "loose_text"
    assert result.data["name"] == "Ceramic Mug"
    assert result.data["price"] == 14.99


def test_prose_keywords_line_recovers_as_a_list():
    """Regression test: prose keyword lines used to have no recovery path
    at all — keywords always came back empty from loose_text, regardless
    of what the model actually wrote. Found via the 73-row stress test."""
    p = FallbackParser()
    raw = "Name: Ceramic Mug\nPrice: 14.99\nKeywords: mug, ceramic, kitchenware, coffee\n"
    result = p.parse(raw)
    assert result.success
    assert result.data["keywords"] == ["mug", "ceramic", "kitchenware", "coffee"]


def test_prose_material_line_becomes_a_dict_not_a_raw_string():
    """Regression test: a 'Material: cotton' line used to overwrite
    attributes with the raw string 'cotton' instead of {'material':
    'cotton'} — which would silently corrupt len(attributes)-based
    readiness scoring downstream. Found via the 73-row stress test."""
    p = FallbackParser()
    raw = "Name: Tote Bag\nPrice: 499\nMaterial: cotton\nColor: natural\n"
    result = p.parse(raw)
    assert result.success
    assert isinstance(result.data["attributes"], dict)
    assert result.data["attributes"] == {"material": "cotton", "color": "natural"}


def test_total_garbage_fails_cleanly():
    p = FallbackParser()
    raw = "I could not process this request due to insufficient information."
    result = p.parse(raw)
    assert not result.success
    assert result.extraction_method == "failed"


if __name__ == "__main__":
    tests = [f for name, f in list(globals().items()) if name.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS  {t.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed")
