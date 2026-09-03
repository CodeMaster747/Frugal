"""Every `ObjectStore` implementation, checked against the port.

The point of a port is that swapping the adapter is a config change (ADR-004).
That only holds if the implementations actually agree, and the two ways they
drifted during the Azure migration are both covered here: a method added to the
protocol that one adapter never grew, and `upload_headers`, which is the first
thing the port exposes that the *frontend* depends on.
"""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import pytest

from app.adapters.ports import ObjectStore
from app.adapters.storage.memory import InMemoryObjectStore
from app.adapters.storage.s3 import S3ObjectStore
from app.core.config import get_settings

# The Azure SDK arrives with the `azure` extra, which only the production
# images install. Skipping is the honest outcome here rather than making every
# image carry it -- the conformance tests below still run for the two adapters
# that are always present.
azure_blob = pytest.importorskip(
    "app.adapters.storage.azure_blob", reason="azure extra not installed"
)

#: `generate_blob_sas` base64-decodes the account key before signing with it,
#: so this has to be real base64 rather than an arbitrary string.
FAKE_ACCOUNT_KEY = base64.b64encode(b"0" * 32).decode()


def _azure_settings():
    return get_settings().model_copy(
        update={
            "storage_backend": "azure_blob",
            "azure_storage_account": "frugaltest",
            "azure_blob_container": "receipts",
            "azure_storage_key": pytest.importorskip("pydantic").SecretStr(FAKE_ACCOUNT_KEY),
        }
    )


def _stores():
    return [
        InMemoryObjectStore(),
        S3ObjectStore(get_settings()),
        azure_blob.AzureBlobObjectStore(_azure_settings()),
    ]


def test_every_adapter_satisfies_the_port() -> None:
    for store in _stores():
        assert isinstance(store, ObjectStore), (
            f"{type(store).__name__} does not satisfy ObjectStore"
        )


def test_only_azure_requires_an_upload_header() -> None:
    """The header is the whole reason `upload_headers` is on the port.

    Azure rejects a PUT without `x-ms-blob-type`; S3 needs nothing. The
    frontend spreads whatever the ticket carries, so getting this wrong on
    either adapter breaks uploads on that backend alone -- which is exactly the
    failure a shared test suite should catch rather than production.
    """
    assert InMemoryObjectStore().upload_headers == {}
    assert S3ObjectStore(get_settings()).upload_headers == {}
    assert azure_blob.AzureBlobObjectStore(_azure_settings()).upload_headers == {
        "x-ms-blob-type": "BlockBlob"
    }


@pytest.mark.asyncio
async def test_azure_presign_put_grants_create_only() -> None:
    """`create`, not `write`.

    A `write` SAS would let a holder of one upload URL overwrite or append to
    an existing blob. An upload ticket is issued for a key that does not exist
    yet and has no reason to do either.
    """
    store = azure_blob.AzureBlobObjectStore(_azure_settings())
    url = await store.presign_put("receipts/abc/def", "image/jpeg", 300)

    parsed = urlparse(url)
    assert parsed.netloc == "frugaltest.blob.core.windows.net"
    assert parsed.path == "/receipts/receipts/abc/def"

    query = parse_qs(parsed.query)
    assert query["sp"] == ["c"], "expected create-only permission"
    assert "sig" in query


@pytest.mark.asyncio
async def test_azure_presign_get_pins_the_response_content_type() -> None:
    """The only defence against a smuggled payload being served as a document.

    Azure cannot constrain the content type at upload the way an S3 presigned
    PUT does, so the guarantee is reconstructed on read: whatever bytes are
    stored, the browser is told they are the type we accepted at ticket time.
    Losing `rsct` here silently reopens a stored-XSS hole on a
    `*.blob.core.windows.net` origin.
    """
    store = azure_blob.AzureBlobObjectStore(_azure_settings())
    url = await store.presign_get("receipts/abc/def", 300, content_type="image/png")

    query = parse_qs(urlparse(url).query)
    assert query["rsct"] == ["image/png"]
    assert query["sp"] == ["r"]


@pytest.mark.asyncio
async def test_azure_presign_get_without_a_type_omits_the_override() -> None:
    """Callers that do not know the type must not get an empty one stamped on."""
    store = azure_blob.AzureBlobObjectStore(_azure_settings())
    url = await store.presign_get("receipts/abc/def", 300)

    assert "rsct" not in parse_qs(urlparse(url).query)
