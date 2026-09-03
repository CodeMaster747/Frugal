"""Wire contracts for the price graph. Amounts are strings (ADR-003)."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    address_line: str | None = None
    pincode: str | None = None
    city: str | None = None
    latitude: str | None = None
    longitude: str | None = None
    source: str
    confirmed: bool = False


class ItemPriceOut(BaseModel):
    """A store-level price, aggregated over contributors.

    `contributors` is published rather than hidden: it is the difference
    between "one person said so" and "eleven people agree", and a user
    deciding where to shop should see which they are looking at.
    """

    canonical_item_id: uuid.UUID
    item_name: str
    store: StoreOut
    median_price: str
    min_price: str
    max_price: str
    contributors: int
    last_seen_on: date
    currency: str = "INR"


class CheaperElsewhereOut(BaseModel):
    item_name: str
    your_price: str
    best_price: str
    saving: str
    saving_percent: str
    store: StoreOut
    contributors: int
    last_seen_on: date
    caveats: list[str] = Field(default_factory=list)


class MapPinOut(BaseModel):
    """One pin on the landing map."""

    store: StoreOut
    #: How many item prices this store has that clear the k-anonymity floor.
    price_count: int
    #: How many community reports are attached to it.
    report_count: int
    #: The best headline this store has, if any.
    headline: str | None = None


class StorePinIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    latitude: str
    longitude: str
    pincode: str | None = Field(default=None, max_length=6)
    note: str | None = Field(default=None, max_length=400)


class MergeCandidateOut(BaseModel):
    id: uuid.UUID
    left_name: str
    right_name: str
    similarity: str


class ContributionOut(BaseModel):
    """One thing a user contributed, and what it earned.

    This is the tenant-scoped view. The shared observation it produced carries
    none of it.
    """

    id: uuid.UUID
    receipt_id: uuid.UUID
    item_name: str
    store_name: str
    unit_price: str
    observed_on: date
    points_awarded: int
    retracted: bool


class PromotionResult(BaseModel):
    promoted: int
    skipped: int
    reason: str | None = None
