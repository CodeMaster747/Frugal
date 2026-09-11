terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
      # Same constraint as the VM module next door, so both are exercised
      # against one provider version rather than drifting apart.
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
  # the CLI's active subscription.
  subscription_id = var.subscription_id != "" ? var.subscription_id : null

  # The storage account sets `shared_access_key_enabled = false`, so the provider
  # must reach the blob data plane with the CLI's Entra token, not an account key.
  # Without this line the account is *created*, and then the provider's own
  # post-create check polls the blob service with a key and gets
  # `403 KeyBasedAuthenticationNotPermitted` -- which is how it was found. The VM
  # module in ../terraform carries the same line for the same reason; it did not
  # survive being copied here.
  storage_use_azuread = true

  features {
    resource_group {
      # A destroy should take the group with it. This deployment is meant to be
      # disposable: that is most of the argument for it over a VM.
      prevent_deletion_if_contains_resources = false
    }
  }
}
