output "api_url" {
  description = "Public HTTPS endpoint. Set this as BACKEND_ORIGIN on Render, then redeploy the frontend."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "storage_account" {
  description = "Receipt images. Reached with the app's managed identity -- there is no key, and none can be created."
  value       = azurerm_storage_account.receipts.name
}

output "jobs" {
  description = "The scheduled background jobs, and when each runs."
  value = merge(
    {
      for name, job in local.scheduled_jobs :
      "${var.prefix}-${name}" => job.cron
    },
    { "${var.prefix}-receipts-now" = "manual trigger" },
  )
}

output "run_receipts_now" {
  description = "Drain the receipt queue immediately, rather than waiting for the cron."
  value       = "az containerapp job start -n ${azurerm_container_app_job.receipts_now.name} -g ${azurerm_resource_group.main.name}"
}

output "next_steps" {
  value = <<-EOT
    1. Set BACKEND_ORIGIN on the Render service to the api_url above and
       redeploy. It is a server-side rewrite target (frontend/next.config.ts),
       so the browser never sees it and no CORS preflight is involved.
    2. Apply migrations against the Neon database. Container Apps runs no
       one-off command on deploy, so this is `alembic upgrade head` from a
       machine that can reach it -- see RUNBOOK.md.
    3. Expect a cold start on the first request after idle. min_replicas = 0
       is what keeps this inside the free grant.
    4. Prove a receipt actually processes end to end: upload one, then run the
       `run_receipts_now` command above and watch the row leave `queued`. It is
       only proven when `overall_confidence` is non-null -- a green execution
       with a null confidence means OCR_ENGINE was not `tesseract` and the fake
       engine "succeeded" having read nothing.
  EOT
}
