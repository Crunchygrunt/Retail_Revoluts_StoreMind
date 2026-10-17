import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from readiness_score import (  # noqa: E402
    compute_readiness_score,
    _is_scriptio_continua,
    _descriptive_word_count,
    _real_content_length,
)


def test_high_score_for_genuinely_rich_product():
    raw_row = {
        "name": "Handwoven Cotton Tote Bag",
        "price": 499,
        "description": "Durable everyday tote, hand-loomed cotton, natural dye.",
        "image": "tote.jpg",
    }
    structured = {
        "name": "Cotton Tote Bag",
        "category": "Bags",
        "description": "A durable everyday tote bag made from hand-loomed cotton, great for shopping.",
        "attributes": {"material": "cotton", "style": "tote", "color": "natural"},
        "keywords": ["tote", "bag", "cotton", "eco-friendly", "everyday"],
        "price": 499,
    }
    result = compute_readiness_score(structured, "strict_json", raw_row=raw_row)
    assert result.score >= 85, result.score
    assert result.meets_threshold


def test_low_score_for_sparse_structured_output():
    raw_row = {"name": "Mug", "price": 199, "description": "", "image": ""}
    structured = {"name": "Mug", "category": None, "description": "", "attributes": {}, "keywords": [], "price": 199}
    result = compute_readiness_score(structured, "loose_text", raw_row=raw_row)
    assert result.score < 50, result.score
    assert not result.meets_threshold


def test_fabrication_from_thin_input_is_caught():
    """The core regression test for the bug we actually hit: a near-empty
    raw row ('Item', no description, no image) where Gemini nonetheless
    returns a fully-formed, well-shaped structured record. Before
    source_richness existed, this scored 90.3 and passed the gate. It
    should now score meaningfully lower and fail the default threshold."""
    raw_row = {"name": "Item", "price": 99, "description": "", "image": ""}
    fabricated_structured = {
        "name": "Generic Retail Item",
        "category": "General Merchandise",
        "description": "A versatile, high-quality item suitable for everyday use in a variety of settings.",
        "attributes": {"type": "general", "usage": "everyday", "quality": "high"},
        "keywords": ["item", "product", "general", "everyday", "versatile"],
        "price": 99,
    }
    result = compute_readiness_score(fabricated_structured, "strict_json", raw_row=raw_row)
    assert result.score < 70, (
        f"expected a thin/generic source input to score below threshold even with polished "
        f"Gemini output, got {result.score} — source_richness isn't dominating the way it should"
    )
    assert not result.meets_threshold
    assert any("generic" in n.lower() or "no description" in n.lower() for n in result.notes)


def test_extraction_method_still_affects_score():
    raw_row = {"name": "Recycled Glass Vase", "price": 150, "description": "Vase made from recycled glass, hand-blown.", "image": "vase.jpg"}
    structured = {
        "name": "Vase",
        "category": "Home Decor",
        "description": "A recycled glass vase for flowers and decor.",
        "attributes": {"material": "glass"},
        "keywords": ["vase", "glass", "decor"],
        "price": 150,
    }
    strict = compute_readiness_score(structured, "strict_json", raw_row=raw_row)
    loose = compute_readiness_score(structured, "loose_text", raw_row=raw_row)
    assert strict.score > loose.score


def test_threshold_is_configurable():
    raw_row = {"name": "Two Word Item", "price": 10, "description": "A reasonably descriptive sentence about this product.", "image": "x.jpg"}
    structured = {
        "name": "Item",
        "category": "General",
        "description": "A generic item description here that is long enough.",
        "attributes": {"a": 1},
        "keywords": ["a", "b", "c"],
        "price": 10,
    }
    result = compute_readiness_score(structured, "strict_json", raw_row=raw_row, threshold=99.0)
    assert not result.meets_threshold


