# ADR-010 — Deploy on Azure for Students, with a second object-storage adapter

**Status:** Accepted · **Date:** 2026-08-22 · **Deployment status:** torn down 2026-09-10, see the amendment at the end

## Context

The deployment described in ADR-004 and [09 — Deployment](../09-deployment.md) runs on an AWS account
on the **Free Plan**: one `t3.micro`, one S3 bucket, CloudWatch logs and alarms, with Postgres on Neon
and the frontend on Render. [COST-SAFETY.md](../../infra/aws/COST-SAFETY.md) records why that plan was
chosen — it *pauses* when credits or six months run out rather than billing, which is the property a
student project needs.

That reasoning has not become wrong. What changed is the clock.

| | AWS Free Plan | Azure for Students |
|---|---|---|
| Credit | $100 | $100 |
| Term | **6 months**, hard | **12 months**, renewable while enrolled |
| Card on file | No | No |
| When it runs out | Account paused | Subscription disabled |
| Can it bill you? | Only if you accept the "upgrade to Paid Plan" prompt | Only if you convert to pay-as-you-go |

The two offers are equivalent on the axis that actually matters — neither can produce an invoice
without an explicit, deliberate upgrade. They differ by a factor of two on runway, and by more than
that in practice: the student offer re-verifies annually against an academic email, so it renews while
enrolment lasts, and the AWS clock does not.

COST-SAFETY.md §2 already anticipated the consequence: *"AWS deployment is M11, at the end of the
roadmap, and the remaining milestones run longer than that. So the likely outcome is the plan expiring
before the deployment exists."* Milestones 12 and 13 have since landed, which is exactly the schedule
pressure that paragraph predicted.

So this is a **term decision, not a cost-safety one.** Nothing here should be read as AWS having been
the unsafe choice; it was safe and it was running.

## Decision

**Move the compute and object storage to Azure. Move nothing else.**

| Concern | Was | Is | Why |
|---|---|---|---|
| API · worker · beat · Redis | EC2 `t3.micro` | Azure VM `Standard_B1s` | Same shape (1 vCPU / 1 GB), same Docker Compose, same Caddy |
| Receipt images | S3 | Azure Blob Storage | New adapter behind the existing port |
| Credentials for storage | EC2 instance profile | System-assigned managed identity | Same property: nothing durable on the box |
| Logs · metrics · alerts | CloudWatch + SNS | Log Analytics + Azure Monitor action group | Direct equivalents |
| **Postgres** | **Neon** | **Neon** | Never was an AWS service |
| **Frontend** | **Render** | **Render** | Never was an AWS service |
| **Redis** | **On the box** | **On the box** | ADR-006's measurement is unchanged |

The last three rows are the point. A modular monolith with managed state outside the compute host
means "migrate the entire project to another cloud" touches one VM, one bucket, and one adapter — the
financial data never moves, and there is no migration window in which it could be lost.

### Blob Storage is a second adapter, not a reconfiguration

`app/adapters/storage/s3.py` already serves AWS S3, Cloudflare R2, Backblaze B2, and MinIO, because
those four speak one protocol and differ only by endpoint. Azure does not, so
`app/adapters/storage/azure_blob.py` is a sibling rather than a config value. Three differences reach
past the adapter boundary, and each is handled at the port rather than left to callers:

1. **Uploads need a header.** Azure rejects a PUT without `x-ms-blob-type: BlockBlob`. The port grew
   `upload_headers`, the upload ticket carries it, and the browser spreads whatever it is given — so
   the frontend has no branch on which cloud it is talking to.

2. **A SAS cannot pin the upload's content type.** An S3 presigned PUT signs `ContentType`, so a URL
   issued for a JPEG cannot store HTML. Azure's `rsct` and its siblings are *response* overrides and
   constrain reads only. The guarantee is therefore reconstructed on the read side: `presign_get`
   takes the content type recorded at ticket time and stamps it on the response, so whatever bytes
   were stored, a browser is told they are the type we accepted. Without this, a payload smuggled past
   the upload check is stored XSS on a `*.blob.core.windows.net` origin.

3. **Signing needs a key the identity does not have.** With no account key there is nothing to sign a
   SAS with, so the adapter fetches a 24-hour *user delegation key* over Entra ID and signs with that.

The S3 adapter gained the same `ResponseContentType` override, so the two behave identically. That is
belt-and-braces there and load-bearing on Azure, and having one behaviour is worth more than the line
it costs.

### Shared account keys are disabled outright

`shared_access_key_enabled = false` on the storage account. This is the one place the migration is a
security *improvement* rather than a lateral move: with keys off, the only way to reach receipt data
is an Entra ID identity, so there is no static credential that can be committed, logged, or pasted
into a chat — the failure mode COST-SAFETY.md §4 ranks first among catastrophic outcomes.

### Container logs go to journald

The CloudWatch agent was configured to tail `/var/log/frugal/app.log`. Nothing ever wrote that file —
the containers logged to Docker's `json-file` driver — so **no application log ever reached CloudWatch,
and the `frugal-5xx` metric filter matched nothing for the life of the deployment.** It was an alarm
that could not fire.

