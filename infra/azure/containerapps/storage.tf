# Receipt images, on Blob Storage, reached with a managed identity.
#
# This is the one part of the deployment that costs real money, and it is
# roughly $0.02/GB/month on hot LRS -- cents at any volume this project will
# see, and the lifecycle rule below caps even that.
#
# It exists because `app/adapters/storage/azure_blob.py` was written for
# ADR-010 and has had nowhere to run since the VM was destroyed. The adapter
# authenticates with `DefaultAzureCredential` when no account key is configured,
# which here resolves to the user-assigned identity below, named to it by
# AZURE_CLIENT_ID. (System-assigned is what this first used; the environment
# is Express, which rejects it.) So the
# interesting property here is what is *absent*: no connection string, no
# account key, no secret to rotate, and nothing in the container's environment
# that would be worth stealing.

resource "random_string" "storage_suffix" {
  # Storage account names are globally unique, lowercase alphanumeric, 3-24
  # chars. A suffix is the difference between `terraform apply` working and
  # failing on somebody else having taken the name.
  length  = 8
  lower   = true
  upper   = false
  numeric = true
  special = false
}

resource "azurerm_storage_account" "receipts" {
  name                = "frugalca${random_string.storage_suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  account_tier             = "Standard"
  account_replication_type = "LRS"

  https_traffic_only_enabled = true
  min_tls_version            = "TLS1_2"

  # No anonymous read, ever. Receipts are the most sensitive thing this system
  # stores; they are served through time-limited user-delegation SAS URLs the
  # adapter mints, never by being public.
  allow_nested_items_to_be_public = false

  # The property that makes the managed identity load-bearing rather than
  # decorative: with keys disabled there is no other way in. A leaked
  # connection string cannot exist because a connection string cannot work.
  shared_access_key_enabled = false

  tags = local.tags
}

resource "azurerm_storage_container" "receipts" {
  name                  = "receipts"
  storage_account_id    = azurerm_storage_account.receipts.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "receipts" {
  storage_account_id = azurerm_storage_account.receipts.id

  rule {
    name    = "expire-receipts"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["receipts/"]
    }

    actions {
      base_blob {
        # A receipt image has served its purpose once the line items are
        # extracted -- everything downstream lives in Postgres. Keeping the
        # photograph indefinitely is storage cost and privacy exposure for no
        # product benefit.
        delete_after_days_since_creation_greater_than = var.receipt_expiry_days
      }
    }
  }
}

# --- the identity ------------------------------------------------------------

resource "azurerm_user_assigned_identity" "api" {
  # User-assigned because this environment is Express, and Express rejects
  # system-assigned identity. It is still single-purpose: one consumer, reached
  # by nothing but the two role assignments below.
  name                = "${var.prefix}-api-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

# --- who may read and write ---------------------------------------------------

resource "azurerm_role_assignment" "app_blob_data" {
  scope                = azurerm_storage_account.receipts.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
  # Stated explicitly: a brand-new identity may not have replicated through
  # Entra yet, and without the type ARM tries to resolve it and can fail with
  # PrincipalNotFound on the first apply.
  principal_type = "ServicePrincipal"
}

resource "azurerm_role_assignment" "app_blob_delegator" {
  scope = azurerm_storage_account.receipts.id
  # Separate from the data role and genuinely required: minting a user-delegation
  # SAS is a control-plane operation, so an identity that can read every blob
  # still cannot hand out a link to one without this. The symptom of omitting it
  # is uploads succeeding and downloads 403ing, which reads as a storage problem
  # and is a permissions one.
  role_definition_name = "Storage Blob Delegator"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
  # Stated explicitly: a brand-new identity may not have replicated through
  # Entra yet, and without the type ARM tries to resolve it and can fail with
  # PrincipalNotFound on the first apply.
  principal_type = "ServicePrincipal"
}
