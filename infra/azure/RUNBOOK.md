# Runbook

> **There is no deployment right now.** It was torn down on 2026-09-10 — see
> [COST-SAFETY.md §7](COST-SAFETY.md) for why, and
> [ADR-010](../../docs/adr/010-azure-migration.md) for what replaces it. Everything
> below is still correct as *instructions*: section 1 recreates the stack from
> nothing, and the operational recipes apply once it exists again. Commands naming
> `frugal-rg` will fail until then, which is expected rather than broken.

Everything you do to this deployment after it exists. Read
[COST-SAFETY.md](COST-SAFETY.md) first if you have not — section 1 there is the
only thing standing between this project and a bill, and it takes two minutes.

The deployment: one `Standard_B1s` VM running Caddy, the API, a Celery worker,
beat, and Redis under Docker Compose. Postgres is on Neon, the frontend on
Render, and receipt images in Azure Blob Storage. See
[ADR-010](../../docs/adr/010-azure-migration.md) for why the boundary sits
exactly there.

---

## 1. First deployment

```bash
cd infra/azure

# 0. Confirm which subscription you are about to spend.
az login
az account show --query '{name:name, state:state}' -o table   # expect: Azure for Students / Enabled

# 1. Check there is quota for the VM. Student subscriptions carry per-region
#    limits that are occasionally zero, and the failure at apply time is a
#    QuotaExceeded that names a family rather than a size.
az vm list-usage --location southeastasia -o table | grep -i "BS Series"

# 2. Infrastructure
cd terraform
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars          # subscription id, your IP, your public key, alert email, frontend origin
terraform init
terraform apply

# 3. DNS, before secrets — Caddy needs a name it can prove
#    Point an A record at:
terraform output -raw public_ip

# 4. Secrets, once, by hand (§2)

# 5. Deploy
cd ..
./deploy.sh "$(terraform -chdir=terraform output -raw public_ip)"
```

Two things about step 2 that are not errors:

- **Role assignments take a minute or two to propagate.** A 403 from the storage
  account immediately after `apply` is almost always that. Wait, then retry
  before debugging anything.
- **The `AzureMonitorLinuxAgent` extension is slow.** Several minutes is normal.
  `apply` waits for it.

### Migrating from the AWS deployment

If the EC2 deployment is still running, receipt images are still in S3 and the
database rows still point at them by key. Copy them **before** cutting over —
the script is idempotent and reads only, so it is safe to run early and again
later:

```bash
cd infra/azure
S3_BUCKET="$(terraform -chdir=../aws/terraform output -raw receipts_bucket)" \
AZURE_STORAGE_ACCOUNT="$(terraform -chdir=terraform output -raw storage_account)" \
  ./migrate-receipts.sh ~/frugal-receipts-migration
```

Then follow its closing instructions to prove a blob name matches an `s3_key` in
Postgres. The count matching is necessary and not sufficient: if the names are
wrong, every image 404s while the database still cheerfully points at them.

Do not destroy the AWS stack until the Azure deployment has served a real
receipt image to a real browser. See §7.

---

## 2. Secrets

`/opt/frugal/.env` on the VM, created once by hand, `chmod 600`. Never in this
repository, never in `custom_data` — cloud-init data is readable by anything on
the box that can reach the metadata service.

```bash
ssh azureuser@<public-ip>
sudo -u azureuser tee /opt/frugal/.env >/dev/null <<'ENVFILE'
DATABASE_URL=postgresql+asyncpg://...          # Neon POOLED endpoint
DATABASE_DIRECT_URL=postgresql+asyncpg://...   # Neon DIRECT endpoint, for Alembic
REDIS_URL=redis://redis:6379/0
JWT_SECRET=                                    # openssl rand -hex 32
AZURE_STORAGE_ACCOUNT=                         # terraform output storage_account
AZURE_BLOB_CONTAINER=receipts
DOMAIN=frugal-api.example.org
CORS_ORIGINS=https://frugal-web.onrender.com
ENVFILE
chmod 600 /opt/frugal/.env
```

