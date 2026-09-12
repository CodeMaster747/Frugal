"""The queue must accept a TLS-only Redis, and optional dispatches must stay optional.

Both halves regressed together once already: a `rediss://` URL passed every
readiness check and then failed `send_task`, and two call sites documented as
failure-tolerant turned that into a 500 that rolled back their transaction.
"""

from __future__ import annotations

import ssl
from typing import Any

import pytest

from app.core import queue


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("rediss://default:secret@example.upstash.io:6379", {"ssl_cert_reqs": ssl.CERT_REQUIRED}),
        ("rediss://default:secret@example.upstash.io:6379/0?ssl_cert_reqs=CERT_REQUIRED", None),
        ("redis://localhost:6379/0", None),
    ],
)
def test_tls_options_only_for_rediss_without_a_stated_policy(url: str, expected: Any) -> None:
    assert queue._redis_tls_options(url) == expected


def test_tls_options_never_disable_verification() -> None:
    options = queue._redis_tls_options("rediss://h:6379")
    assert options is not None
    assert options["ssl_cert_reqs"] is not ssl.CERT_NONE


def _raise(*_: Any, **__: Any) -> Any:
    raise ValueError("broker unavailable")


def test_best_effort_dispatch_absorbs_a_queue_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue.celery_app, "send_task", _raise)
    assert queue.dispatch_best_effort("app.workers.tasks.probe") is None


def test_best_effort_dispatch_returns_the_id_when_it_works(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        id = "task-123"

    monkeypatch.setattr(queue.celery_app, "send_task", lambda *_, **__: _Result())
    assert queue.dispatch_best_effort("app.workers.tasks.probe") == "task-123"


def test_strict_dispatch_still_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue.celery_app, "send_task", _raise)
    with pytest.raises(ValueError, match="broker unavailable"):
        queue.dispatch("app.workers.tasks.probe")
