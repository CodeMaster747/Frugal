"""Schema-wide conventions, asserted against the database migrations produce.

These are the rules this codebase learned the expensive way and then wrote down
nowhere enforceable. Both were established by a migration and kept by nothing:

- **0015** indexed nineteen foreign-key child columns after an account delete
  ran past ten minutes and was cancelled. It dropped the same work to 32.9 s.
  Nothing stopped the twentieth FK from arriving unindexed.
- **0016** gave nineteen user-owned tables a cascading foreign key to `users`,
  after the discovery that account deletion had never deleted anything. That
  one *is* guarded, by `test_every_user_owned_table_cascades`.

M14 adds the missing half. These run against a live database rather than
against `Base.metadata`, on purpose: the question is what the migrations built,
not what the models declare, and the two drift.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


#: A single-column foreign key whose child column heads no index.
#:
#: `indkey[0]` rather than "appears anywhere in indkey": Postgres can only use a
#: composite index for this lookup when the column is its *leading* one, so an
#: index on (user_id, status) counts and one on (status, user_id) does not.
_UNINDEXED_FK_CHILDREN = text("""
    SELECT c.relname AS table_name,
           a.attname AS column_name,
           con.conname AS constraint_name
      FROM pg_constraint con
      JOIN pg_class c ON c.oid = con.conrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = con.conkey[1]
     WHERE con.contype = 'f'
       AND n.nspname = 'public'
       AND array_length(con.conkey, 1) = 1
       AND NOT EXISTS (
           SELECT 1 FROM pg_index i
            WHERE i.indrelid = c.oid
              AND i.indkey[0] = a.attnum
       )
     ORDER BY 1, 2
""")


#: Money and probabilities, in the database rather than in the models.
#:
#: `test_no_float_money` sweeps `Base.metadata`, which catches a `Float` column
#: someone *declares*. It cannot catch one a hand-written migration creates, and
#: every migration in this repo from 0017 on was hand-written.
_FLOAT_COLUMNS = text("""
    SELECT c.relname AS table_name, a.attname AS column_name, t.typname AS type_name
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_type t ON t.oid = a.atttypid
     WHERE c.relkind = 'r'
       AND n.nspname = 'public'
       AND a.attnum > 0
       AND NOT a.attisdropped
       AND t.typname IN ('float4', 'float8')
     ORDER BY 1, 2
""")


class TestSchemaConventions:
    async def test_every_foreign_key_child_column_is_indexed(self, db_session):
        """The convention migration 0015 established, made structural.

        Without an index on the child column, deleting a parent row makes
        Postgres sequentially scan the child table once per deleted row to
        enforce the constraint. That is the shape of the ten-minute delete: it
        is invisible at development volumes and it sits directly on the path of
        a user exercising their right to erasure.
        """
        rows = (await db_session.execute(_UNINDEXED_FK_CHILDREN)).all()

        assert not rows, (
            "Foreign keys with no index on the child column: "
            + ", ".join(f"{r.table_name}.{r.column_name} ({r.constraint_name})" for r in rows)
            + ". Add an index in the migration that creates the column -- see "
            "migration 0015 and the account-delete finding in docs/07-roadmap.md."
        )

    async def test_no_column_in_the_database_is_floating_point(self, db_session):
        """ADR-003, asserted against the schema instead of the models."""
        rows = (await db_session.execute(_FLOAT_COLUMNS)).all()

        assert not rows, (
            "Floating-point columns found: "
            + ", ".join(f"{r.table_name}.{r.column_name} ({r.type_name})" for r in rows)
            + ". Money is NUMERIC(18,2) and probabilities NUMERIC(4,3) -- see ADR-003."
        )
