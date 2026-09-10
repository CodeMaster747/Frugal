# Cost safety on Azure

Written for the case where a surprise bill would genuinely hurt. Read section 1
before creating a single Azure resource.

This is the Azure counterpart of [the AWS document](../aws/COST-SAFETY.md),
which stays in the repository because its reasoning is still correct — the move
is recorded in [ADR-010](../../docs/adr/010-azure-migration.md) and was about
*term*, not about AWS being unsafe.

---

## 1. What actually protects you here

**Azure for Students cannot bill you, because there is nothing to bill.**

The offer is granted without a payment instrument. No card, no bank account, no
payment method on file. When the $100 credit is spent or the twelve months
elapse, the subscription moves to **Disabled**: running resources are stopped
and then deleted after a grace period, and the balance owed is zero.

That is a *structural* guarantee, not a configured one, and it is stronger than
anything in the rest of this document. It is the same class of protection the
AWS Free Plan gave, with twice the term.

| Mechanism | What it actually does |
|---|---|
| **No payment method on the subscription** | **The actual protection. Nothing can be charged.** |
| Azure Budgets | Sends email at a threshold. **Does not stop anything.** |
| Budget action groups | Can trigger an automation runbook. Not configured here; see §4. |
| Cost alerts | Same — a notification, not a brake. |
| Credit balance in the portal | The number that matters. Check it monthly. |

Everything after §1 is about not *wasting* the credit. None of it is about
avoiding an invoice, because an invoice is not possible.

### The two things that would undo it

1. **Converting to Pay-As-You-Go.** This is the only action that re-exposes you,
   and it requires you to enter card details — it cannot happen by accident or
   by clicking through a banner. Azure will prompt for it when the credit runs
   low. Treat every such prompt as a decision.

2. **A different subscription.** If your university tenant also grants you
   access to a departmental or sponsored subscription, that one may well have
   billing attached. Terraform deploys into whatever `subscription_id` says, so
   check it rather than trusting the CLI's active default:

   ```bash
   az account show --query '{name:name, id:id, state:state}' -o table
   ```

   Expect `Azure for Students`. Anything else, stop.

### The twelve-month clock

The offer ends on whichever comes first: credit exhausted, or twelve months.
Unlike the AWS Free Plan's six months, it is **renewable** — Microsoft
re-verifies academic status by email, and the offer continues while you are
enrolled. Renewal is not automatic; the email arrives before expiry and needs
answering.

---

## 2. What this deployment costs

Measured against a $100 credit that has to last twelve months — a budget of
**$8.33/month**. Prices are list, `centralindia`, pulled from the Azure Retail
Prices API rather than estimated:

| Resource | Monthly | Note |
|---|---|---|
| `Standard_B1s` VM (1 vCPU / 1 GB) | ~$8.18 | The largest line, and may be covered — see below |
| Standard SSD OS disk, 32 GiB (E4) | ~$2.64 | 30 GiB would bill as 32 anyway |
| Standard static public IP | ~$3.65 | Unavoidable; Basic SKU was retired Sept 2025 |
| Blob Storage, hot LRS | ~$0.02/GB | Receipts expire at 90 days |
| Log Analytics | $0 | 5 GB/month ingestion is free, and `daily_quota_gb = 1` caps it |
| Azure Monitor alerts | $0 | Metric alerts and two log queries are within the free allowance |
| Bandwidth out | $0 | First 100 GB/month is free |
| **Total** | **~$14.47** | **Over budget at list price** |

That number is above $8.33, and the resolution is the free-services allowance:
Azure grants **750 hours/month of B1s Linux for the first 12 months**, which
covers the VM entirely and brings the running total to roughly **$6.29/month**.

### The floor, and why nothing gets under $4

Worth stating plainly, because it is the question everyone asks second: **the
static public IP alone is $3.65/month**, and Standard is the only SKU Azure will
still create. Add the smallest OS disk an Ubuntu-plus-Docker box can boot from
and the floor for an always-on VM here is around **$5/month**, even with the VM
itself free. Going below that means having no public IP — which is the argument
for Container Apps, where ingress, TLS and a custom domain are included and the
consumption free grant covers a low-traffic API outright.

### This table once described a machine that was never deployed

An earlier revision priced a `Standard_B1s` and concluded ~$6/month. The
deployment actually ran a **`Standard_B2ts_v2` at $14.89/month** — because
`variables.tf` defaulted to that size while its own docstring argued for B1s,
and `terraform.tfvars` never overrode it. No free-services grant applies to a v2
size, so the "the VM is covered" line above was false for what was running. It
spent roughly $7 of the credit without ever serving a request, and the
deployment was torn down rather than repaired (see §7).

The lesson is the one this document already tries to teach, arriving from an
unexpected direction: **the number that matters is the credit balance, not this
table.** A cost model in a file cannot notice that the infrastructure disagrees
with it.

**Verify this rather than believing this table.** The free-services catalogue is
Microsoft's to change, and whether every item applies to the student offer
specifically is worth ten seconds of checking against your own balance:

```bash
# What has actually been spent, this month, by resource
az consumption usage list --start-date 2026-08-01 --end-date 2026-08-31 \
  --query "[].{name:instanceName, cost:pretaxCost}" -o table
```

The `frugal-budget` resource created by Terraform emails at 80% of $9 actual and
at 100% forecast, which is the early warning that the assumption above is wrong.

### If it is over budget

In descending order of saving, and none of these is a crisis:

1. **Deallocate the VM when not demonstrating it.** Stopped-deallocated VMs bill
   nothing for compute; the disk and IP continue. This alone halves the cost.
   ```bash
   az vm deallocate -g frugal-rg -n frugal-app     # ~$7.59/month → $0
   az vm start      -g frugal-rg -n frugal-app
   ```
   The public IP is static, so DNS survives this. On AWS it did not.

2. **Drop to Standard HDD** (`Standard_LRS`) — saves ~$0.90/month. The disk is
   idle between deploys, so the latency does not matter.

3. **Move receipts to Cloudflare R2.** The `s3.py` adapter already speaks it
   (ADR-010 kept it for exactly this), 10 GB free, hard-capped. Only worth it if
   receipt volume ever becomes real.

---

## 3. What causes wasted credit

The AWS list was about avoiding four-figure bills. This one is about not burning
a year's runway in a month. Different stakes, different list.

| Cause | Cost | Guard |
|---|---|---|
| **A larger VM size** | 4–10× | `vm_size` validation refuses anything outside the B-series |
| **Azure Cache for Redis** | ~$16/month for the smallest tier | Redis is co-hosted (ADR-006). Never provision the managed one. |
| **Azure Database for PostgreSQL** | ~$13/month after 12 months | Postgres is on Neon. ADR-010 explains why it stays there. |
| **Azure Container Registry** | ~$5/month Basic, billed idle | The VM builds from source; `deploy.sh` pushes no images |
| **Load Balancer / Application Gateway** | $18–$250/month | Caddy terminates TLS on the VM |
| **Log Analytics over-ingestion** | ~$2.30/GB past 5 GB | `daily_quota_gb = 1` on the workspace |
| **Orphaned disks and IPs** | Grows quietly | `terraform destroy` takes the whole resource group |
| **A second region by accident** | Doubles everything | Everything is in one resource group; check the portal's region filter |

The one Azure-specific trap worth naming: **`Standard_B1ls`** looks like the
cheapest option and has 0.5 GB of RAM. The worker measures ~450 MB during a
Prophet fit. It will not run.

---

## 4. Guardrails, in the order to apply them

### 4.1 Confirm the subscription (30 seconds, do it first)

```bash
az account show --query '{name:name, state:state}' -o table
```

`Azure for Students` and `Enabled`. This is the control that matters; everything
below is hygiene.

### 4.2 Secure the account

1. **MFA on the account.** Usually enforced by the university tenant already —
   confirm rather than assume.
2. **No service principals with client secrets.** This deployment needs none:
   the VM uses a managed identity and you use `az login` interactively. A
   long-lived client secret in a CI variable is this stack's nearest equivalent
   to a leaked IAM key.
3. **Shared storage keys stay disabled.** Terraform sets
   `shared_access_key_enabled = false`. If something fails with 403 and a
   suggestion to enable them, the fix is a role assignment, not that switch.

### 4.3 The budget

Created by `terraform apply` — `infra/azure/terraform/budget.tf`. Emails at 80%
of actual and 100% of forecast. It notifies; it does not stop. That is
acceptable here only because §1 is true.

### 4.4 Check the credit balance monthly

Portal → **Cost Management + Billing** → **Credits**. Two minutes. It is the
control that actually catches things, and unlike a bill it tells you how much
runway is left rather than how much is already gone.

### 4.5 Deallocate when idle

If the project is not being actively demonstrated, `az vm deallocate` is free to
do and free to undo. There is no equivalent of a forgotten EC2 instance quietly
billing for months here — but there is a forgotten VM quietly eating the credit
that was meant to last until graduation.

---

## 5. Residual risk, stated plainly

With everything above in place, the ways this still goes wrong are:

- **You convert to Pay-As-You-Go**, deliberately, and then leave something
  running. This is the only path to an actual invoice.
- **You deploy into the wrong subscription** on a tenant that has a billed one.
  §1's second bullet is the guard; it is a habit, not a control.
- **The credit runs out early** and the subscription is disabled with the VM
  still holding the only copy of something. It never does —
  [`infra/ops/backup.sh`](../ops/backup.sh) writes locally for this reason, and
  the receipt images are the only user data on Azure at all.

The honest summary: on this subscription you cannot be billed, and the thing to
protect is the runway. Check the credit balance once a month and deallocate the
VM when nobody is looking at it.

---

## 6. If the subscription is disabled anyway

Nothing here is an emergency, and the reasoning is the same as
[the AWS account-migration note](../aws/ACCOUNT-MIGRATION.md).

| What | Where it lives | Survives? |
|---|---|---|
| All financial data — accounts, transactions, budgets, goals, insights, forecasts | **Neon** | **Yes.** Not an Azure service. |
| Cache, rate limits, Celery queue | Redis on the VM | No, and it does not matter — all of it is regenerable |
| Frontend | **Render** | **Yes.** |
| API + worker + beat | The VM | No — Terraform recreates it in minutes |
| Receipt **images** | Blob Storage | No — back these up |

So the loss is one VM that `terraform apply` rebuilds, and the receipt
photographs. Everything extracted *from* a receipt is in Postgres and is
untouched.

Before the credit runs out:

```bash
export DATABASE_DIRECT_URL='<the Neon direct endpoint>'
export AZURE_STORAGE_ACCOUNT='<terraform output storage_account>'

cd infra/ops
./backup.sh ~/frugal-backups
./restore.sh ~/frugal-backups/<newest> --into-scratch   # prove it works
```

The second command is not optional ceremony. It restores into a throwaway
container and counts the rows, and it exits non-zero if the dump restores
cleanly but empty — which is exactly what a backup pointed at the wrong database
looks like at every other step.

---

## 7. Current status — torn down

**As of 2026-09-10 there is no Azure deployment.** `terraform destroy` removed all
26 resources including the `frugal-rg` resource group, and the subscription now
carries nothing for this project. Running cost is **$0.00/month**.

Why, in one paragraph: the VM ran for eleven days at $14.89/month (the wrong-size
default described in §2) and never served a request — no DNS was pointed at it,
`/opt/frugal/.env` was never created, and `deploy.sh` never ran. Stopping it left
the OS disk and the static public IP still billing $6.29/month for a machine
nobody could reach. Since the box is entirely reproducible from `terraform apply`
plus `cloud-init.sh` plus `deploy.sh`, keeping it stopped was paying to preserve
something a command rebuilds in minutes — and one stray `terraform apply` would
have put the whole $21/month back.

Nothing was lost. The `receipts` container held zero blobs; Postgres is on Neon,
Redis on Upstash, and the frontend on Render, none of which are Azure. The
untracked `terraform.tfvars` is deliberately left on disk, because it is what a
future `terraform apply` needs.

**The intended successor is Azure Container Apps**, on the Consumption plan with
scale-to-zero: the free grant (180k vCPU-seconds, 360k GiB-seconds, 2M requests
per month) covers a portfolio-traffic API, and ingress, TLS and custom domains
are included — which is what removes the $3.65 public IP that §2 identifies as
the floor. That migration is not yet built.

Everything in `infra/azure/` remains accurate as *instructions*; it describes a
deployment that can be recreated, not one that is currently running.

