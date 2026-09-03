"""Azure Blob Storage.

The Azure half of the `ObjectStore` port (ADR-004). Written as a sibling of
`s3.py` rather than a replacement: one adapter covers AWS S3, R2, B2 and MinIO
because they share a protocol, and Azure Blob does not -- different auth,
different signing, different client requirements. A port exists precisely so
this is a new file rather than a branch inside the old one.

Three things differ from S3 in ways that reach the rest of the application, and
each is handled below rather than left for a caller to discover:

1. **Uploads need a header.** Azure requires `x-ms-blob-type: BlockBlob` on a
   PUT. The browser must send it, so it is published through `upload_headers`
   and travels with the ticket -- see `upload_headers` below.

2. **A SAS cannot pin the upload's content type.** S3 signs `ContentType` into
   a presigned PUT, so a URL issued for a JPEG cannot be used to store HTML.
   Azure's equivalent fields (`rsct` and friends) are *response* overrides and
   constrain reads, not writes. `presign_get` therefore forces the content type
   we recorded at ticket time, which closes the same hole from the other end:
   whatever bytes were actually stored, the browser is told they are the image
   type we accepted, so a smuggled HTML payload is never served as a document.

3. **Signing needs a key, not just a credential.** With a managed identity
   there is no account key to sign with, so Azure issues a short-lived *user
   delegation key* over Entra ID and the SAS is signed with that. It is the
   direct equivalent of the EC2 instance profile: nothing durable on the box,
   rotated by the platform, and nothing to leak into a repository.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Azure caps a user delegation key at seven days. Twenty-four hours is well
#: inside that and keeps the blast radius of a leaked key to a day.
_DELEGATION_KEY_TTL = timedelta(hours=24)

#: Renew this far before expiry. A key that lapses between signing and use
#: produces a SAS that looks valid and is rejected by the service -- a failure
#: that reads as a permissions problem and is not one.
_DELEGATION_KEY_MARGIN = timedelta(minutes=30)

#: Process-level cache of `(key, expires_at)`.
#:
#: Deliberately unguarded by a lock. The Celery worker calls this adapter
#: through `asyncio.run`, which builds and tears down an event loop per task,
#: and an `asyncio.Lock` created under one loop raises when awaited under the
#: next. The race this leaves is benign: two coroutines may each fetch a
#: delegation key, and the loser's is simply discarded.
_delegation_key: tuple[Any, datetime] | None = None


class AzureBlobObjectStore:
    """Presigned-URL storage for receipts and exports, on Azure Blob."""

    def __init__(self, settings: Settings) -> None:
        self._account = settings.azure_storage_account
        self._container = settings.azure_blob_container
        self._account_key = (
            settings.azure_storage_key.get_secret_value() if settings.azure_storage_key else None
        )
        self._account_url = (
            settings.azure_blob_endpoint or f"https://{self._account}.blob.core.windows.net"
        )

    @property
    def upload_headers(self) -> dict[str, str]:
        """Headers the browser must send with the presigned PUT.

        Azure rejects a PUT without this; S3 needs no equivalent and returns an
        empty mapping. Surfacing it through the port is what keeps the frontend
        free of a `if (isAzure)` branch -- it spreads whatever the ticket
        carries.
        """
        return {"x-ms-blob-type": "BlockBlob"}

    # --- clients ----------------------------------------------------------

    @asynccontextmanager
    async def _client(self) -> Any:
        """A client per operation, as in `s3.py`.

        Caching one on the instance would be faster and is wrong here: the
        underlying transport binds to the running event loop, and the worker
        creates a fresh loop for every task. A cached client survives exactly
        one task and then fails with a closed-transport error that points
        nowhere near this line.
        """
        credential: Any
        if self._account_key:
            credential = self._account_key
            async with BlobServiceClient(self._account_url, credential=credential) as client:
                yield client
            return

        # No key configured: authenticate as the VM's managed identity.
        # Imported here so a deployment using an account key -- local Azurite,
        # or the migration script -- does not need azure-identity present.
        from azure.identity.aio import DefaultAzureCredential

        async with (
            DefaultAzureCredential() as credential,
            BlobServiceClient(self._account_url, credential=credential) as client,
        ):
            yield client

    async def _signing_key(self, client: BlobServiceClient) -> Any:
        """The account key, or a cached user delegation key.

        Returns the account key verbatim when one is configured, because
        `generate_blob_sas` accepts either in the same position.
        """
        if self._account_key:
            return self._account_key

        global _delegation_key
        now = datetime.now(UTC)

        if _delegation_key is not None:
            key, expires_at = _delegation_key
            if expires_at - _DELEGATION_KEY_MARGIN > now:
                return key

        # `start` is backdated: Azure validates it against *its* clock, and a
        # host running even slightly fast issues a key that is not yet valid.
        key = await client.get_user_delegation_key(
            key_start_time=now - timedelta(minutes=5),
            key_expiry_time=now + _DELEGATION_KEY_TTL,
        )
        _delegation_key = (key, now + _DELEGATION_KEY_TTL)
        logger.info("issued user delegation key", extra={"expires_in_hours": 24})
        return key

    def _sas_url(self, key: str, token: str) -> str:
        return f"{self._account_url}/{self._container}/{key}?{token}"

    # --- the port ---------------------------------------------------------

    async def presign_put(self, key: str, content_type: str, expires_in: int) -> str:
        """A URL the browser uploads to directly.

        `content_type` is accepted for parity with the port and is *not*
        enforceable in an Azure SAS -- see the module docstring. The guarantee
        it provides on S3 is reconstructed in `presign_get`.
        """
        async with self._client() as client:
            signing_key = await self._signing_key(client)
            token = generate_blob_sas(
                account_name=self._account,
                container_name=self._container,
                blob_name=key,
                # `create` alone is what a fresh upload needs. Granting `write`
                # as well would let a holder of one URL append to or overwrite
                # an existing blob, which an upload ticket has no reason to do.
                permission=BlobSasPermissions(create=True),
                expiry=datetime.now(UTC) + timedelta(seconds=expires_in),
                **self._sas_credential(signing_key),
            )
            return self._sas_url(key, token)

    async def presign_get(self, key: str, expires_in: int, content_type: str | None = None) -> str:
        """Short-lived read URL, generated per request and never stored.

        When the caller knows what it accepted, that type is stamped onto the
        response. Without it a blob whose stored type says `text/html` is
        served as a document from a `*.blob.core.windows.net` origin, and a
        payload smuggled past the upload check becomes stored XSS.
        """
        async with self._client() as client:
            signing_key = await self._signing_key(client)
            token = generate_blob_sas(
                account_name=self._account,
                container_name=self._container,
                blob_name=key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(UTC) + timedelta(seconds=expires_in),
                content_type=content_type,
                **self._sas_credential(signing_key),
            )
            return self._sas_url(key, token)

    async def get_bytes(self, key: str) -> bytes:
        async with self._client() as client:
            blob = client.get_blob_client(self._container, key)
            stream = await blob.download_blob()
            return bytes(await stream.readall())

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        async with self._client() as client:
            blob = client.get_blob_client(self._container, key)
            await blob.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )

    async def delete(self, key: str) -> None:
        async with self._client() as client:
            blob = client.get_blob_client(self._container, key)
            # Deleting what is not there is the desired end state, and the
            # expiry policy on the container means the caller routinely cannot
            # know whether a blob still exists.
            with suppress(ResourceNotFoundError):
                await blob.delete_blob()

    async def exists(self, key: str) -> bool:
        async with self._client() as client:
            blob = client.get_blob_client(self._container, key)
            return bool(await blob.exists())

    async def ensure_container(self) -> None:
        """Create the container if missing.

        For Azurite in local development. On a real account the container is
        created once by Terraform, with its access level set to private.
        """
        async with self._client() as client:
            try:
                await client.create_container(self._container)
                logger.info("created container", extra={"container": self._container})
            except AzureError:
                pass

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _sas_credential(signing_key: Any) -> dict[str, Any]:
        """Route the signing material to the parameter that matches its kind.

        `generate_blob_sas` takes an account key as `account_key` and a
        delegation key as `user_delegation_key`, and silently produces an
        unusable token if given the wrong one.
        """
        if isinstance(signing_key, str):
            return {"account_key": signing_key}
        return {"user_delegation_key": signing_key}
