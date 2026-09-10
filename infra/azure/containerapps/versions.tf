terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
      # Same constraint as the VM module next door, so both are exercised
      # against one provider version rather than drifting apart.
      version = "~> 4.9"
    }
  }
}

provider "azurerm" {
  # Required from azurerm 4.0 onward -- the provider no longer infers it from
  # the CLI's active subscription.
  subscription_id = var.subscription_id != "" ? var.subscription_id : null

  features {
    resource_group {
      # A destroy should take the group with it. This deployment is meant to be
      # disposable: that is most of the argument for it over a VM.
      prevent_deletion_if_contains_resources = false
    }
  }
}
