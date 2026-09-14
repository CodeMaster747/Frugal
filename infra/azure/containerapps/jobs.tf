# The background work, as Container Apps Jobs.
#
# This is what finally runs the sweeps and receipt OCR. Everything that came
# before it -- the erasure fix, `list_prefix`, the blob erasure kind, the
# one-shot runner, the worker image -- is capability with nothing invoking it.
#
# ## Why these live in a second environment
#
# `azurerm_container_app_environment.main` is **Express**, and Express refuses
# job resources outright:
#
#     ERROR: (ExpressEnvironmentResourceNotSupported) 'Job' resources are not
#     supported on express environments.
#
# That was probed directly rather than taken from the docs, with a job whose
# cron is 31 February so it could never fire.
#
# The obvious fix -- move the API to a standard environment -- is the expensive
# one: the FQDN changes, which means updating BACKEND_ORIGIN on Render and
# taking downtime for a cosmetic reason. **Jobs do not need to share an
# environment with the app.** They need the image, the secrets, the identity,
# and outbound network to Neon, Upstash and Blob. So the API stays exactly where
# it is and the jobs get an environment of their own.
#
# ## Why the environment is a data source and not a resource
#
# The property that decides this is `properties.environmentMode`, and **azurerm
# 4.81 has no argument for it** -- confirmed against the provider schema, whose
# environment resource exposes nothing mode-like at all. Only the CLI can set
# it, with `--environment-mode WorkloadProfiles`.
#
# A `workload_profile` block is *not* the equivalent, and assuming it was would
# have wasted an apply: the Express environment already carries an identical
# `workloadProfiles: [Consumption]` array. Workload profiles are not what makes
# an environment Express.
#
# `terraform import` would be worse than a data source, not better. The resource
# has no `environmentMode` attribute, so a future destroy-and-recreate would
# quietly produce an **Express** environment, and every job here would start
# failing with the error above -- at a moment nobody was looking at this file.
# A data source cannot recreate what it does not own, which is the property
# wanted here.
#
# The cost of that choice is honest and small: `terraform apply` fails until the
# environment exists. See RUNBOOK.md for the one command that creates it.

data "azurerm_container_app_environment" "jobs" {
  name                = var.jobs_environment_name
  resource_group_name = azurerm_resource_group.main.name
}

locals {
  # Named exactly as in `azurerm_container_app.api`, so a reader comparing the
  # two sees the same four names rather than two conventions.
  job_secrets = {
    "database-url"        = var.database_url
    "redis-url"           = var.redis_url
    "jwt-secret"          = var.jwt_secret
    "contribution-pepper" = var.contribution_pepper != "" ? var.contribution_pepper : var.jwt_secret
  }

  job_secret_env = {
    DATABASE_URL        = "database-url"
    REDIS_URL           = "redis-url"
    JWT_SECRET          = "jwt-secret"
    CONTRIBUTION_PEPPER = "contribution-pepper"
  }

  # No CORS_ORIGINS: a job has no ingress and serves nobody.
  #
  # REDIS_URL is here despite nothing in the runner using a broker, because
  # `Settings` requires it to construct at all -- importing `app.core.queue`
  # builds the Celery app at module scope. Supplying it costs nothing; omitting
  # it means the process cannot start.
  job_plain_env = {
    STORAGE_BACKEND       = "azure_blob"
    AZURE_STORAGE_ACCOUNT = azurerm_storage_account.receipts.name
    AZURE_BLOB_CONTAINER  = azurerm_storage_container.receipts.name
    AZURE_CLIENT_ID       = azurerm_user_assigned_identity.api.client_id
    ENVIRONMENT           = "production"
  }

  scheduled_jobs = {
    "sweeps-hourly" = {
      cron    = "15 * * * *"
      image   = var.image
      cpu     = 0.25
      memory  = "0.5Gi"
      timeout = 600
      # `auth` is the only extra these need, and the API image has it.
      extra_env = {}
    }

    "sweeps-nightly" = {
      # 03:35, not 03:30. `*/30` and `30 3 * * *` would collide on the half
      # hour, and two executions against Neon's free tier at once is the
      # connection pressure this deployment can least afford.
      cron      = "35 3 * * *"
      image     = var.image
      cpu       = 0.25
      memory    = "0.5Gi"
      timeout   = 900
      extra_env = {}
    }

    "receipts" = {
      # Every half hour. The plan's arithmetic puts the three jobs at ~21% of
      # the monthly free grant at this cadence; `*/15` would double the
      # receipts share for a latency win that `receipts-now` already provides on
      # demand. Measure real durations with `az containerapp job execution list`
      # before tightening it -- the estimates behind that 21% include a cold
      # start pulling a 944 MB image, and are estimates.
      cron    = var.receipts_cron
      image   = var.worker_image
      cpu     = 0.5
      memory  = "1.0Gi"
      timeout = 600
      # Without this the runner builds `FakeOCREngine` and every execution
      # succeeds having extracted nothing -- receipts reach `ready` carrying no
      # data, and nothing downstream can tell. `run_jobs` warns on stderr when
      # it sees this unset, which is the other half of the guard.
      extra_env = { OCR_ENGINE = "tesseract" }
    }
  }
}

