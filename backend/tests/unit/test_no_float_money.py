"""No monetary column may be a floating-point type (ADR-003).

The cheapest possible guard against the highest-cost class of bug in a financial
product. It sweeps every mapped table, so it covers models that do not exist
yet -- which is the point of writing it in M0.
"""

from __future__ import annotations

from sqlalchemy import Float

from app.core.metadata import ALL_METADATA

# Importing every module's models registers them on a metadata. `conftest`
# imports `app.main`, which reaches all of them.
#
# The sweep iterates `ALL_METADATA` rather than `Base.metadata`, and that is
# not defensive tidiness: ADR-011 added a *second* declarative base, and a
# second base escapes every schema-wide invariant in this suite silently. The
# first personalization table carrying a Float would have passed the one test
# whose entire purpose is to make that impossible.


def test_no_table_declares_a_float_column():
    offenders = [
        f"{table.name}.{column.name}"
        for metadata in ALL_METADATA
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Float)
    ]
    assert not offenders, (
        f"Floating-point columns found: {offenders}. Money must be NUMERIC(18,2) "
        "and probabilities NUMERIC(4,3) -- see ADR-003."
    )


def test_the_sweep_covers_every_declarative_base():
    """`ALL_METADATA` is the thing that keeps this test honest, so check it.

    A third base added without updating `app/core/metadata.py` would leave this
    file passing while checking two-thirds of the schema.
    """
    from sqlalchemy.orm import DeclarativeBase

    import app.core.models
    import app.core.signals_base  # noqa: F401 — registers SignalsBase

    declared = {
        cls.metadata for cls in DeclarativeBase.__subclasses__() if hasattr(cls, "metadata")
    }
    missing = declared - set(ALL_METADATA)
    assert not missing, (
        "A declarative base exists that ALL_METADATA does not name, so every "
        "schema-wide invariant in this suite silently skips its tables. Add it "
        "to app/core/metadata.py."
    )
