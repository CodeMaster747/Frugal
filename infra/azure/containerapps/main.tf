# The API on Azure Container Apps.
#
# This replaces the VM deployment in `../terraform`, and the reason is cost
# rather than taste. That module's floor is roughly $6.29/month even with the
# VM itself free, because a Standard static public IP is $3.65/month on its own
# and Basic was retired in September 2025. Container Apps includes ingress, TLS
# and a certificate in the platform, so the single most expensive line in the
# VM design simply does not exist here.
#
# What it costs: the Consumption plan bills vCPU-seconds and GiB-seconds, and
# grants 180,000 vCPU-s + 360,000 GiB-s + 2M requests per subscription per
# month for free. With `min_replicas = 0` a portfolio-traffic API sits inside
# that grant, so the expected bill is $0 and the ceiling is a couple of dollars.
#
# What it costs in exchange: a cold start. The first request after an idle
# period pays container startup. That is the honest trade for scale-to-zero, and
# it is the right trade for a portfolio deployment rather than a business.
#
# Deliberately absent:
#
#   * **No ACR.** Basic is $5/month, more than this whole deployment. The image
#     lives in GitHub Container Registry, public, so no pull credentials exist
#     to leak or rotate.
#   * **No Postgres or Redis.** Azure's managed versions are ~$12 and ~$16 a
#     month. Neon and Upstash free tiers hold both, which is what ADR-010
#     already specified -- the boundary it drew is the reason this migration is
#     a new compute host and nothing else.
#   * **No worker or beat, yet.** Celery needs a process that does not scale to
#     zero, so it belongs in a Container Apps Job on a cron schedule rather than
#     an always-on replica. Until that exists, receipt OCR and the periodic
#     sweeps do not run. Everything synchronous -- the map, auth, transactions,
#     the price graph -- does.

locals {
  tags = {
    project    = var.prefix
    managed_by = "terraform"
    replaces   = "frugal-rg (VM deployment, destroyed 2026-09-10)"
  }
}

resource "azurerm_resource_group" "main" {
  name     = "${var.prefix}-rg"
  location = var.location
  tags     = local.tags
}

# Required by the Container Apps environment: it has nowhere else to send
# stdout. The 5 GB/month free ingestion allowance covers this comfortably, and
# the daily cap makes that structural rather than hopeful -- an app in a crash
# loop can otherwise produce a surprising amount of log.
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.prefix}-logs"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 1
  tags                = local.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "${var.prefix}-env"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags

  # No `workload_profile` block, and that is load-bearing. Declaring one moves
  # the environment onto the Dedicated plan, which bills an "Environment
  # Management Hour" at $0.14 -- about $102/month -- before a single container
  # runs. Omitting it keeps the environment on Consumption, where the free
  # grant applies.
}

resource "azurerm_container_app" "api" {
  name                         = "${var.prefix}-api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  # System-assigned rather than user-assigned: this identity has exactly one
  # consumer and should not outlive it. Its lifecycle is the app's, which is
  # also what makes `terraform destroy` leave no orphaned principal behind.
  identity {
    type = "SystemAssigned"
  }

  # Secrets live in the platform, not in the image and not in this repository.
  secret {
    name  = "database-url"
    value = var.database_url
  }
  secret {
    name  = "redis-url"
    value = var.redis_url
  }
  secret {
    name  = "jwt-secret"
    value = var.jwt_secret
  }
  secret {
    name = "contribution-pepper"
    # Falls back to the JWT secret, matching what docker-compose.prod.yml did
    # on the VM. Stated here rather than left to the application so the value
    # is stable across deploys -- see the variable's warning about rotation.
    value = var.contribution_pepper != "" ? var.contribution_pepper : var.jwt_secret
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    # HTTP is redirected rather than served. The refresh token is an httpOnly
    # cookie; carrying it over plaintext once is once too many.
    allow_insecure_connections = false
    transport                  = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # Scale to zero is the whole cost model. `max_replicas = 2` is a ceiling
    # against a traffic spike quietly spending the credit, not a capacity plan.
    min_replicas = 0
    max_replicas = 2

    container {
      name   = "api"
      image  = var.image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "REDIS_URL"
        secret_name = "redis-url"
      }
      env {
        name        = "JWT_SECRET"
        secret_name = "jwt-secret"
      }
      env {
        name        = "CONTRIBUTION_PEPPER"
        secret_name = "contribution-pepper"
      }
      env {
        name  = "CORS_ORIGINS"
        value = var.frontend_origin
      }
      env {
        name  = "STORAGE_BACKEND"
        value = "azure_blob"
      }
      env {
        name  = "AZURE_STORAGE_ACCOUNT"
        value = azurerm_storage_account.receipts.name
      }
      env {
        name  = "AZURE_BLOB_CONTAINER"
        value = azurerm_storage_container.receipts.name
      }
      # Deliberately no AZURE_STORAGE_KEY. The adapter falls back to
      # `DefaultAzureCredential` when none is set, which resolves to this app's
      # system-assigned identity -- and the account has keys disabled outright,
      # so there is no key that could be set even by mistake.
      env {
        name  = "ENVIRONMENT"
        value = "production"
      }

      # Readiness gates traffic; liveness restarts a wedged container. Both hit
      # `/health`, which the Dockerfile's own HEALTHCHECK already uses.
      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        initial_delay           = 5
        interval_seconds        = 10
        failure_count_threshold = 3
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        initial_delay           = 20
        interval_seconds        = 30
        failure_count_threshold = 3
      }
    }
  }
}
