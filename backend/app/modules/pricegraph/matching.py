"""Resolving a receipt line to a canonical item, and a merchant to a store.

Four layers, each running only when the one before it fails, in ascending order
of cost:

0. deterministic rules (`normalize.py`, pure, no database)
1. an exact alias hit -- one indexed lookup, and the common path after a term's
   first sighting
2. `pg_trgm` similarity *within a block* -- the extension has been installed
   since migration 0001
3. a human, for anything the machine should not decide alone

Layer 2 is where the rule that keeps the graph honest lives: blocking on
(brand, size, unit) happens **before** similarity is computed, never after.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pricegraph.models import (
    CanonicalItem,
    CanonicalItemAlias,
    ItemMergeCandidate,
    MergeStatus,
    Store,
)
from app.modules.pricegraph.normalize import NORMALIZER_VERSION, NormalizedItem

#: Similarity at or above which an alias is attached without asking anyone.
AUTO_ALIAS_SIMILARITY = Decimal("0.85")
#: Below this, not even worth proposing. Between the two, a human decides.
PROPOSE_SIMILARITY = Decimal("0.55")

#: A store name must be at least this similar, *and* share a PIN code.
STORE_NAME_SIMILARITY = Decimal("0.60")


@dataclass(frozen=True, slots=True)
class ItemMatch:
    item: CanonicalItem
    #: `alias` | `trigram` | `created`. Recorded so a bad rule is retractable
    #: by how the match was made rather than by guessing at dates.
    how: str
    similarity: Decimal | None = None


async def resolve_item(
    session: AsyncSession, normalized: NormalizedItem, *, allow_create: bool = True
) -> ItemMatch | None:
    """Find or create the canonical item for a normalised receipt line."""
    alias = (
        await session.execute(
            select(CanonicalItemAlias).where(CanonicalItemAlias.alias_key == normalized.key)
        )
    ).scalar_one_or_none()
    if alias is not None:
        item = await session.get(CanonicalItem, alias.canonical_item_id)
        if item is not None:
            return ItemMatch(item=item, how="alias")

    exact = (
        await session.execute(
            select(CanonicalItem).where(CanonicalItem.normalized_key == normalized.key)
        )
    ).scalar_one_or_none()
    if exact is not None:
        return ItemMatch(item=exact, how="alias")

    candidate = await _closest_in_block(session, normalized)
    if candidate is not None:
        item, similarity = candidate
        if similarity >= AUTO_ALIAS_SIMILARITY:
            # Auto-merge only ever attaches an *alias* to an existing canonical.
            # Canonical-to-canonical always waits for a human, because trigram
            # similarity is not transitive: chaining A~B and B~C produces groups
            # where A and C are unrelated.
            session.add(
                CanonicalItemAlias(
                    canonical_item_id=item.id,
                    alias_key=normalized.key,
                    source="trigram",
                )
            )
            await session.flush()
            return ItemMatch(item=item, how="trigram", similarity=similarity)

        if similarity >= PROPOSE_SIMILARITY and allow_create:
            created = await _create(session, normalized)
            await _propose_merge(session, created.id, item.id, similarity)
            return ItemMatch(item=created, how="created", similarity=similarity)

    if not allow_create:
        return None
    return ItemMatch(item=await _create(session, normalized), how="created")


async def _closest_in_block(
    session: AsyncSession, normalized: NormalizedItem
) -> tuple[CanonicalItem, Decimal] | None:
    """The most similar item *of the same brand and pack*.

    The block predicate comes first in the WHERE clause and is not negotiable.
    "amul taaza 500ml" and "amul taaza 1l" score around 0.93 and are a different
    product at a different price; similarity alone would merge every pack size
    of everything.

    `IS NOT DISTINCT FROM` rather than `=`, so a NULL brand blocks against other
    NULL brands instead of matching nothing.
    """
    similarity = func.similarity(CanonicalItem.normalized_key, normalized.key)
    stmt = (
        select(CanonicalItem, similarity)
        .where(
            CanonicalItem.brand.is_not_distinct_from(normalized.brand),
            CanonicalItem.pack_size.is_not_distinct_from(normalized.pack_size),
            CanonicalItem.pack_unit.is_not_distinct_from(normalized.pack_unit),
            CanonicalItem.normalized_key.op("%")(normalized.key),
        )
        .order_by(similarity.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    item, score = row
    return item, Decimal(str(score)).quantize(Decimal("0.001"))


async def _create(session: AsyncSession, normalized: NormalizedItem) -> CanonicalItem:
    item = CanonicalItem(
        canonical_name=normalized.display,
        normalized_key=normalized.key,
        brand=normalized.brand,
        pack_size=normalized.pack_size,
        pack_unit=normalized.pack_unit,
        normalizer_version=NORMALIZER_VERSION,
    )
    session.add(item)
    await session.flush()
    return item


async def _propose_merge(
    session: AsyncSession, left: uuid.UUID, right: uuid.UUID, similarity: Decimal
) -> None:
    """Queue a pair for a human, once, in a canonical order.

    The ordering is what the `one_row_per_unordered_pair` check enforces: without
    it the same two items queue twice, in both directions, and somebody answers
    the same question again.
    """
    low, high = (left, right) if str(left) < str(right) else (right, left)
    existing = (
        await session.execute(
            select(ItemMergeCandidate).where(
                ItemMergeCandidate.left_item_id == low,
                ItemMergeCandidate.right_item_id == high,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        ItemMergeCandidate(
            left_item_id=low,
            right_item_id=high,
            similarity=similarity,
            status=MergeStatus.PENDING.value,
        )
    )
    await session.flush()


async def resolve_store(
    session: AsyncSession,
    *,
    gstin: str | None,
    merchant_normalized: str | None,
    pincode: str | None,
) -> Store | None:
    """Find the shop a receipt came from.

    Strongest evidence first, and it is not close: a GSTIN is a government
    registration for one business at one state, while a merchant name is
    something thousands of shops share. Falling back to name similarity is only
    safe when a PIN code narrows it to a postal area -- without that, "Reliance
    Fresh" matches a branch two thousand kilometres away.
    """
    if gstin:
        found = (
            await session.execute(select(Store).where(Store.gstin == gstin))
        ).scalar_one_or_none()
        if found is not None:
            return found

    if not merchant_normalized or not pincode:
        return None

    similarity = func.similarity(Store.normalized_name, merchant_normalized)
    stmt = (
        select(Store, similarity)
        .where(
            Store.pincode == pincode,
            similarity >= float(STORE_NAME_SIMILARITY),
        )
        .order_by(similarity.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    return row[0] if row is not None else None
