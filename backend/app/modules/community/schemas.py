"""Wire contracts for community reports. Amounts are strings (ADR-003)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.community.models import ReportKind


class ReportCreate(BaseModel):
    store_id: uuid.UUID
    kind: ReportKind
    item_text: str = Field(min_length=2, max_length=200)
    price: str | None = None
    note: str | None = Field(default=None, max_length=1000)


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class VerificationIn(BaseModel):
    agrees: bool


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    body: str
    upvote_count: int
    created_at: datetime
    #: Whether the *requesting* user wrote it. Deliberately the only thing said
    #: about authorship: a report is world-readable, and the person who wrote it
    #: is not the subject of it.
    mine: bool = False
    voted: bool = False


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    store_id: uuid.UUID
    store_name: str | None = None
    kind: str
    item_text: str
    price: str | None = None
    currency: str = "INR"
    note: str | None = None
    status: str

    agree_count: int
    dispute_count: int
    upvote_count: int
    #: Whether this report has cleared the trust threshold and entered the
    #: shared price graph.
    promoted: bool = False

    created_at: datetime
    mine: bool = False
    voted: bool = False
    verified_by_me: bool | None = None
    comments: list[CommentOut] = Field(default_factory=list)


class TrustOut(BaseModel):
    """A user's own standing, with the reasons.

    ADR-002's shape. A score that gates whether somebody's contribution reaches
    other people has no business being unexplainable.
    """

    score: str
    can_promote: bool
    threshold: str
    factors: list[dict[str, str]] = Field(default_factory=list)
