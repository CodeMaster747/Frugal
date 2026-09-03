"""Cross-user receipt deduplication (ADR-013).

The attack this defends against is the oldest one available: upload the same
receipt from several accounts and collect the points several times. Per-user
deduplication does not see it, because each upload is that user's first.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.receipts.pipeline.fingerprint import (
    HAMMING_THRESHOLD,
    content_hash,
    hamming,
    looks_like_the_same_paper,
    perceptual_hash,
)

pytest.importorskip("cv2", reason="pHash needs OpenCV, which ships in the `ocr` extra")

import cv2
import numpy as np

DAY = date(2026, 3, 1)


def _png(array: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", array)
    assert ok
    return buffer.tobytes()


def _receipt(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (400, 300), dtype=np.uint8)


class TestContentHash:
    def test_line_order_does_not_matter(self):
        """OCR line order varies between reads of one piece of paper.

        An order-sensitive digest would hash the same receipt differently on
        every upload, which defeats the whole check.
        """
        args = dict(
            gstin="27AAPFU0939F1ZV",
            merchant_normalized="corner store",
            observed_on=DAY,
            total=Decimal("250.00"),
        )
        assert content_hash(**args, line_totals=[Decimal("100"), Decimal("150")]) == content_hash(
            **args, line_totals=[Decimal("150"), Decimal("100")]
        )

    def test_a_different_total_is_a_different_receipt(self):
        args = dict(
            gstin="27AAPFU0939F1ZV",
            merchant_normalized="corner store",
            observed_on=DAY,
            line_totals=[Decimal("100")],
        )
        assert content_hash(**args, total=Decimal("100")) != content_hash(
            **args, total=Decimal("101")
        )

    def test_the_gstin_wins_over_the_merchant_string(self):
        """A GSTIN identifies a shop; a merchant string describes one.

        Two OCR readings of the same shop's name -- "CORNER STORE" and "CORNFR
        ST0RE" -- must still hash alike when the registration matches.
        """
        args = dict(gstin="27AAPFU0939F1ZV", observed_on=DAY, total=Decimal("100"), line_totals=[])
        assert content_hash(**args, merchant_normalized="corner store") == content_hash(
            **args, merchant_normalized="cornfr st0re"
        )


class TestPerceptualHash:
    def test_a_rescan_of_one_receipt_matches(self):
        base = _receipt(7)
        rescan = np.roll(np.clip(base.astype(int) + 12, 0, 255).astype(np.uint8), 1, axis=0)

        assert hamming(perceptual_hash(_png(base)), perceptual_hash(_png(rescan))) <= (
            HAMMING_THRESHOLD
        )

    def test_two_different_receipts_do_not_match(self):
        assert (
            hamming(perceptual_hash(_png(_receipt(7))), perceptual_hash(_png(_receipt(99))))
            > HAMMING_THRESHOLD
        )

    def test_it_fits_a_postgres_bigint(self):
        """BIGINT is signed 64-bit; Postgres has no unsigned integers."""
        value = perceptual_hash(_png(_receipt(3)))
        assert -(2**63) <= value < 2**63

    def test_an_unreadable_image_is_not_a_hash(self):
        assert perceptual_hash(b"not an image at all") == 0

    def test_two_unreadable_images_are_not_duplicates_of_each_other(self):
        """Zero must mean "no signal", not "a hash that matches every other
        failure" -- otherwise every failed decode duplicates every other."""
        assert not looks_like_the_same_paper(
            phash_a=0,
            phash_b=0,
            date_a=DAY,
            date_b=DAY,
            total_a=Decimal("100"),
            total_b=Decimal("100"),
        )


class TestCorroboration:
    def test_all_three_signals_must_agree(self):
        """pHash alone false-positives on two different receipts from one shop:
        same letterhead, same layout, same binarisation. That is exactly the
        case a price graph sees most often."""
        one = perceptual_hash(_png(_receipt(7)))

        assert not looks_like_the_same_paper(
            phash_a=one,
            phash_b=one,
            date_a=DAY,
            date_b=date(2026, 3, 2),
            total_a=Decimal("100"),
            total_b=Decimal("100"),
        )
        assert not looks_like_the_same_paper(
            phash_a=one,
            phash_b=one,
            date_a=DAY,
            date_b=DAY,
            total_a=Decimal("100"),
            total_b=Decimal("200"),
        )
        assert looks_like_the_same_paper(
            phash_a=one,
            phash_b=one,
            date_a=DAY,
            date_b=DAY,
            total_a=Decimal("100"),
            total_b=Decimal("100"),
        )
