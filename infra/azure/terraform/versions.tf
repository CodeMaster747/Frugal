terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
      # >= 4.9 for `storage_account_id` on azurerm_storage_container. The
      # older `storage_account_name` form is deprecated and removed in 5.0,
      # and writing the deprecated form now buys a rewrite later.
      version = "~> 4.9"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  # Required from azurerm 4.0 onward -- the provider no longer infers it from
  # the CLI's active subscription. Left to the ARM_SUBSCRIPTION_ID environment
  # variable when the variable is empty, which is how CI would supply it.
  subscription_id = var.subscription_id != "" ? var.subscription_id : null

  # The receipts account has shared_access_key_enabled = false, so the provider
  # must reach the blob data plane with the CLI's Entra token, not an account key.
  storage_use_azuread = true

  features {
    resource_group {
      # Let `terraform destroy` remove the resource group even when Azure has
      # put things in it that Terraform does not track -- boot-diagnostics
      # artefacts and the extension's own state are the usual ones. The default
      # refuses, and the teardown then half-completes and leaves a VM running
      # on a subscription whose currency is a fixed credit balance.
      prevent_deletion_if_contains_resources = false
    }
  }
}

provider "random" {}