The Azure stack routes container stdout to the systemd journal, which the Azure Monitor agent
genuinely collects, and the 5xx alert is a KQL query over real rows. The journal gets its own size
ceiling in `cloud-init.sh` to replace the per-container caps `json-file` provided.

## Consequences

**Positive**

- Twelve months of runway instead of six, renewable while enrolled.
- No static storage credential exists anywhere in the system, by account policy rather than by care.
- Application logs are actually collected, and the 5xx alert can fire.
- The public IP is static at no extra cost, so DNS survives a reboot — on AWS a static address billed
  and a dynamic one moved, and the runbook carried a procedure for the move.
- ADR-004 is now evidenced rather than asserted: swapping clouds cost one adapter, one config value,
  and one header.

**Negative**

- **Two storage adapters to keep honest.** `tests/unit/test_storage_adapters.py` checks both against
  the port for exactly this reason, and CI installs the `azure` extra so those tests run rather than
  skip.
- **A content-type guarantee is weaker than it was.** On S3 an upload URL could not be repurposed. On
  Azure it can, and the defence has moved to read time. That is a real reduction in depth, mitigated
  rather than eliminated.
- **The public IP costs ~$3.65/month from credit.** Basic SKU was retired in September 2025, so
  Standard is the only option and it bills whether traffic flows or not. AWS's auto-assigned address
  was free.
- **CORS is now load-bearing.** `x-ms-blob-type` is not a safelisted header, so every browser upload
  preflights. A missing origin in the storage account's CORS rule fails as a CORS error that reads
  like a bad URL. S3 needed no bucket CORS policy at all.
- **A second cloud's idioms to know.** Being subscription Owner does not grant data-plane access to a
  blob; role assignments take minutes to propagate; `az storage` needs `--auth-mode login`. None is
  hard, all are surprising once.

## Alternatives rejected

**Stay on AWS and accept the six-month clock.** The honest option, and it was close. Rejected because
the account pausing mid-roadmap means the deployment story disappears from the project at exactly the
point it is most worth showing, and re-establishing it later is this same migration with worse timing.

**Move Postgres to Azure Database for PostgreSQL.** "Fully on Azure" is not a goal — running is. The
Flexible Server free tier is 12 months, after which a B1ms is ~$13/month against a $100 credit that
must cover a year; that one line item alone outruns the budget. Neon's free tier has no such clock,
holds the only irreplaceable data, and was never an AWS dependency. Moving it would have added the
migration's only real risk of data loss in exchange for nothing.

**Azure Container Apps instead of a VM.** Its monthly free grant (180k vCPU-seconds) is generous for
something that scales to zero, and Celery's worker and beat do not: one worker at 0.25 vCPU running
continuously is ~648k vCPU-seconds a month, comfortably past the grant. It would also need a registry
(ACR Basic is ~$5/month, more than half the VM) and a managed Redis or a fourth container. More
services, more cost, and a rewrite of a deployment that already works.

**Keep receipts on Cloudflare R2 and change no code.** The `s3.py` adapter already speaks R2, so this
was free. Rejected because it leaves the project's storage on a third provider for no benefit beyond
avoiding ~150 lines, and R2 remains available as a fallback precisely because the port makes it one.

---

## Amendment, 2026-09-10 — the deployment is torn down; the decision is not

**The decision above stands unchanged.** Azure is still the right host for this
project, for exactly the reason recorded here: twelve renewable months against
AWS's hard six, with neither able to produce an invoice. What follows is a change
of *status*, not of direction.

**The VM is gone.** `terraform destroy` removed all 26 resources including the
resource group. It had run for eleven days without ever serving a request — no
DNS, no `/opt/frugal/.env`, `deploy.sh` never executed — while billing $14.89 a
month, because `variables.tf` defaulted to `Standard_B2ts_v2` while its own
docstring argued for the `Standard_B1s` this ADR specifies. That default is now
corrected. Deallocating the VM left the disk and static IP still costing $6.29 a
month for a machine nobody could reach, so it was destroyed instead.

**What this ADR got right, and it is the part worth keeping:** the boundary. The
table above says Postgres stays on Neon, the frontend on Render, and Redis off
the managed-service bill. Because of that, destroying the entire Azure footprint
cost nothing — no data moved, no migration window existed, the receipts container
held zero blobs, and the Render frontend did not notice. A modular monolith with
managed state outside the compute host meant "delete the whole cloud deployment"
was a ten-minute operation with no user-visible consequence. That is the property
this ADR was arguing for, tested in the least expected way.

**The successor is Azure Container Apps**, not another VM. Consumption plan,
scale-to-zero, with the free monthly grant covering a portfolio-traffic API;
ingress, TLS and custom domains are included, which removes the $3.65/month
static public IP that is otherwise the hard floor on any always-on VM here. The
Blob adapter, the managed-identity approach, and the Log Analytics wiring this
ADR introduced all carry over unchanged — only the compute host differs.

`infra/azure/` remains accurate as instructions for recreating the deployment.
See [COST-SAFETY.md §7](../../infra/azure/COST-SAFETY.md) for the current status.

