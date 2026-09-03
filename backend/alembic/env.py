"""Alembic environment.

Runs synchronously against the *direct* database endpoint. On Neon, DDL through
the connection pooler is unreliable, so ``Settings.alembic_url`` prefers
``database_direct_url`` and falls back to the pooled URL locally where they are
the same host.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.models import Base

# Importing every module's models is what makes autogenerate see them. Each
# milestone adds its import here.
#
# A missing line here is not a no-op: `target_metadata` then lacks the table,
# and the next `--autogenerate` proposes DROPPING it. M12's and M13's tables
# sat in exactly that state until M14 -- `alembic check` in CI is what makes
# a forgotten import a build failure instead of a migration that deletes data.
from app.core import audit as _audit_models  # noqa: F401
from app.modules.auth import models as _auth_models  # noqa: F401
from app.core import jobs as _job_models  # noqa: F401
from app.modules.advisor import models as _advisor_models  # noqa: F401
from app.modules.categorization import models as _categorization_models  # noqa: F401
from app.modules.finance import models as _finance_models  # noqa: F401
from app.modules.forecasting import models as _forecasting_models  # noqa: F401
from app.modules.health import models as _health_models  # noqa: F401
from app.modules.market import models as _market_models  # noqa: F401
from app.modules.notifications import models as _notification_models  # noqa: F401
from app.modules.insights import models as _insights_models  # noqa: F401
from app.modules.receipts import models as _receipt_models  # noqa: F401
from app.modules.sms import models as _sms_models  # noqa: F401
from app.core import quota as _quota_models  # noqa: F401
from app.core import erasure as _erasure_models  # noqa: F401
from app.modules.pricegraph import models as _pricegraph_models  # noqa: F401
from app.modules.points import models as _points_models  # noqa: F401
from app.modules.community import models as _community_models  # noqa: F401
from app.core import host_budget as _host_budget_models  # noqa: F401
from app.adapters.offers.scraper import robots as _robots_models  # noqa: F401
from app.modules.market import scrape_cache as _scrape_cache_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
# psycopg (sync) for migrations -- Alembic's runner is synchronous.
config.set_main_option(
    "sqlalchemy.url",
    settings.alembic_url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    ),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (`alembic upgrade --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
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
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