resource "azurerm_container_app_job" "scheduled" {
  for_each = local.scheduled_jobs

  name                = "${var.prefix}-${each.key}"
  resource_group_name = azurerm_resource_group.main.name
  # The jobs environment may be in a different region to the resource group --
  # the RG is in Indonesia Central and the environment wherever the
  # `--environment-mode` create succeeded. Taken from the environment rather
  # than from `var.location` so the two cannot disagree.
  location                     = data.azurerm_container_app_environment.jobs.location
  container_app_environment_id = data.azurerm_container_app_environment.jobs.id
  tags                         = local.tags

  replica_timeout_in_seconds = each.value.timeout

  # No retries. The runner writes its own failures to the `jobs` row and the
  # next scheduled execution re-claims anything retryable, so a platform retry
  # would duplicate a mechanism that already exists and reports better.
  replica_retry_limit = 0

  # Same user-assigned identity as the API, and for the same reason: the
  # storage account has keys disabled, so `DefaultAzureCredential` naming this
  # identity through AZURE_CLIENT_ID is the only way to reach a blob. An
  # identity is not scoped to one environment, so nothing new is granted here --
  # the two role assignments in storage.tf already cover it.
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }

  schedule_trigger_config {
    cron_expression = each.value.cron
    # One replica, one completion. These are sweeps over shared state, not a
    # parallel workload, and the receipt drain's safety argument assumes a
    # single claimant per execution.
    parallelism              = 1
    replica_completion_count = 1
  }

  dynamic "secret" {
    for_each = local.job_secrets
    content {
      name  = secret.key
      value = secret.value
    }
  }

  template {
    container {
      name   = each.key
      image  = each.value.image
      cpu    = each.value.cpu
      memory = each.value.memory

      # The image's CMD is uvicorn (API) or celery worker (worker image).
      # Neither is wanted; both Dockerfiles use a bare CMD with no ENTRYPOINT,
      # so this replaces it cleanly.
      command = ["python", "-m", "scripts.run_jobs", each.key]

      dynamic "env" {
        for_each = local.job_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      dynamic "env" {
        for_each = merge(local.job_plain_env, each.value.extra_env)
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
}

# The same receipts job, on a manual trigger.
#
# A Container Apps Job has exactly one trigger type, so "run the drain now"
# cannot be asked of the scheduled one. Rather than depend on whether
# `az containerapp job start` is accepted against a Schedule-triggered job,
# this is a second job that exists to be started by hand -- it costs nothing
# while idle, and it makes a live demo a single command instead of a wait.
resource "azurerm_container_app_job" "receipts_now" {
  name                         = "${var.prefix}-receipts-now"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = data.azurerm_container_app_environment.jobs.location
  container_app_environment_id = data.azurerm_container_app_environment.jobs.id
  tags                         = local.tags

  replica_timeout_in_seconds = 600
  replica_retry_limit        = 0

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  dynamic "secret" {
    for_each = local.job_secrets
    content {
      name  = secret.key
      value = secret.value
    }
  }

  template {
    container {
      name    = "receipts"
      image   = var.worker_image
      cpu     = 0.5
      memory  = "1.0Gi"
      command = ["python", "-m", "scripts.run_jobs", "receipts"]

      dynamic "env" {
        for_each = local.job_secret_env
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      dynamic "env" {
        for_each = merge(local.job_plain_env, { OCR_ENGINE = "tesseract" })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
}
