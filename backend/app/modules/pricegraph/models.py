"""The shared price graph, and the private records that feed it.

Two kinds of table live here, and which is which is the whole privacy design:

**Global** -- `stores`, `canonical_items`, `price_observations`,
`receipt_fingerprints`. No `user_id`, by the same reasoning as `offer_snapshots`
and `products`: a price at a shop on a day is a fact about the shop, not about a
person.

**Tenant-scoped** -- `user_store_pins`, `receipt_promotions`. These carry
`TenantMixin` and cascade from `users`. `receipt_promotions` is the join that
`price_observations` deliberately does not carry: deleting an account destroys
the ability to attribute an observation to anybody, while the observation
survives as the shared fact it always was.

Note what is *absent* from `price_observations` and cannot be added: no
`receipt_id`, no line number, no timestamp, nothing that reconstructs a basket.
Item co-occurrence on one receipt is a fingerprint that re-identifies people
with no name attached, which is why the shared row records one item and not the
shopping trip it came from.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import (
    CONFIDENCE,
    CURRENCY,
    MONEY,
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class StoreSource(StrEnum):
    OSM_IMPORT = "osm_import"
    RECEIPT = "receipt"
    USER_PIN = "user_pin"


class ObservationSource(StrEnum):
    """Where a price came from, in descending order of how much it is trusted."""

    RECEIPT = "receipt"
    USER_REPORT = "user_report"
    SCRAPER = "scraper"
    PROVIDER = "provider"


class MergeStatus(StrEnum):
    PENDING = "pending"
    MERGED = "merged"
    REJECTED = "rejected"


class Store(UUIDMixin, TimestampMixin, Base):
    """A shop, somewhere.

    `latitude`/`longitude` are NUMERIC, not double precision. ADR-003's rule is
    about money, but a coordinate compared or deduplicated by equality has
    exactly the same problem -- and `DOUBLE_PRECISION` is a `Float` subclass, so
    `test_no_float_money` would have caught the mistake regardless.

    Radius search runs as a bounding-box predicate on the two indexed columns
    followed by Haversine in Python over the survivors. No PostGIS: the upgrade
    path if it is ever needed is `cube` + `earthdistance`, both contrib and both
    available on Neon, and neither is needed at this size.
    """

    __tablename__ = "stores"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Through `finance.normalize_merchant`, so a receipt's merchant string and
    #: a store name are normalised by one implementation.
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)

    #: The strongest identity there is: a government registration for a specific
    #: business at a specific state. Turns "AMUL PARLOUR" -- a name thousands of
    #: shops share -- into an entity key.
    gstin: Mapped[str | None] = mapped_column(String(15))
    #: Chain identity, so "Reliance Fresh" branches group without merging.
    brand_slug: Mapped[str | None] = mapped_column(String(80), index=True)

    address_line: Mapped[str | None] = mapped_column(String(255))
    pincode: Mapped[str | None] = mapped_column(String(6))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(80))

    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))

    source: Mapped[str] = mapped_column(String(16), nullable=False)
    osm_id: Mapped[str | None] = mapped_column(String(40))
    #: Set when a human has confirmed the location, not merely when it was
    #: derived. Only confirmed stores anchor a promoted price.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("gstin", name="uq_stores_gstin"),
        UniqueConstraint("osm_id", name="uq_stores_osm_id"),
        Index("ix_stores_pincode_normalized_name", "pincode", "normalized_name"),
        Index(
            "ix_stores_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
        Index("ix_stores_latitude_longitude", "latitude", "longitude"),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)", name="coordinates_come_in_pairs"
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="coordinates_are_on_earth",
        ),
    )


class CanonicalItem(UUIDMixin, TimestampMixin, Base):
    """One product, however many ways receipts spell it."""

    __tablename__ = "canonical_items"

    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(200), nullable=False)

    brand: Mapped[str | None] = mapped_column(String(120))
    pack_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    pack_unit: Mapped[str | None] = mapped_column(String(8))

    #: Assigned by the TF-IDF classifier once the item exists -- the one job it
    #: is the right tool for here.
    category_slug: Mapped[str | None] = mapped_column(String(80), index=True)

    #: True until enough independent sightings to show to other users. A first
    #: sighting is one person's OCR, and publishing it as a product would let a
    #: single mis-read create an entry everyone else then matches against.
    is_provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    observation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    normalizer_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    __table_args__ = (
        UniqueConstraint("normalized_key", name="uq_canonical_items_normalized_key"),
        Index(
            "ix_canonical_items_normalized_key_trgm",
            "normalized_key",
            postgresql_using="gin",
            postgresql_ops={"normalized_key": "gin_trgm_ops"},
        ),
        Index("ix_canonical_items_brand_pack_size_pack_unit", "brand", "pack_size", "pack_unit"),
    )


class CanonicalItemAlias(UUIDMixin, TimestampMixin, Base):
    """A spelling that resolves to a canonical item.

    After a term's first sighting this is the common path: one indexed lookup,
    no similarity computation.
    """

    __tablename__ = "canonical_item_aliases"

    canonical_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: How many independent confirmations -- deliberately not *which* users.
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (UniqueConstraint("alias_key", name="uq_canonical_item_aliases_alias_key"),)


class ItemMergeCandidate(UUIDMixin, TimestampMixin, Base):
    """Two canonical items that might be one, awaiting a human.

    Exists because **trigram similarity is not transitive**: auto-merging A~B
    and B~C can produce a group where A and C are unrelated. Auto-merge is
    therefore only ever allowed to attach an *alias* to an existing canonical;
    canonical-to-canonical always waits here.
    """

    __tablename__ = "item_merge_candidates"

    left_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    right_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    similarity: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=MergeStatus.PENDING.value
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("left_item_id", "right_item_id", name="uq_item_merge_candidates_pair"),
        # One row per unordered pair. Without this the same two items queue
        # twice, in both orders, and a human resolves the same question again.
        CheckConstraint("left_item_id < right_item_id", name="one_row_per_unordered_pair"),
    )


class PriceObservation(UUIDMixin, TimestampMixin, Base):
    """One price, at one shop, on one day.

    Deliberately **not** tenant-scoped, and this is the line the whole design
    turns on. Receipts are PII; a price is not. What crosses is a fact about a
    shop, so this table carries no `user_id`, no `receipt_id`, no line number,
    and nothing that reconstructs a basket.

    `contributor_hash` is `HMAC(pepper, user|store|date)`. Store and date are
    *inside* the HMAC, so the value is not linkable across shops or across days,
    and the pepper lives in Settings rather than the database, so a dump alone
    links nothing. Be exact about what that buys: this is **pseudonymisation,
    not anonymisation** -- an operator holding the pepper can test a specific
    guess. Calling it anonymous would be false. On erasure the hash is nulled,
    and *then* the row is genuinely anonymous.
    """

    __tablename__ = "price_observations"

    canonical_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )

    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False, server_default="INR")
    #: Snapshotted, so a later canonical merge stays auditable.
    pack_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    pack_unit: Mapped[str | None] = mapped_column(String(8))

    #: A DATE, never a timestamp. Time of day plus store is close to identifying.
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)

    #: NULL for machine sources, and for rows whose contributor has been erased.
    #: Postgres treats NULLs as distinct, so the constraint below does not stop
    #: many scraped observations of the same shelf.
    contributor_hash: Mapped[bytes | None] = mapped_column(LargeBinary(16))
    pepper_version: Mapped[int | None] = mapped_column(SmallInteger)

    #: Set by retraction, which is a different right from erasure: erasure
    #: anonymises, retraction removes.
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # One contribution per user per item per store per day, enforced by the
        # database rather than by a check somebody can forget.
        UniqueConstraint(
            "canonical_item_id",
            "store_id",
            "observed_on",
            "contributor_hash",
            name="uq_price_observations_one_per_contributor_per_day",
        ),
        Index(
            "ix_price_observations_item_store_date",
            "canonical_item_id",
            "store_id",
            text("observed_on DESC"),
            postgresql_where=text("retracted_at IS NULL"),
        ),
        Index("ix_price_observations_store_id_observed_on", "store_id", text("observed_on DESC")),
        CheckConstraint("unit_price > 0", name="unit_price_positive"),
    )


class ReceiptFingerprint(UUIDMixin, TimestampMixin, Base):
    """Has anyone, anywhere, already contributed this receipt?

    Answerable in one indexed lookup. The converse -- *who* -- is deliberately
    unanswerable from this table: the only link runs the other way, from a
    tenant-scoped `receipts.fingerprint_id` that the asking user cannot read.
    That asymmetry is the entire privacy property.
    """

    __tablename__ = "receipt_fingerprints"

    #: sha256(gstin|date|total|sorted line totals). This is what actually
    #: detects duplicates.
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    #: 64-bit DCT hash of the *preprocessed* image, stored signed because
    #: Postgres has no unsigned integers. Corroborating signal only: binarising
    #: is also what makes two different receipts from one shop look alike.
    phash: Mapped[int] = mapped_column(BigInteger, nullable=False)

    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    first_seen_on: Mapped[date] = mapped_column(Date, nullable=False)
    sighting_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_receipt_fingerprints_content_hash"),
        Index("ix_receipt_fingerprints_phash_observed_on", "phash", "observed_on"),
        Index("ix_receipt_fingerprints_observed_on_total", "observed_on", "total"),
    )


class UserStorePin(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A shop a user placed on the map themselves.

    The fallback when a receipt names a shop the OSM import has never heard of,
    which for small local stores is most of them.
    """

    __tablename__ = "user_store_pins"

    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    pincode: Mapped[str | None] = mapped_column(String(6))
    note: Mapped[str | None] = mapped_column(String(400))

    __table_args__ = (
        CheckConstraint(
            "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180",
            name="pin_is_on_earth",
        ),
    )