**Note what is absent: any storage credential at all.** The VM authenticates to
Blob Storage as its system-assigned managed identity, and the storage account
has shared access keys disabled, so there is nothing to put here even if you
wanted to. If you find yourself reaching for `AZURE_STORAGE_KEY`, the problem is
a missing role assignment — see §6.

`DATABASE_DIRECT_URL` is not optional. Alembic through Neon's pooler can fail
partway and leave the schema in a state no migration describes.

---

## 3. Routine operations

```bash
# Deploy the current working tree
./deploy.sh <public-ip>

# Logs — the journal is the source of truth now, not `docker logs`
ssh azureuser@<ip> 'journalctl -u docker -n 100 --no-pager'
ssh azureuser@<ip> 'cd /opt/frugal/infra/azure && docker compose -f docker-compose.prod.yml logs -f api'

# Restart one service
ssh azureuser@<ip> 'cd /opt/frugal/infra/azure && docker compose -f docker-compose.prod.yml restart worker'

# Your ISP moved you and SSH stopped answering
./allow-my-ip.sh

# Stop spending credit while nobody is looking at it
az vm deallocate -g frugal-rg -n frugal-app
az vm start      -g frugal-rg -n frugal-app     # the public IP is static; DNS survives
```

Querying the collected logs, which is the thing CloudWatch never actually had
data for:

```bash
WS=$(terraform -chdir=terraform output -raw log_analytics_workspace)
az monitor log-analytics query \
  --workspace "$(az monitor log-analytics workspace show -g frugal-rg -n "$WS" --query customerId -o tsv)" \
  --analytics-query "Syslog | where TimeGenerated > ago(1h) | where SyslogMessage has 'status_code' | take 20" \
  -o table
```

---

## 4. Verify the alerts — by triggering them

An alert nobody has seen fire is a hypothesis. Three of the four here are
cheap to test, and the fourth is the one worth testing most.

**4.1 Delivery works at all.** Azure sends immediately with no confirmation
click, unlike SNS — which means the failure mode is a typo in the address rather
than an unconfirmed subscription.

```bash
az monitor action-group test-notifications create \
  -g frugal-rg --action-group frugal-alerts \
  --alert-type servicehealth --notification-type email
```

Check the inbox. Nothing arriving means the address is wrong.

**4.2 `frugal-5xx-errors`.** This is the one that was broken on AWS for the life
of the deployment — the agent tailed a file nothing wrote. Prove it works here:

```bash
# Hit an endpoint that 500s, or force one:
ssh azureuser@<ip> 'logger -p daemon.err "{\"status_code\": 503, \"test\": true}"'
```

Six of those inside fifteen minutes should page. Under five should not — the
threshold is `> 5` because one 500 is noise.

**4.3 `frugal-disk-nearly-full`.** Fills the disk to under 15% free. Remove the
file afterwards, and do not run this while a deploy is in flight.

```bash
ssh azureuser@<ip> 'fallocate -l 26G /tmp/ballast && sleep 1200 && rm /tmp/ballast'
```

Expect ~30 minutes: the agent samples every 5 minutes and the rule evaluates
every 15.

**4.4 `frugal-vm-unavailable`.** Deallocating the VM triggers it. This is also
the fastest way to confirm that alerts reach you when the box is gone, which is
precisely when you need them to.

---

## 5. Backups

Local, not to Blob Storage. A backup stored inside the subscription that stopped
is not a backup.

```bash
export DATABASE_DIRECT_URL='<Neon direct endpoint>'
export AZURE_STORAGE_ACCOUNT='<terraform output storage_account>'

cd ../ops
./backup.sh ~/frugal-backups
./restore.sh ~/frugal-backups/<newest> --into-scratch
```

Run the restore. It exits non-zero if the dump restores cleanly but empty, which
is what a backup pointed at the wrong database looks like at every other step.

`backup.sh` reads blobs with `--auth-mode login`, so it needs the Storage Blob
Data Contributor role that Terraform assigned to whoever ran `apply`. If you run
backups from a different account, assign it there too.

---

## 6. When something is wrong

**SSH times out, HTTPS works.** Your address moved. `./allow-my-ip.sh`.

