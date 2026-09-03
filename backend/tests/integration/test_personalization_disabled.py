"""What a deployment without a second database looks like (ADR-011).

This is the *default* configuration -- `.env.example` ships
`SIGNALS_DATABASE_URL` unset -- and until this file existed nothing exercised
it. `conftest` assigns the variable for every other test, so the suite only
ever saw the feature switched on.

Run in a subprocess because the router is composed at import time: `app/api/v1`
reads `get_settings().personalization_enabled` when the module loads, so the
question "is the route registered" cannot be re-asked inside a process that has
already answered it. A subprocess with a different environment is the only
honest way to ask.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.integration


def _run(script: str) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        # Both, or pydantic-settings picks the survivor back up.
        if k not in {"SIGNALS_DATABASE_URL", "SIGNALS_DATABASE_DIRECT_URL"}
    }
    return subprocess.run(  # noqa: S603 — a literal script in this file, not input
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


class TestWithoutTheSecondDatabase:
    def test_the_app_starts(self):
        result = _run("""
            from app.core.config import get_settings
            from app.main import create_app

            settings = get_settings()
            assert settings.personalization_enabled is False
            create_app(settings)
            print("OK")
        """)
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_the_routes_do_not_exist(self):
        """404, not 500.

        A registered route over a missing database says "this feature is
        broken". An absent one says "this deployment does not have that
        feature". Only the second is true, and the difference is what a user
        reads in a support thread.
        """
        result = _run("""
            from app.core.config import get_settings
            from app.main import create_app

            app = create_app(get_settings())
            # `getattr`: Starlette's route list holds Mounts and included
            # routers as well as endpoints, and only endpoints carry `.path`.
            paths = {getattr(r, "path", "") for r in app.routes}
            paths |= set(app.openapi()["paths"])
            leaked = {p for p in paths if "personalization" in p}
            assert not leaked, f"personalization routes registered anyway: {leaked}"
            print("OK")
        """)
        assert result.returncode == 0, result.stderr

    def test_readiness_reports_absent_rather_than_degraded(self):
        result = _run("""
            import asyncio
            from app.modules.personalization.service import health_check

            assert asyncio.run(health_check()) is None
            print("OK")
        """)
        assert result.returncode == 0, result.stderr

    def test_the_worker_tasks_report_disabled(self):
        """Not an error, and not a crash loop every hour."""
        result = _run("""
            from app.workers.tasks.personalization import refresh_profiles, run_erasure

            assert run_erasure() == {"status": "disabled"}
            assert refresh_profiles() == {"status": "disabled"}
            print("OK")
        """)
        assert result.returncode == 0, result.stderr

    def test_deleting_an_account_records_no_debt_nobody_can_pay(self, tmp_path):
        """A debt against a database that does not exist would sit pending
        forever and hold the staleness alarm on, for data that never existed."""
        result = _run("""
            import inspect
            from app.modules.auth import service

            source = inspect.getsource(service.AuthService.delete_account)
            assert "personalization_enabled" in source, (
                "delete_account must not record an erasure debt on a deployment "
                "that has no personalization database"
            )
            print("OK")
        """)
        assert result.returncode == 0, result.stderr

    def test_the_migration_runner_skips_and_says_so(self):
        result = _run("""
            from scripts.migrate_signals import main

            assert main() == 0
            print("OK")
        """)
        assert result.returncode == 0, result.stderr
        assert "skipping" in result.stdout.lower()
