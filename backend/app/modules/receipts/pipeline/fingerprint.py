"""Has this receipt already been contributed, by anyone?

Points on crowdsourced data invite farming, and the cheapest attack is the
oldest: upload the same receipt from several accounts, or the same receipt
repeatedly. Per-user deduplication -- which the ledger already does by content
hash -- does not see it, because each upload is a different user's first.

**Two hashes, because one is not enough.**

- `content_hash` does the detecting: a digest of the facts on the paper. Two
  photographs of one receipt produce the same value however differently they
  were taken.
- `phash` corroborates: a 64-bit DCT hash of the *preprocessed* image, which is
  what makes it robust to angle and lighting. Its own risk is worth naming --
  binarisation is also what makes two *different* receipts from the same shop
  look alike, same header, same layout -- so it is never used alone.

**No new dependency.** `imagehash` would pull in PIL and scipy; the worker
already carries `opencv-python-headless` and `numpy` through the `ocr` extra,
and pHash is a DCT over a 32x32 grayscale resize. That is what keeps the 1 GB
promise in ADR-010.

**Nothing is stored for longer.** Both hashes are computed in the pass that
already holds the image bytes for OCR, and the bytes are discarded exactly as
they are today.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

#: Bits that may differ and still be the same paper. Six of sixty-four is
#: tolerant of rescanning and intolerant of a different receipt: at this
#: threshold a random pair collides with probability around 1 in 10^13, and a
#: reprint of one receipt typically differs by two or three.
HAMMING_THRESHOLD = 6

#: The DCT is taken over this square, and the top-left 8x8 block of low
#: frequencies becomes the hash. Standard pHash sizing.
_RESIZE = 32
_LOW_FREQ = 8


def content_hash(
    *,
    gstin: str | None,
    merchant_normalized: str | None,
    observed_on: date,
    total: Decimal,
    line_totals: list[Decimal],
) -> bytes:
    """A digest of what the receipt says, independent of how it was photographed.

    GSTIN first when present, because it identifies the shop exactly where a
    merchant string only describes it. The line totals are sorted: OCR line
    order varies between reads of the same paper, and an order-sensitive digest
    would make one receipt hash differently on every upload.
    """
    identity = gstin or merchant_normalized or ""
    parts = [
        identity.strip().lower(),
        observed_on.isoformat(),
        format(total, "f"),
        "|".join(format(t, "f") for t in sorted(line_totals)),
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).digest()


def perceptual_hash(image_bytes: bytes) -> int:
    """A 64-bit DCT hash, returned signed so it fits a Postgres BIGINT.

    Returns 0 when the image cannot be read. Zero is treated as "no perceptual
    signal" by `looks_like_the_same_paper`, not as a hash that matches every
    other unreadable image -- which it otherwise would, and that would make
    every failed decode a duplicate of every other.
    """
    import cv2
    import numpy as np

    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return 0

    resized = cv2.resize(image, (_RESIZE, _RESIZE), interpolation=cv2.INTER_AREA)
    # `astype`, not `np.float32(...)`: the latter is typed as returning a
    # scalar, and cv2.dct has no overload for one.
    frequencies = cv2.dct(resized.astype(np.float32))
    block = np.asarray(frequencies, dtype=np.float64)[:_LOW_FREQ, :_LOW_FREQ]
    flat = block.flatten()

    # The DC term carries overall brightness rather than structure, so it is
    # excluded from the median. Including it makes the hash track exposure.
    median = float(np.median(flat[1:]))

    bits = 0
    for index, value in enumerate(flat):
        if float(value) > median:
            bits |= 1 << index

    return _to_signed(bits)


def _to_signed(value: int) -> int:
    """Postgres has no unsigned integers; BIGINT is signed 64-bit."""
    return value - (1 << 64) if value >= (1 << 63) else value


def hamming(left: int, right: int) -> int:
    """Differing bits between two signed 64-bit hashes."""
    mask = (1 << 64) - 1
    return ((left & mask) ^ (right & mask)).bit_count()


def looks_like_the_same_paper(
    *,
    phash_a: int,
    phash_b: int,
    date_a: date,
    date_b: date,
    total_a: Decimal,
    total_b: Decimal,
) -> bool:
    """Whether the perceptual signal corroborates a duplicate.

    All three must agree. pHash alone false-positives on two different receipts
    from one shop -- same letterhead, same layout, same binarisation -- which is
    precisely the case a price graph sees most often, so the date and the total
    have to agree as well.
    """
    if phash_a == 0 or phash_b == 0:
        return False
    if date_a != date_b:
        return False
    if hamming(phash_a, phash_b) > HAMMING_THRESHOLD:
        return False

    if total_a <= 0 or total_b <= 0:
        return False
    difference = abs(total_a - total_b) / max(total_a, total_b)
    return difference <= Decimal("0.01")