def test_cjk_name_is_not_treated_as_a_single_generic_word():
    """Regression test: CJK scripts don't use spaces between words, so the
    original whitespace-based word count treated EVERY Chinese/Japanese/
    Korean product name as a single generic 'word' — same penalty as if
    the raw name had literally been 'Item'. Found via the stress test with
    a genuinely specific 7-character Chinese product name that scored
    right at the threshold edge for the wrong reason."""
    raw_row_cjk = {
        "name": "手工编织羊毛毯",  # "hand-knitted wool blanket" — specific, not generic
        "price": 2299,
        "description": "Hand-knitted wool blanket, traditional pattern.",
        "image": "blanket.jpg",
    }
    raw_row_english_generic = {"name": "Item", "price": 2299, "description": "Hand-knitted wool blanket, traditional pattern.", "image": "blanket.jpg"}
    structured = {
        "name": "Hand-Knitted Wool Blanket",
        "category": "Home Decor",
        "description": "A traditional hand-knitted wool blanket with a classic pattern.",
        "attributes": {"material": "wool"},
        "keywords": ["blanket", "wool", "handmade", "home", "decor"],
        "price": 2299,
    }
    cjk_result = compute_readiness_score(structured, "strict_json", raw_row=raw_row_cjk)
    generic_result = compute_readiness_score(structured, "strict_json", raw_row=raw_row_english_generic)
    assert cjk_result.score > generic_result.score, (
        "a specific CJK name should score meaningfully better than a literally generic "
        "English name, given identical structured output — they were equal before this fix"
    )
    assert not any("generic" in n.lower() for n in cjk_result.notes)


def test_scriptio_continua_detection_covers_sea_scripts():
    """Direct check that Thai, Khmer, Lao, and Myanmar are now detected
    alongside the original CJK scripts — the same whitespace-tokenization
    gap already fixed for CJK (test_cjk_name_is_not_treated_as_a_single_
    generic_word) previously applied to these four scripts too."""
    scriptio_continua_examples = {
        "Thai": "\u0e2a\u0e27\u0e31\u0e2a\u0e14\u0e35",
        "Khmer": "\u179f\u17bd\u179f\u17d2\u178f\u17b8",
        "Lao": "\u0eaa\u0eb0\u0e9a\u0eb2\u0e8d\u0e94\u0eb5",
        "Myanmar": "\u1019\u1004\u103a\u1039\u1002\u101c\u102c\u1015\u102b",
    }
    for script_name, sample in scriptio_continua_examples.items():
        assert _is_scriptio_continua(sample), f"{script_name} text not detected: {sample!r}"
    assert not _is_scriptio_continua("Plain English Name")


def test_thai_name_is_not_treated_as_a_single_generic_word():
    """Same regression shape as the CJK test, for Thai — one of the four
    newly-covered SEA scripts."""
    raw_row_thai = {
        "name": "\u0e1c\u0e49\u0e32\u0e44\u0e2b\u0e21\u0e17\u0e2d\u0e21\u0e37\u0e2d\u0e04\u0e38\u0e13\u0e20\u0e32\u0e1e\u0e2a\u0e39\u0e07",
        "price": 1200,
        "description": "Hand-woven silk fabric, traditional Thai weaving technique.",
        "image": "silk.jpg",
    }
    raw_row_english_generic = {
        "name": "Item",
        "price": 1200,
        "description": "Hand-woven silk fabric, traditional Thai weaving technique.",
        "image": "silk.jpg",
    }
    structured = {
        "name": "Hand-Woven Silk Fabric",
        "category": "Textiles",
        "description": "A high-quality hand-woven silk fabric made using a traditional technique.",
        "attributes": {"material": "silk"},
        "keywords": ["silk", "fabric", "handmade", "textile", "thai"],
        "price": 1200,
    }
    thai_result = compute_readiness_score(structured, "strict_json", raw_row=raw_row_thai)
    generic_result = compute_readiness_score(structured, "strict_json", raw_row=raw_row_english_generic)
    assert thai_result.score > generic_result.score, (
        "a specific Thai name should score meaningfully better than a literally generic "
        "English name, given identical structured output"
    )
    assert not any("generic" in n.lower() for n in thai_result.notes)


