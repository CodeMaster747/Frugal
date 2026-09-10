output "api_url" {
  description = "Public HTTPS endpoint. Set this as BACKEND_ORIGIN on Render, then redeploy the frontend."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
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
  EOT
}
