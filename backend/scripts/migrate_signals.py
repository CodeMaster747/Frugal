"""Apply the personalization migrations, or say why not.

`alembic_signals/env.py` raises when `SIGNALS_DATABASE_URL` is unset, on
purpose: a migration runner that silently does nothing is how a schema drifts.
But `make migrate` and `deploy.sh` must still work on a deployment that has
personalization turned off. This is where the skip lives, so it can be
explicit.

Usage: python -m scripts.migrate_signals
"""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    from app.core.config import get_settings

    if not get_settings().personalization_enabled:
        print(
            "SIGNALS_DATABASE_URL is unset: personalization is disabled for this "
            "deployment, so there is nothing to migrate. Skipping."
        )
        return 0

    from alembic.command import upgrade
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic_signals.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic_signals"))
    upgrade(config, "head")
    print("personalization database is at head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