**Both time out.** Check the VM is running rather than deallocated, and the
subscription is Enabled rather than Disabled:

```bash
az vm get-instance-view -g frugal-rg -n frugal-app \
  --query "instanceView.statuses[?starts_with(code,'PowerState')].displayStatus" -o tsv
az account show --query state -o tsv
```

**403 from Blob Storage.** Almost always one of three things, in order of
likelihood:

1. **Role assignment has not propagated.** Minutes, not seconds. Wait.
2. **You are calling as yourself without the data-plane role.** Being
   subscription Owner is a *control-plane* role: it can delete the storage
   account and cannot read a blob inside it. Check what you have:
   ```bash
   az role assignment list --assignee "$(az ad signed-in-user show --query id -o tsv)" \
     --scope "$(az storage account show -g frugal-rg -n <account> --query id -o tsv)" -o table
   ```
3. **You forgot `--auth-mode login`.** Shared keys are disabled, so the CLI's
   default key-based auth fails. The fix is the flag, never re-enabling keys.

**Uploads fail in the browser with a CORS error.** The frontend's origin is not
in `cors_allowed_origins`. Azure requires `x-ms-blob-type` on the PUT, which is
not safelisted, so every upload preflights — unlike S3, which needed no bucket
CORS policy at all. Add the origin to `terraform.tfvars` and re-apply.

**Uploads fail with 400 and `x-ms-blob-type` in the message.** The browser is
not sending the header. It comes from `upload_headers` on the ticket response;
check the API is running the Azure adapter (`STORAGE_BACKEND=azure_blob`) and
that the frontend build is recent enough to spread them.

**The API will not start, `ImportError: azure`.** The image was built without
the `azure` extra. `docker-compose.prod.yml` sets
`INSTALL_EXTRAS: "auth,ocr,classify,azure"` — rebuild with `--no-cache` if an
older layer is cached.

**Alerts stopped.** The agent extension is the usual cause, and its failure is
silent — the disk rule reports no data rather than firing.

```bash
az vm extension show -g frugal-rg --vm-name frugal-app -n AzureMonitorLinuxAgent \
  --query provisioningState -o tsv
```

---

## 7. Decommissioning the AWS stack

Only after the Azure deployment has served a real receipt image to a real
browser. Not before, and not on the same day you cut over.

```bash
# 1. A final backup, taken while the S3 bucket still exists
cd infra/ops
export DATABASE_DIRECT_URL='<Neon direct endpoint>'
export S3_BUCKET="$(terraform -chdir=../aws/terraform output -raw receipts_bucket)"
./backup.sh ~/frugal-backups-final
./restore.sh ~/frugal-backups-final/<newest> --into-scratch

# 2. Point the frontend at Azure and confirm it works, end to end, in a browser.
#    Render dashboard → BACKEND_ORIGIN → the new domain. Redeploy.

# 3. Only then
terraform -chdir=../aws/terraform destroy
```

S3 refuses to delete a non-empty bucket, so step 3 will fail until the objects
are gone. That refusal is a feature — it is the last thing standing between you
and deleting the images. Empty it deliberately, once the backup in step 1 has
been verified:

```bash
aws s3 rm "s3://${S3_BUCKET}" --recursive
```

Keep `infra/aws/` in the repository. It is the record of a deployment that
worked, ADR-010 refers to it, and its COST-SAFETY.md is still the better
document on the topic of surprise bills.

---

## 8. Known gaps

- **No staging environment.** Deploys go to production. The suite and the E2E
  tests are what stands in for one.
- **The 5xx alert parses JSON out of syslog with a regex.** It works, and it is
  fragile to a change in the log format. A structured custom table would be
  correct and needs a DCR transform that is more machinery than this earns.
- **The content type of an upload is not enforced at write time.** Azure SAS
  cannot do it; the defence moved to read time (ADR-010). A malicious upload can
  put arbitrary bytes at a key it was granted, and those bytes are served back
  only under the content type recorded at ticket time.
- **One VM, no redundancy.** Deliberate. An outage costs a restart, and the data
  that cannot be recreated is not on it.
