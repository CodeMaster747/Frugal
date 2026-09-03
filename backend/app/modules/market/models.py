"""The product catalogue, wishlist, and price alerts.

`products` and `price_points` were declared in M8 and never populated — the
catalogue lived in Python and the advisor read it directly. They moved here in
M9, which is the first milestone that actually queries them, and where price
*history* is the whole point. A Python tuple has no history.

`products` and `price_points` are **shared reference data**: two of the very few
tables without `user_id`. A MacBook's price is not a fact about one user, and
scoping it per account would duplicate the catalogue for no benefit.

The wishlist is where a user says "tell me when this gets cheaper", which is the
one thing that justifies polling prices at all.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import (
    CURRENCY,
    MONEY,
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ProductSource(StrEnum):
    SEED_CATALOG = "seed_catalog"
    MANUAL_ENTRY = "manual_entry"
    PROVIDER_API = "provider_api"


class FulfillmentType(StrEnum):
    PLATFORM = "platform"
    THIRD_PARTY = "third_party"
    BRAND_DIRECT = "brand_direct"


class Product(UUIDMixin, TimestampMixin, Base):
    """A thing someone might buy. Not tenant-scoped — see the module docstring."""

    __tablename__ = "products"

    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    specs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    image_url: Mapped[str | None] = mapped_column(String(1024))
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    #: The provider's own identifier, so re-importing the catalogue updates
    #: rather than duplicating.
    external_id: Mapped[str | None] = mapped_column(String(160))

    __table_args__ = (
        UniqueConstraint("external_id", name="uq_products_external_id"),
        Index("ix_products_category", "category"),
        Index("ix_products_name", "canonical_name"),
    )


class PricePoint(UUIDMixin, TimestampMixin, Base):
    """One observation of a price. Append-only time series.

    Price history, lowest-recorded-price, and drop detection (M9) are all
    queries over this table rather than three separate mechanisms.
    """

    __tablename__ = "price_points"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    seller_name: Mapped[str] = mapped_column(String(120), nullable=False)
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Observable reliability signals only — exactly the inputs the M9 Seller
    # Reliability Score is allowed to use, and deliberately nothing more.
    seller_rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    rating_count: Mapped[int | None] = mapped_column(Integer)
    return_window_days: Mapped[int | None] = mapped_column(SmallInteger)
    warranty_months: Mapped[int | None] = mapped_column(SmallInteger)
    fulfillment_type: Mapped[str | None] = mapped_column(String(20))

    provider: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "product_id", "seller_name", "observed_at", name="uq_price_points_snapshot"
        ),
        Index("ix_price_points_product_time", "product_id", text("observed_at DESC")),
    )


class WishlistItem(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "wishlist_items"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: The price when they added it. The reference point for "cheaper than when
    #: you started watching", which is the comparison a user actually has in
    #: mind — not the all-time low, which they never saw.
    price_when_added: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)

    #: Alert when the price falls this far below `price_when_added`. Null means
    #: use the account default.
    target_price: Mapped[Decimal | None] = mapped_column(MONEY)

    notes: Mapped[str | None] = mapped_column(String(500))
    #: Set once the user acts on it, so the feed can stop nagging.
    purchased_on: Mapped[date | None] = mapped_column(Date)

    #: When a drop alert last fired for this item. The cooling period keys on
    #: this, so a price oscillating around the threshold does not alert daily.
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_alerted_price: Mapped[Decimal | None] = mapped_column(MONEY)

    __table_args__ = (
        # One *live* entry per product per user. Adding a thing twice is a
        # mistake, not an intent to watch it twice — but a plain unique
        # constraint also blocks re-adding something previously removed, since
        # the soft-deleted row still occupies the pair. A partial index is the
        # only version that expresses the actual rule.
        Index(
            "uq_wishlist_user_product",
            "user_id",
            "product_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_wishlist_user_created", "user_id", text("created_at DESC")),
    )


class PriceAlert(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """A recorded price drop.

    Separate from `insights` on purpose. An insight is generated from the user's
    own ledger and expires with its period; a price alert is a fact about the
    market at a moment, and it stays useful as history — "it was ₹89,900 in
    August" is worth keeping long after the alert stops being news.
    """

    __tablename__ = "price_alerts"

    wishlist_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wishlist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    previous_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    new_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    #: Drop as a fraction of the previous price, precomputed for ranking.
    drop_fraction: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)

    seller_name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Whether this is the lowest price ever recorded for the product. The
    #: strongest reason to act, and cheap to compute at write time.
    is_lowest_recorded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_price_alerts_user_created", "user_id", text("created_at DESC")),)


class OfferSnapshot(UUIDMixin, TimestampMixin, Base):
    """Cached results of one offer search.

    Shared reference data, so no `user_id`: two users searching the same
    product get the same listings, and scoping the cache per user would
    multiply the quota cost by the number of people who asked.

    In Postgres rather than Redis, against this project's usual preference for
    keeping regenerable state out of the database. The exception is deliberate:
    what a cache miss costs here is *quota*, and quota is the one thing in this
    system that cannot be regenerated -- an evicted key means a paid call.

    Rows expire and are purged rather than accumulating. A cache with an
    `expires_at` is a cache; a table of retailer prices that grows forever is a
    republished catalogue, which is a different thing with different terms
    attached to it.
    """

    __tablename__ = "offer_snapshots"

    #: sha256 of the normalised query. Hashed rather than indexed on the text so
    #: the unique constraint is a fixed width regardless of query length.
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    query_text: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    offers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "query_hash", name="uq_offer_snapshots_provider_query"),
        Index("ix_offer_snapshots_expires_at", "expires_at"),
    )
