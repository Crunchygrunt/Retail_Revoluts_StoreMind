"""
Tests for infer_brand_by_category (brand_resolution.py) — kept in its
own file, separate from the existing test_brand_resolution.py, since
that file's exact content wasn't available to safely merge into without
risking an overwrite of tests already covering infer_brand/resolve_brand.
"""

from brand_resolution import infer_brand, infer_brand_by_category


class FakeStore:
    def __init__(self, products):
        self._products = products

    def list_products(self, limit=10_000):
        return self._products[:limit]


def test_infer_brand_store_wide_is_completely_unchanged():
    """Regression guard: the _vote extraction must not alter infer_brand's
    existing, separately-tested behavior at all."""
    products = [{"category": "Fashion", "brand": "Fabindia", "brand_source": "source"} for _ in range(90)]
    products += [{"category": "Kitchenware", "brand": None, "brand_source": None} for _ in range(10)]
    store = FakeStore(products)
    result = infer_brand(store)
    assert result.applicable is True
    assert result.brand == "Fabindia"
    assert result.sample_size == 90


def test_a_confident_majority_in_one_category_does_not_bleed_into_another():
    """The exact motivating bug: a 90-kurta/10-kitchenware catalog must
    not let Fabindia's Fashion majority get applied to unbranded mugs."""
    products = [{"category": "Fashion", "brand": "Fabindia", "brand_source": "source"} for _ in range(90)]
    products += [{"category": "Kitchenware", "brand": None, "brand_source": None} for _ in range(10)]
    store = FakeStore(products)
    by_category = infer_brand_by_category(store)

    assert by_category["Fashion"].applicable is True
    assert by_category["Fashion"].brand == "Fabindia"
    assert "Kitchenware" not in by_category, "a category with zero trusted votes must not appear as a key at all"


def test_a_genuinely_mixed_category_stays_inconclusive():
    products = [
        {"category": "Electronics", "brand": "Samsung", "brand_source": "source"},
        {"category": "Electronics", "brand": "Samsung", "brand_source": "source"},
        {"category": "Electronics", "brand": "boAt", "brand_source": "source"},
        {"category": "Electronics", "brand": "boAt", "brand_source": "source"},
        {"category": "Electronics", "brand": "JBL", "brand_source": "vision"},
    ]
    store = FakeStore(products)
    result = infer_brand_by_category(store)["Electronics"]
    assert result.applicable is False


def test_a_category_below_minimum_sample_size_stays_inapplicable_even_if_unanimous():
    products = [{"category": "Beauty", "brand": "Lakme", "brand_source": "source"} for _ in range(2)]
    store = FakeStore(products)
    result = infer_brand_by_category(store)["Beauty"]
    assert result.applicable is False
    assert result.sample_size == 2


def test_two_independent_categories_each_get_their_own_correct_answer():
    products = [{"category": "Fashion", "brand": "Fabindia", "brand_source": "source"} for _ in range(10)]
    products += [{"category": "Electronics", "brand": "Samsung", "brand_source": "source"} for _ in range(10)]
    store = FakeStore(products)
    by_category = infer_brand_by_category(store)
    assert by_category["Fashion"].brand == "Fabindia"
    assert by_category["Electronics"].brand == "Samsung"


def test_custom_thresholds_are_respected_per_category_same_as_the_store_wide_version():
    products = [{"category": "Fashion", "brand": "Fabindia", "brand_source": "source"} for _ in range(3)]
    store = FakeStore(products)
    assert infer_brand_by_category(store, min_sample_size=5)["Fashion"].applicable is False
    assert infer_brand_by_category(store, min_sample_size=2)["Fashion"].applicable is True
    
    