"""Every MetaData a schema-wide invariant must sweep.

A second declarative base silently escapes every one of them. ``Base.metadata``
is hardcoded in ``test_no_float_money``, and would have been hardcoded in the
next such test too -- so the first personalization table carrying a ``Float``
would have passed a suite whose entire purpose is to make that impossible.

Collecting the bases here makes adding a third one line instead of a silent
hole. Imported by tests and by the erasure sweep; ``core`` importing ``core``
breaks no contract.
"""

from __future__ import annotations

from sqlalchemy import MetaData

from app.core.models import Base
from app.core.signals_base import SignalsBase

ALL_METADATA: tuple[MetaData, ...] = (Base.metadata, SignalsBase.metadata)
