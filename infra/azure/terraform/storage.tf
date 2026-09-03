# Receipt storage.
#
# The only Azure-hosted application state in the deployment. Postgres stays on
# Neon and Redis stays on the VM, so nothing here scales with usage except the
# images themselves -- and the management policy below bounds those.

data "azurerm_client_config" "current" {}

# Storage account names are globally unique across every Azure customer, 3-24
# characters, lowercase alphanumeric only -- no hyphens. `frugalreceipts` is
# long gone, so a suffix is not optional.
resource "random_string" "storage_suffix" {
  length  = 8
  lower   = true
  upper   = false
  numeric = true
  special = false

  # Regenerating this would orphan the old account and every receipt in it.
  lifecycle {
    ignore_changes = all
  }
}

resource "azurerm_storage_account" "receipts" {
  name                = "${var.prefix}${random_string.storage_suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags

  account_tier = "Standard"
  account_kind = "StorageV2"
  access_tier  = "Hot"

  # LRS: three copies inside one datacentre. GRS doubles the price to protect
  # against losing a whole region, and what it would protect is photographs
  # whose extracted contents are already in Postgres on a different provider.
  account_replication_type = "LRS"

  https_traffic_only_enabled = true
  min_tls_version            = "TLS1_2"

  # The equivalent of the S3 public access block. Receipts are photographs of
  # someone's shopping: they carry names, card fragments, and locations.
  # Nothing here is ever public, and the application reaches objects through
  # short-lived SAS URLs rather than by making the container readable.
  allow_nested_items_to_be_public = false
  public_network_access_enabled   = true

  # No shared account keys. This is the setting that makes the migration off
  # AWS a security improvement rather than a lateral move: with keys disabled,
  # the *only* way to reach this data is an Entra ID identity, so there is no
  # static credential that can be committed, logged, or pasted into a chat.
  #
  # The adapter signs its SAS URLs with a user delegation key, which is issued
  # over Entra ID and expires in 24 hours (see adapters/storage/azure_blob.py).
  #
  # Cost of this choice: `az storage` and `azcopy` need `--auth-mode login`
  # rather than a key. The runbook and migrate-receipts.sh already do.
  shared_access_key_enabled = false

  # Encryption at rest is on by default with Microsoft-managed keys and cannot
  # be turned off, so there is no equivalent of the explicit SSE block the S3
  # module needed. Customer-managed keys would add a Key Vault to run and pay
  # for, and would not change the threat that matters here (a container left
  # readable, which the setting above prevents).

  blob_properties {
    # Soft delete is deliberately NOT enabled -- neither for blobs nor for
    # containers. It would retain every deleted object for the retention
    # window, which turns the expiry rule below into a no-op and makes storage
    # grow without bound. Receipts are immutable once uploaded, so there is no
    # version history worth keeping.
    #
    # Stated as an empty block rather than omitted because new storage accounts
    # created through the portal get 7-day blob soft delete by default, and
    # "we did not configure it" is not the same as "it is off".
    versioning_enabled  = false
    change_feed_enabled = false

    cors_rule {
      # The browser PUTs receipt bytes straight here, so this origin list is
      # load-bearing: without it the upload fails at the preflight with a CORS
      # error that looks like a broken URL. `x-ms-blob-type` must be allowed
      # explicitly -- it is the header the Azure adapter requires and is not
      # on the safelist.
      allowed_origins    = var.cors_allowed_origins
      allowed_methods    = ["GET", "PUT", "HEAD"]
      allowed_headers    = ["content-type", "x-ms-blob-type"]
      exposed_headers    = ["etag"]
      max_age_in_seconds = 3600
    }
  }
}

resource "azurerm_storage_container" "receipts" {
  name               = "receipts"
  storage_account_id = azurerm_storage_account.receipts.id

  # `private` means no anonymous read of blobs *or* of the container listing.
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "receipts" {
  storage_account_id = azurerm_storage_account.receipts.id

  rule {
    name    = "expire-processed-receipts"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["${azurerm_storage_container.receipts.name}/receipts/"]
    }

    actions {
      base_blob {
        # The image is input to OCR, not the record. Extracted fields and the
        # transaction live in Postgres and are unaffected; what expires is the
        # photograph, once nobody is plausibly still reviewing it.
        delete_after_days_since_creation_greater_than = var.receipt_expiry_days
      }

      # No equivalent of S3's abort_incomplete_multipart_upload rule is needed:
      # Azure garbage-collects uncommitted blocks after seven days on its own,
      # so the failure mode that bills invisibly on S3 does not exist here.
    }
  }
}

# --- identity ---------------------------------------------------------------

resource "azurerm_role_assignment" "vm_blob_data" {
  scope                = azurerm_storage_account.receipts.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_linux_virtual_machine.app.identity[0].principal_id

  # Scoped to this one storage account, not the resource group and not the
  # subscription. A compromised VM should not be able to reach anything else.
}

resource "azurerm_role_assignment" "vm_blob_delegator" {
  scope                = azurerm_storage_account.receipts.id
  role_definition_name = "Storage Blob Delegator"
  principal_id         = azurerm_linux_virtual_machine.app.identity[0].principal_id

  # Redundant today: Storage Blob Data Contributor already carries
  # `generateUserDelegationKey`. Assigned explicitly anyway, because every SAS
  # this deployment issues depends on that one action, built-in role
  # definitions are Microsoft's to narrow, and the failure if it were ever
  # removed is a 403 at presign time that reads like a broken adapter.
}

resource "azurerm_role_assignment" "operator_blob_data" {
  scope                = azurerm_storage_account.receipts.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id

  # Whoever ran `terraform apply`. Owner on the subscription is a *control
  # plane* role: it can delete this account but cannot read a blob inside it,
  # which surprises people the first time `az storage blob list` returns 403 on
  # a resource they own. This is what makes migrate-receipts.sh and backup.sh
  # work without re-enabling shared keys.
}
