"""In-memory object store.

The fake that makes the receipt pipeline testable with no network, no
credentials, and no running MinIO -- which is the entire point of the port
(ADR-004).

Presigned URLs are synthetic: they encode the key and an expiry so a test can
assert on their shape, but nothing serves them. A test that needs the bytes
calls `get_bytes` directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Stored:
    data: bytes
    content_type: str


@dataclass(slots=True)
class InMemoryObjectStore:
    objects: dict[str, _Stored] = field(default_factory=dict)
    base_url: str = "memory://frugal"

    async def presign_put(self, key: str, content_type: str, expires_in: int) -> str:
        return f"{self.base_url}/{key}?op=put&ct={content_type}&exp={int(time.time()) + expires_in}"

    async def presign_get(self, key: str, expires_in: int, content_type: str | None = None) -> str:
        url = f"{self.base_url}/{key}?op=get&exp={int(time.time()) + expires_in}"
        return f"{url}&ct={content_type}" if content_type else url

    @property
    def upload_headers(self) -> dict[str, str]:
        return {}

    async def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise KeyError(f"No object at {key!r}")
        return self.objects[key].data

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = _Stored(data=data, content_type=content_type)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects
