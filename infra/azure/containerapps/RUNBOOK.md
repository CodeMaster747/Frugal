# Container Apps runbook

The API on Azure Container Apps. This is the successor to the VM deployment in
[`../terraform`](../terraform), which was destroyed on 2026-09-10 — see
[COST-SAFETY.md §7](../COST-SAFETY.md) and the amendment at the end of
[ADR-010](../../../docs/adr/010-azure-migration.md).

**Why this exists:** the VM design's floor is about $6.29/month even when the VM
itself is free, because a Standard static public IP costs $3.65/month on its own
and Basic was retired in September 2025. Container Apps puts ingress, TLS and
certificates in the platform, so that line disappears. With `min_replicas = 0`
the expected bill is **$0**, inside the Consumption free grant.

---

## What runs, and what does not

| | Where | Cost |
|---|---|---|
| API | Container Apps, Consumption, scale-to-zero | $0 inside the free grant |
| Postgres | Neon, free tier | $0 |
| Redis | Upstash, free tier | $0 |
| Frontend | Render, free tier | $0 |
| Receipt images | Azure Blob, hot LRS, managed identity | ~$0.02/GB, expiring at 90 days |
| Image | GitHub Container Registry, public | $0 |
| Logs | Log Analytics, capped at 0.15 GB/day | $0 — cannot leave the 5 GB free tier |

**Not deployed: the Celery worker and beat.** Celery needs a process that does
not scale to zero, which is the opposite of the cost model here. The intended
shape was a Container Apps Job on a cron schedule — but this environment is
Express, and **Express does not support jobs**, so that route is closed here. Until it
is, receipt OCR and the periodic sweeps do not run. Everything synchronous —
the map, sign-in, transactions, the price graph — does.

Receipt *upload* does work, because storing the image is synchronous — it is
the OCR that is not. `STORAGE_BACKEND` is `azure_blob`, against a storage
account whose keys are disabled outright, so the app's user-assigned identity
is the only way in. Nothing in the container's environment is worth stealing:
there is no connection string and no account key, because a connection string
could not work even if one leaked.

---

## The environment is Express, and that shapes everything

Azure created this environment with `environmentMode: Express`, a preview tier
built for fast provisioning and sub-second scale-from-zero. Nothing in Terraform
asked for it: azurerm 4.81 has no argument for environment mode, and the FAQ says
new environments default to standard. This subscription refused a standard
environment in Central India outright, so Express appears to be what it allows.

From the [Express overview](https://learn.microsoft.com/en-us/azure/container-apps/express-overview):

| | On Express |
|---|---|
| System-assigned managed identity | **Not supported** — the first deploy used it, and every update was then rejected |
| User-assigned managed identity at runtime | Supported — what this deployment uses, via `AZURE_CLIENT_ID` |
| Manual secrets | Supported (not Key Vault references) |
| HTTP health probes | Supported (not exec probes) |
| Container Apps jobs | **Not supported** — the worker cannot run as a cron job here |
| Workload profiles, Dapr | Not supported |
| Custom domains | Not supported |
| SLA | None during preview |

**Billing is unchanged by Express:** standard Consumption rates, the same monthly
free grant, and no environment fee.

---

## 1. First deployment

```bash
# 0. Confirm the subscription before spending it.
az login
az account show --query '{name:name, state:state}' -o table   # Azure for Students / Enabled

# 1. One-time prerequisites (free).
az provider register -n Microsoft.App        # takes a minute or two
az extension add -n containerapp

# 2. Publish the image. Either push to main, or run the workflow by hand:
gh workflow run publish-api-image.yml
gh run watch

#    Then make the package public, once, under the repository's Packages tab.
#    A private package needs a pull secret the Container App has no reason to
#    hold, and the failure mode is a revision that never becomes ready.

# 3. Secrets.
cd infra/azure/containerapps
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars        # paste Neon's URL unmodified -- the app
                                # normalises it. Redis needs rediss://, two s.

# 4. Apply.
terraform init
terraform apply

# 5. Migrations, if the database is not already at head. Container Apps runs no
#    one-off command on deploy, so this is driven from anywhere that can reach
#    Neon. Check first -- re-running against a current database is a no-op, but
#    knowing which it was is worth ten seconds:
cd ../../../backend
DATABASE_URL='<the same value as terraform.tfvars>' alembic current
DATABASE_URL='<the same value as terraform.tfvars>' alembic upgrade head

# 6. Point the frontend at it.
terraform -chdir=../infra/azure/containerapps output -raw api_url
#    Set that as BACKEND_ORIGIN on the Render service, then redeploy. It is a
#    server-side rewrite target (frontend/next.config.ts), so the browser never
#    sees it and no CORS preflight is involved.
```

## 2. Routine operations

```bash
# Logs, live
az containerapp logs show -g frugal-ca-rg -n frugal-ca-api --follow

# Recent revisions and their health
az containerapp revision list -g frugal-ca-rg -n frugal-ca-api -o table

# Roll out a new image without a Terraform run
az containerapp update -g frugal-ca-rg -n frugal-ca-api \
  --image ghcr.io/codemaster747/frugal-api:latest

# Restart the current revision
az containerapp revision restart -g frugal-ca-rg -n frugal-ca-api \
  --revision "$(az containerapp revision list -g frugal-ca-rg -n frugal-ca-api \
      --query '[0].name' -o tsv)"
```

## 3. What it is actually costing

```bash
az consumption usage list \
  --start-date "$(date -u -v-30d +%Y-%m-%d 2>/dev/null || date -u -d '30 days ago' +%Y-%m-%d)" \
  --end-date "$(date -u +%Y-%m-%d)" \
  --query "[?contains(instanceName,'frugal-ca')].{name:instanceName, cost:pretaxCost}" -o table
```

**Read that with suspicion.** On a sponsored offer this API reports `0.00` for
everything regardless of actual consumption, which is not the same as free. The
number that settles it is the credit balance in the portal.

The two things that would take this out of the free grant:

1. **`min_replicas` above 0.** An always-on replica at 0.25 vCPU / 0.5 GiB
   consumes roughly 657,000 vCPU-seconds a month against a 180,000 grant. That
   is the single change most likely to start a bill.
2. **Raising `daily_quota_gb` on the workspace.** Log Analytics bills $2.30/GB
   past 5 GB/month. At 0.15 GB/day the monthly total cannot reach that; at
   1 GB/day it could hit ~30 GB, which is about $57 — more than the whole
   deployment costs in a year.
3. **A *Dedicated* workload profile, a private endpoint, or planned
   maintenance.** Those carry a plan management charge. A Consumption profile
   does not — the environment already has one, attached by Azure.

   *Correction:* an earlier revision of this runbook said any `workload_profile`
   block moved the environment to the Dedicated plan at about $102/month. That
   was wrong. From the billing docs: "You aren't billed any plan management
   charges unless you use a Dedicated workload profile in your environment."

## 4. Tearing it down

```bash
terraform destroy
```

Nothing here holds state. Postgres is on Neon and Redis on Upstash, neither of
which this module creates or deletes — the same boundary that made destroying
the VM deployment a ten-minute operation with no data loss.

## 5. Known gaps

- **No worker or beat.** See above — and Express rules out running it as a job. Receipt OCR and periodic sweeps do not run.
- **No custom domain.** The app answers on its generated
  `*.azurecontainerapps.io` name, which has a managed certificate. A custom
  domain is free to add but needs DNS.
- **Cold starts.** First request after idle pays container startup. That is the
  price of `min_replicas = 0`, and it is the right trade here.
- **Migrations are manual.** Step 5 above. A Container Apps Job would automate
  it and is the natural companion to the worker work.
- **Uploaded receipts are stored but not read.** OCR runs in the worker, so an
  image lands in Blob and waits. It expires after 90 days either way.
