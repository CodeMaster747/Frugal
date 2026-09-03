"""The worker's half of the composition root.

Deliberately duplicated from `app.main` rather than shared with it: the worker
is a separate entry point that must not import the FastAPI application, and the
`layered-architecture` contract enforces that -- api and workers are siblings
and neither may import the other.

Lifted out of `tasks/receipts.py` when a second task needed a `ReceiptService`.
Two copies inside one process is one too many: they would drift, and the one
that drifts is the one nobody is looking at.
"""

from __future__ import annotations

from typing import Any


def build_object_store(settings: Any) -> Any:
    """Choose the storage adapter, exactly as `app.main` does."""
    if settings.storage_backend == "memory":
        from app.adapters.storage.memory import InMemoryObjectStore

        return InMemoryObjectStore()

    if settings.storage_backend == "azure_blob":
        from app.adapters.storage.azure_blob import AzureBlobObjectStore

        return AzureBlobObjectStore(settings)

    from app.adapters.storage.s3 import S3ObjectStore

    return S3ObjectStore(settings)