def test_underscore_and_hyphen_joined_names_count_as_specific():
    """Regression test: social-commerce exports (Shopee/Lazada/WhatsApp
    Business) commonly join real descriptive words with '_' or '-'
    instead of a space. A whitespace split saw these as ONE word and
    penalized them as generic — same penalty as if the name had
    literally been 'Item'."""
    assert _descriptive_word_count("Premium_Cotton_Kurta") >= 2
    assert _descriptive_word_count("PREMIUM-COTTON-KURTA-FOR-MEN") >= 2


def test_emoji_padded_generic_name_still_counts_as_generic():
    """The flip side of the same bug: emoji padding around an
    actually-generic name used to inflate the whitespace word count
    past the 'specific enough' threshold (the padding characters
    counted as 'words'). Emoji aren't letters or digits, so they no
    longer count. Note the limit of this fix: '#trending #musthave'
    still counts as 2 words, because 'trending' and 'musthave' ARE
    real letter-content once the '#' symbol is stripped — this fix
    removes decorative padding, it doesn't judge whether a real word is
    marketing fluff versus product-relevant."""
    assert _descriptive_word_count("\U0001f525 ITEM \U0001f525") < 2
    assert _descriptive_word_count("\u2728\u2728\u2728 ITEM \u2728\u2728\u2728") < 2


def test_generic_placeholder_with_counter_still_generic():
    """A bare number doesn't make a name specific on its own -- 'ITEM_1'
    is still just 'Item' with a counter attached, not two real words."""
    assert _descriptive_word_count("ITEM_1") < 2
    assert _descriptive_word_count("PRODUCT-001") < 2


def test_token_with_letters_and_digits_together_still_counts():
    """A unit/size token like '500ml' IS genuinely descriptive and
    should still count, unlike a bare digit."""
    assert _descriptive_word_count("Cotton Kurta 500ml") >= 2


def test_pure_emoji_description_has_zero_real_content():
    """A description that's purely decorative emoji provides zero
    informative content for Gemini to structure from -- it shouldn't
    score as if it were that many characters of real description."""
    padding_only = "\U0001f525" * 20  # 20 raw characters, zero real content
    assert len(padding_only) == 20
    assert _real_content_length(padding_only) == 0


def test_informal_allcaps_description_is_not_penalized():
    """The actual concern this fix addresses: a genuinely informal but
    substantive listing (the kind exported from Shopee/TikTok Shop/
    WhatsApp Business) shouldn't be penalized just for being all-caps
    or lightly emoji-decorated."""
    assert _real_content_length("NICE LONGYI FOR MEN gd quality fast delivery") >= 20


def test_social_commerce_style_listing_scores_well_end_to_end():
    """Integration-level check: a genuinely informal social-commerce
    listing -- underscore-joined name, all-caps emoji-flanked
    description -- should score on the strength of its real content,
    not get penalized for its formatting style."""
    raw_row = {
        "name": "Premium_Cotton_Kurta",
        "price": 899,
        "description": "\U0001f525 SOFT COTTON KURTA available in 3 colors, fast delivery \U0001f525",
        "image": "kurta.jpg",
    }
    structured = {
        "name": "Premium Cotton Kurta",
        "category": "Apparel",
        "description": "A soft cotton kurta available in three colors with fast delivery.",
        "attributes": {"material": "cotton"},
        "keywords": ["kurta", "cotton", "apparel", "casual", "premium"],
        "price": 899,
    }
    result = compute_readiness_score(structured, "strict_json", raw_row=raw_row)
    assert result.meets_threshold, (result.score, result.notes)
    assert not any("generic" in n.lower() for n in result.notes)


if __name__ == "__main__":
    tests = [f for name, f in list(globals().items()) if name.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS  {t.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed")
    