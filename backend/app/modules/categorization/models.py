"""Categorisation feedback.

Every correction a user makes serves two purposes at once (FR-5.5):

* a **personal rule**, applied immediately so the same merchant is right next
  time -- the user should never have to correct the same thing twice;
* a **labelled example** for the next training run.

`predicted_confidence` and `categorizer_version` are recorded alongside so the
eval harness can distinguish "the model was wrong" from "the model was
correctly unsure". Those two demand opposite fixes: the first means more or
better features, the second means the threshold is doing its job.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import CONFIDENCE, Base, TenantMixin, TimestampMixin, UUIDMixin


class CategorizationFeedback(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "categorization_feedback"

    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "transactions.id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    #: The normalised form, not the raw narration -- the model is trained on
    #: normalised strings, so the rule has to key on the same thing.
    merchant_normalized: Mapped[str] = mapped_column(String(255), nullable=False)

    predicted_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "categories.id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    corrected_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    predicted_confidence: Mapped[Decimal | None] = mapped_column(CONFIDENCE)
    categorizer_version: Mapped[str | None] = mapped_column(String(48))

    __table_args__ = (
        # The rule lookup: most recent correction per merchant wins.
        Index(
            "ix_categorization_feedback_user_id_merchant",
            "user_id",
            "merchant_normalized",
            text("created_at DESC"),
        ),
        Index("ix_categorization_feedback_created_at", text("created_at DESC")),
    )
