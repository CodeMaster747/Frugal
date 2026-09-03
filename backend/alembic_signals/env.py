"""Alembic environment for the personalization database (ADR-011).

Differs from its sibling in exactly four ways: a different base, a different
models import, a different URL, and a different version table.

``version_table`` is insurance rather than necessity. Two databases could each
hold an ``alembic_version`` without conflict -- but if someone ever pointed both
URLs at one database despite the validator in Settings, the result would be two
histories writing to one row, which corrupts rather than fails. Two names make
that misconfiguration visible instead.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.core.signals_base import SignalsBase

# The same rule as alembic/env.py: a missing import here does not no-op, it
# makes autogenerate propose DROPPING the table. `alembic -c
# alembic_signals.ini check` in CI is what turns that into a red build.
from app.modules.personalization import models as _personalization_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()

if settings.signals_database_url is None:
    # Fails rather than no-ops. A migration runner that silently does nothing
    # is how a schema drifts, and the difference between "not configured" and
    # "already at head" is invisible from the outside. The skip belongs in
    # scripts/migrate_signals.py, where it can say so out loud.
    raise RuntimeError(
        "SIGNALS_DATABASE_URL is unset, so there is no personalization database "
        "to migrate. Run `python -m scripts.migrate_signals`, which skips "
        "cleanly and says so, or set the variable."
    )

config.set_main_option(
    "sqlalchemy.url",
    settings.signals_alembic_url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    ),
)

target_metadata = SignalsBase.metadata

#: Deliberately not `alembic_version`. See the module docstring.
VERSION_TABLE = "alembic_version_signals"


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
