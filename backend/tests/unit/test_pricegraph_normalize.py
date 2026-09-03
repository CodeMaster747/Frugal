"""Item normalisation, exercised with no database (ADR-013).

`normalize.py` is pure, which is what makes this file worth writing: rules like
these are only trustworthy if you can run a few hundred real grocery strings
through them in a millisecond, and this is that.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.pricegraph.normalize import (
    BRAND_ALIASES,
    BRANDS,
    normalize_item,
    parse_pack,
    same_block,
)


class TestUnits:
    @pytest.mark.parametrize(
        ("text", "size", "unit"),
        [
            ("milk 500ml", Decimal(500), "ml"),
            ("milk 0.5l", Decimal(500), "ml"),
            ("milk 1 ltr", Decimal(1000), "ml"),
            ("rice 1kg", Decimal(1000), "g"),
            ("rice 500 gm", Decimal(500), "g"),
            ("eggs 12 pcs", Decimal(12), "pc"),
        ],
    )
    def test_everything_reduces_to_a_canonical_base(self, text, size, unit):
        """`0.5 L` and `500 ML` must be indistinguishable afterwards.

        This single rule collapses a large share of grocery duplicates, which
        is why it runs before anything more expensive.
        """
        parsed_size, parsed_unit, _ = parse_pack(text)
        assert parsed_size == size
        assert parsed_unit == unit

    def test_a_magnitude_never_renders_in_scientific_notation(self):
        """`Decimal.normalize()` turns 500 into `5E+2`, which then goes into the
        blocking key -- so `500ml` and `5E+2ml` would be two products."""
        size, _, _ = parse_pack("milk 500ml")
        assert "E" not in str(size)
        assert str(size) == "500"


class TestIdentity:
    def test_the_same_product_spelled_three_ways_is_one_key(self):
        keys = {
            normalize_item("Amul Taaza Milk 500 ml").key,
            normalize_item("AMUL TAAZA MILK 0.5L").key,
            normalize_item("amul  taaza   milk  500ML").key,
        }
        assert len(keys) == 1

    def test_pack_size_is_part_of_identity(self):
        """The rule that keeps the whole graph honest.

        "amul taaza 500ml" and "amul taaza 1l" score around 0.93 on trigram
        similarity and are a different product at a different price. If
        similarity were allowed to decide, every pack size of everything would
        merge.
        """
        half = normalize_item("AMUL TAAZA 500ML")
        full = normalize_item("AMUL TAAZA 1L")

        assert half.key != full.key
        assert not same_block(half, full)

    def test_a_brand_alias_never_swallows_a_product_variant(self):
        """Amul Taaza and Amul Gold are different milks at different prices.

        An earlier version mapped "amul taaza" -> "amul" as a tidy-up, which
        folded the variant into the brand and made them one product. A brand
        alias may only collapse spelling.
        """
        taaza = normalize_item("AMUL TAAZA MILK 500ML")
        gold = normalize_item("AMUL GOLD MILK 500ML")

        assert taaza.brand == gold.brand == "amul"
        assert taaza.key != gold.key
        assert "taaza" in taaza.key and "gold" in gold.key

    def test_brand_spelling_variants_do_collapse(self):
        assert normalize_item("PARLE-G BISCUIT 800G").brand == "parle-g"
        assert normalize_item("Parle G Biscuits 0.8 KG").brand == "parle-g"
        assert normalize_item("Coca Cola 750ml").brand == "coca-cola"

    def test_the_longest_brand_wins(self):
        """ "mother dairy" must not resolve to "dairy"."""
        assert normalize_item("MOTHER DAIRY CURD 400 G").brand == "mother-dairy"


class TestNoise:
    @pytest.mark.parametrize("junk", ["", "   ", "---", "1234567890", "****", "...", "=========="])
    def test_a_line_that_is_not_an_item_returns_none(self, junk):
        """Most receipts carry several such lines, and treating them as items
        would fill the graph with separators."""
        assert normalize_item(junk) is None

    def test_hsn_codes_and_barcodes_are_stripped(self):
        item = normalize_item("TATA SALT 1KG HSN: 25010010")
        assert "25010010" not in item.key
        assert item.key == normalize_item("TATA SALT 1KG").key

    def test_stopwords_do_not_change_identity(self):
        assert (
            normalize_item("FRESH AMUL TAAZA MILK 500ML MRP").key
            == normalize_item("AMUL TAAZA MILK 500ML").key
        )


class TestTheTables:
    def test_every_alias_resolves_to_a_known_brand(self):
        """An alias pointing at a brand that is not in BRANDS silently produces
        a brand nothing else will ever match."""
        for alias, canonical in BRAND_ALIASES.items():
            assert canonical in BRANDS, f"{alias} -> {canonical}, which is not a brand"