class StoreConfirmation(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """One person saying a pinned shop is really there.

    `Store.confirmed_at` was declared with the comment "only confirmed stores
    anchor a promoted price" and then written by nothing, so every user-pinned
    shop wore the "not yet confirmed" banner permanently and
    `PointsReason.STORE_PIN_CONFIRMED` was unreachable. This is the missing
    half.

    Confirmation deliberately does **not** gate promotion. Requiring it would
    make user pins useless for prices, and user pins exist precisely because the
    small shops that print nothing scannable are also the ones no import knows
    about. What it gates is the banner, and the award to whoever placed the pin.

    `UNIQUE (user_id, store_id)` for the same reason every other vote in this
    system has one: a count is not a control, and a service-level check is
    bypassed by the next call site somebody writes.
    """

    __tablename__ = "store_confirmations"

    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint("user_id", "store_id", name="uq_store_confirmations_user_id_store_id"),
    )


class ReceiptPromotion(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """The uploader's private record of what they contributed.

    This is the join `price_observations` deliberately does not carry. It is
    tenant-scoped and cascades from `users`, so deleting an account destroys the
    ability to attribute an observation to anybody -- while the observation
    itself, a price at a shop on a day, survives as the shared fact it always
    was.
    """

    __tablename__ = "receipt_promotions"

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    receipt_line_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("receipt_line_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    price_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("price_observations.id", ondelete="SET NULL"), index=True
    )
    points_awarded: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint("receipt_line_item_id", name="uq_receipt_promotions_receipt_line_item_id"),
    )
