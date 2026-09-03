"""S3-compatible object storage.

One adapter serves AWS S3, Cloudflare R2, Backblaze B2, and MinIO -- the
protocol is the same and only the endpoint differs. That is what makes the
deployment target a config choice rather than a rewrite (see
infra/aws/COST-SAFETY.md).

Uses aioboto3 so presigning and reads never block the event loop.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class S3ObjectStore:
    """Presigned-URL storage for receipts and exports."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._endpoint = settings.s3_endpoint_url
        self._region = settings.s3_region
        self._session = aioboto3.Session(
            aws_access_key_id=(
                settings.s3_access_key.get_secret_value() if settings.s3_access_key else None
            ),
            aws_secret_access_key=(
                settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None
            ),
            region_name=settings.s3_region,
        )
        # SigV4 explicitly: R2 and MinIO both require it, and the default can
        # fall back to SigV2 on a custom endpoint.
        self._config = Config(signature_version="s3v4", retries={"max_attempts": 3})

    @property
    def upload_headers(self) -> dict[str, str]:
        """None needed. S3 signs the content type into the URL itself."""
        return {}

    @asynccontextmanager
    async def _client(self) -> Any:
        async with self._session.client(
            "s3", endpoint_url=self._endpoint, config=self._config
        ) as client:
            yield client

    async def presign_put(self, key: str, content_type: str, expires_in: int) -> str:
        """A URL the browser uploads to directly.

        The content type is part of the signature, so a client cannot present a
        URL issued for an image and use it to store something else.
        """
        async with self._client() as client:
            return str(
                await client.generate_presigned_url(
                    "put_object",
                    Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
                    ExpiresIn=expires_in,
                )
            )

    async def presign_get(self, key: str, expires_in: int, content_type: str | None = None) -> str:
        """Short-lived read URL.

        Generated per request and never stored: a persisted URL is a stored
        expiry bug, which is why the database holds the key alone.

        `ResponseContentType` overrides whatever type the object carries. The
        presigned PUT already pins the type at upload, so this is belt and
        braces here -- but it is the *only* such guarantee on Azure, and
        applying it on both keeps the two adapters behaviourally identical.
        """
        params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        if content_type:
            params["ResponseContentType"] = content_type
        async with self._client() as client:
            return str(
                await client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=expires_in,
                )
            )

    async def get_bytes(self, key: str) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            return bytes(await response["Body"].read())

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        async with self._client() as client:
            await client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

    async def delete(self, key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self._bucket, Key=key)

    async def exists(self, key: str) -> bool:
        async with self._client() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
            except ClientError:
                return False
            return True

    async def ensure_bucket(self) -> None:
        """Create the bucket if missing.

        Only useful against MinIO in local development; on a real provider the
        bucket is created once, deliberately, with its access policy.
        """
        async with self._client() as client:
            try:
                await client.head_bucket(Bucket=self._bucket)
            except ClientError:
                logger.info("creating bucket", extra={"bucket": self._bucket})
                await client.create_bucket(Bucket=self._bucket)
