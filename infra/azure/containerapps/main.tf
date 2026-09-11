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
#   * **No worker or beat.** Celery needs a process that does not scale to
#     zero. The plan was a Container Apps Job on a cron schedule, but this
#     environment is Express, and Express does not support jobs -- so that plan
#     does not apply here. The worker needs a standard environment (which this
#     subscription refused in Central India) or a different host. Until then,
#     receipt OCR and the periodic sweeps do not run. Everything synchronous --
#     the map, auth, transactions, the price graph -- does.

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
# stdout.
#
# **The daily quota is the budget control, and 1 GB/day was wrong.** Log
# Analytics gives 5 GB of ingestion free per month and bills $2.30/GB after
# that -- the same rate in centralindia and in indonesiacentral, where this runs. A 1 GB/day cap permits ~30 GB in a month, so the worst
# case was 25 billable GB -- about $57, against a deployment budgeted under
# three dollars. The cap was protecting against nothing that mattered.
#
# 0.15 GB/day is ~4.65 GB/month, which cannot leave the free allowance no
# matter what the app does. The trade is explicit: a container in a crash loop
# can exhaust a day's budget and lose the rest of that day's logs -- precisely
# when logs are most wanted. That is still the right way round, because the
# alternative is discovering a $57 bill on a $100 credit that has to last a
# year, and `az containerapp logs show --follow` reads the live stream directly
# rather than through the workspace.
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.prefix}-logs"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  # Retention beyond the included 31 days bills $0.117/GB/month in
  # indonesiacentral ($0.14 in centralindia). Thirty keeps it
  # inside the free window.
  retention_in_days = 30
  daily_quota_gb    = 0.15
  tags              = local.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "${var.prefix}-env"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags

  # No `workload_profile` block, and no environment mode either.
  #
  # **An earlier version of this comment was wrong**, and said so with some
  # confidence: that declaring a workload profile moves the environment onto the
  # Dedicated plan at about $102/month. Microsoft's billing docs say otherwise:
  # "You aren't billed any plan management charges unless you use a Dedicated
  # workload profile in your environment." A Consumption profile carries no
  # management fee -- Azure attached one to this environment on its own. What
  # does bill a plan management charge is a *Dedicated* profile, a private
  # endpoint, or planned maintenance.
  #
  # What actually shapes this environment is that **Azure created it in Express
  # mode** (preview). azurerm 4.81 exposes no argument to choose, and the FAQ
  # says new environments default to standard -- yet this one is Express, and
  # Central India refused a standard one outright on this subscription. Express
  # rejects system-assigned identity (hence the user-assigned one below),
  # supports manual secrets and HTTP probes, and does not support jobs, workload
  # profiles, or custom domains. See RUNBOOK.md.

  lifecycle {
    # Azure attached a Consumption workload profile to this environment on its
    # own, and a plan without this wanted to strip it: an update to an Express
    # environment, touching a feature Express treats as unsupported. That
    # request could be rejected part-way through an apply that has already
    # swapped the role assignments, leaving a half-changed deployment. The
    # profile carries no fee -- management charges apply only to Dedicated
    # profiles -- so the right move is to leave Azure's platform-managed value
    # alone rather than send a change it may refuse.
    ignore_changes = [workload_profile]
  }
}

resource "azurerm_container_app" "api" {
  name                         = "${var.prefix}-api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  # User-assigned, not system-assigned -- and this is not a preference. The
  # environment is Express, and Express rejects system-assigned identity: the
  # first deploy used one, Azure created it anyway, and every later update to
  # the app was refused with ExpressEnvironmentFeatureNotSupported. User-assigned
  # identity at runtime is supported.
  #
  # The cost of the switch is one extra resource with its own lifecycle.
  # `terraform destroy` removes it with the app, so nothing is orphaned.
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
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
      env {
        # Which identity `DefaultAzureCredential` should use. With only a
        # user-assigned identity attached, the credential cannot find it on its
        # own -- it looks for a system-assigned one by default -- so without this
        # it fails to get a token and every blob call is rejected. The adapter
        # builds `DefaultAzureCredential()` with no arguments, so this variable
        # is the whole of the wiring: no application change is needed.
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.api.client_id
      }
      # Deliberately no AZURE_STORAGE_KEY. The adapter falls back to
      # `DefaultAzureCredential` when none is set, which resolves to this app's
      # user-assigned identity (named by AZURE_CLIENT_ID above) -- and the account has keys disabled outright,
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
