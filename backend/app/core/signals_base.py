"""The declarative base for the personalization database (ADR-011).

Separate from ``app.core.models.Base`` by MetaData *identity*, not by module
location: ``Base.metadata`` and ``SignalsBase.metadata`` are two objects, and
``alembic upgrade head`` on either tree compares only against the one its
``env.py`` names. A model that inherits the wrong base is therefore invisible to
one tree and proposed-for-creation by the other -- which is why
``test_every_personalization_model_uses_the_signals_base`` exists.

Kept in ``core`` beside ``models.py`` for the same reason that file is there:
it is infrastructure every layer above may depend on, and nothing here imports
a module. The *engine* is a different matter -- see ``signals_database.py``,
which exactly one module is permitted to import.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 — SQLAlchemy's own name
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.models import MONEY, NAMING_CONVENTION


class SignalsBase(DeclarativeBase):
    """Metadata for the personalization database.

    Conventions are deliberately identical to ``Base``'s -- same naming
    convention, same type map, same NUMERIC money -- so that reading a model
    over here requires learning nothing new. Only the metadata differs, and
    only because it must.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        Decimal: MONEY,
        datetime: DateTime(timezone=True),
        uuid.UUID: PgUUID(as_uuid=True),
    }

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={getattr(self, 'id', None)}>"


class SubjectMixin:
    """The opaque handle to a person.

    Deliberately **not** named ``user_id``, and this is the single most
    important naming decision in this milestone. In this codebase ``user_id``
    means three things at once: a cascading foreign key to ``users``, a row in
    the account-deletion cascade, and a ``BaseRepository.scoped_select``
    predicate. None of the three is true over here.

    Borrowing the name would do two kinds of damage. It would make
    ``test_every_user_owned_table_cascades`` -- which asks the database which
    tables carry a ``user_id`` without a cascading foreign key -- describe a
    database it cannot see. And it would let a personalization model be picked
    up by the tenant-repository sweep and silently skipped, because the scoping
    mechanism it checks for does not exist here.

    ``subject_id`` says what it is: an opaque value, erased by the sweep in
    ``app/core/erasure.py``, never joined to anything.
    """

    subject_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
