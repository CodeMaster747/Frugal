# 09 — Deployment

A first deployment, step by step. Every command block says **where** it runs and
in **which directory**.

Read [`infra/azure/COST-SAFETY.md`](../infra/azure/COST-SAFETY.md) before Step 9.
Its section 1 is the important one, and it is short: an Azure for Students
subscription has no payment method attached, so it *cannot* bill you — it is
disabled when the credit runs out. Everything after that is about not wasting a
year's runway.

Budget about 2.5 hours. Most of it is waiting: cloud-init, a Docker build on a
`Standard_B1s`, and a first Render build.

> Deploying on AWS instead? [`infra/aws/`](../infra/aws/) still holds that
> stack, and it worked. [ADR-010](adr/010-azure-migration.md) records why the
> target moved — it was about the six-month clock, not about AWS being unsafe.

---

## Where commands run

| Marker | Means |
|---|---|
| 💻 **Mac** | Your own Terminal. The directory is given above each block. |
| 🖥️ **Server** | Inside `ssh azureuser@<IP>`. Step 17 gets you there. |
| 🌐 **Browser** | A web console. Clicks, not commands. |

`REPO` below means `~/Documents/Rahul/Projects/Deployed/Frugal` — wherever you
cloned this. Nothing is destructive until Step 13 (`terraform apply`).

---

## What runs where, when it's done

| Layer | Host | At the free-tier limit |
|---|---|---|
| Frontend + `/api` proxy | Render web service | Sleeps after 15 min idle; 750 h/month |
| Caddy · API · worker · beat · Redis | Azure VM `Standard_B1s` | Subscription disabled when credit runs out; no bill |
| Postgres | Neon free | Paused, not billed |
| Receipt images | Azure Blob Storage | Expire at 90 days; subscription disabled, no bill |
| Logs and alerts | Log Analytics | 5 GB/month free, capped at 1 GB/day |

Three design points, because each one is the reason a step below looks odd:

- **The backend is not on Render.** Render's free instance type covers web
  services and static sites only — Background Workers and Cron Jobs are paid,
  and a free instance is 512 MB against a worker that measures ~450 MB during a
  Prophet fit (ADR-006). The `Standard_B1s` has 1 GB plus the 2 GB swap file
  `cloud-init.sh` creates.
- **Redis is on the instance, not Upstash.** Upstash free is 500k commands per
  *month*; an idle Celery worker BRPOPs its queues about once a second, roughly
  2.6M. The broker would stop answering a week into every month and the symptom
  would be tasks silently not running. This Redis holds only regenerable state,
  so it is capped at 64 MB and unpersisted.
- **The frontend proxies `/api`.** The refresh token is an httpOnly cookie with
  `SameSite=Lax`, which browsers send only on same-site requests, and
  `onrender.com` is on the Public Suffix List — so even two Render subdomains
  are two different sites. A direct cross-site call would carry no cookie and
  every session would die at the 15-minute access-token expiry.

---

## Before you start

Open a scratch note. You will collect seven values across these steps and paste
them into one file at Step 18. Referred to below as **① … ⑦**.

| | Value | Comes from |
|---|---|---|
| ① | Neon **pooled** URL, edited | Step 7 |
| ② | Neon **direct** URL, edited | Step 7 |
| ③ | JWT secret | Step 8 |
| ④ | Blob storage account name | Step 14 |
| ⑤ | VM public IP | Step 14 |
| ⑥ | DuckDNS hostname | Step 15 |
| ⑦ | Render URL | Step 21 |

Accounts you need, all free: GitHub, [Neon](https://neon.tech),
[DuckDNS](https://duckdns.org), [Render](https://render.com), and an
[Azure for Students](https://azure.microsoft.com/free/students/) subscription —
which needs an academic email address and **no credit card**.

---

# Phase 1 — Tooling and GitHub

### Step 1 — Install the CLIs · 💻 Mac

Directory doesn't matter.

```bash
brew install azure-cli terraform gitleaks
```

`gh` is already installed. Verify all four:

```bash
az version --query '"azure-cli"' && terraform version | head -1 \
  && gitleaks version && gh --version | head -1
```

> Migrating from the existing AWS deployment? Keep `awscli` too —
> [`migrate-receipts.sh`](../infra/azure/migrate-receipts.sh) reads the S3
> bucket with it. `brew install awscli`.

### Step 2 — Scan for secrets before the first commit · 💻 Mac · in `REPO`

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal
gitleaks detect --no-git --config .gitleaks.toml --redact -v
```

Expect `no leaks found`. A secret committed here stays in the history even after
you delete it, which is why this runs before `git commit` and not after.

### Step 3 — Check what will be committed · 💻 Mac · in `REPO`

```bash
git add -A
git status --short | grep -iE '\.env$|node_modules|test-results|\.next/' || echo "clean"
```

Must print `clean`. If it lists anything, stop and add it to `.gitignore`.

### Step 4 — Commit and push · 💻 Mac · in `REPO`

```bash
git commit -m "Frugal: milestones 0-10"
gh repo create frugal --private --source=. --remote=origin --push
```

> Use `--public` if this is going in a portfolio, and decide now — flipping a
> repository to public later exposes its entire history, not just its current
> state.

### Step 5 — Watch CI · 💻 Mac · in `REPO`

```bash
gh run watch
```

Wait for green. Step 19 deploys this same code, so a red build here is a failed
deploy later.

---

# Phase 2 — Database

### Step 6 — Create the Neon project · 🌐 Browser

1. [neon.tech](https://neon.tech) → sign in → **New Project**.
2. Name: `frugal`.
3. **Region — decide this now, it constrains Step 13.** Every API request makes
   several database round-trips and only one browser round-trip, so the API
   belongs next to the database, not next to the user.
   - Neon offers Mumbai → pick it, and keep Terraform's default `ap-south-1`.
   - Otherwise → pick **Singapore**, and pass `-var="region=ap-southeast-1"` at
     Step 13.
4. **Create Project**.

### Step 7 — Copy and fix both connection strings · 🌐 Browser → scratch note

On the project dashboard → **Connection Details**. You need two strings:

- **Pooled** — its hostname contains `-pooler`. Toggle **Connection pooling** on.
- **Direct** — the same, with pooling toggled off.

Neon gives you something like:

```
postgresql://frugal_owner:PASS@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/frugal?sslmode=require&channel_binding=require
```

**Make two edits to each**, or the app will not start:

1. `postgresql://` → `postgresql+asyncpg://`
2. **Delete `?sslmode=require&channel_binding=require` entirely.**

> Why deleting rather than converting: SQLAlchemy's asyncpg dialect does not
> translate `sslmode` — it forwards it to `asyncpg.connect()`, which rejects it
> as an unknown keyword. Replacing it with `?ssl=require` fixes the API and
> breaks the **worker**, which reaches the same URL through psycopg
> ([`sync_database_url`](../backend/app/core/config.py)) and understands
> `sslmode` but not `ssl`. Both drivers default to `prefer` and Neon requires
> TLS, so with no query string at all the connection is still encrypted.

Save to your note as ① and ②:

```
① DATABASE_URL=postgresql+asyncpg://frugal_owner:PASS@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/frugal
② DATABASE_DIRECT_URL=postgresql+asyncpg://frugal_owner:PASS@ep-xxx.ap-southeast-1.aws.neon.tech/frugal
```

They are not interchangeable. Pooled runs the application; direct runs Alembic,
because a migration through a connection pooler can fail partway and leave the
schema in a state no migration file describes.

### Step 8 — Generate the JWT secret · 💻 Mac

```bash
openssl rand -hex 32
```

Copy the 64-character output to your note as ③. Anything shorter than 32
characters is rejected at boot.

---

# Phase 3 — Azure guardrails (before any resource exists)

### Step 9 — Confirm which subscription you are about to spend · 💻 Mac

```bash
az login
az account show --query '{name:name, id:id, state:state}' -o table
```

It must read **Azure for Students** and **Enabled**. Copy the `id` — Step 13
needs it.

**If it says anything else, stop.** University tenants often grant access to
departmental or sponsored subscriptions, and those may well have billing
attached. Terraform deploys into whatever subscription id it is given, so this
is a habit worth forming rather than a one-off check. Switch with
`az account set --subscription "Azure for Students"`.

On this offer there is no payment method on file. When the $100 credit is spent
or the twelve months elapse, the subscription is **disabled** — resources stop,
and nothing is owed. That is a structural guarantee, and it is why Phase 3 here
is four short steps rather than the layered damage-limitation the AWS version
needed.

### Step 10 — Secure the account · 🌐 Browser

1. **MFA.** Usually enforced by the university tenant already. Confirm at
   [aka.ms/mfasetup](https://aka.ms/mfasetup) rather than assuming.
2. **Create no service principals.** This deployment needs none: the VM uses a
   managed identity, and you authenticate interactively with `az login`. A
   long-lived client secret in a CI variable is this stack's nearest equivalent
   to a leaked IAM key, and there is no reason to create one.

### Step 11 — Check there is quota for the VM · 💻 Mac

Student subscriptions carry per-region vCPU limits that are occasionally zero in
busy regions. Finding that out at `terraform apply` costs you a confusing
`QuotaExceeded` that names a VM *family* rather than a size.

```bash
az vm list-usage --location southeastasia -o table | grep -iE "BS Series|Total Regional"
```

You need at least 1 vCPU free in both rows. If either is `0`, pick another
region — `centralindia`, `eastasia`, or `australiaeast` are the nearest alternatives — and pass it
as `-var="location=..."` at Step 13.

### Step 12 — Register the resource providers · 💻 Mac

A fresh subscription has not enabled every service namespace, and the first
`apply` fails on whichever one it reaches first. Registering takes a minute and
is idempotent.

```bash
for ns in Microsoft.Compute Microsoft.Network Microsoft.Storage \
          Microsoft.OperationalInsights Microsoft.Insights Microsoft.Consumption; do
  az provider register --namespace "$ns"
done

az provider show -n Microsoft.OperationalInsights --query registrationState -o tsv
```

Wait for `Registered`. The last one is the slowest.

> There is no equivalent of Step 12 in the AWS version — no budget action, no
> deny-all policy, no zero-spend alarm. Those existed because AWS has no hard
> spending cap. Here the cap is the offer itself. The budget created at Step 13
> is an early warning that the credit is going faster than a year's runway
> allows, not a brake.

---

# Phase 4 — Infrastructure

### Step 13 — Apply the Terraform · 💻 Mac · in `REPO/infra/azure/terraform`

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/azure/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` — it is gitignored, and it holds your home IP address
and your public key:

```hcl
subscription_id      = "<the id from Step 9>"
ssh_source_address   = "<curl -s ifconfig.me>"
ssh_public_key       = "<cat ~/.ssh/id_ed25519.pub>"
alert_email          = "you@example.com"
cors_allowed_origins = ["https://frugal-web.onrender.com"]   # corrected at Step 22
```

> No SSH key yet? `ssh-keygen -t ed25519 -C "frugal-deploy"`, accept the
> defaults, then come back.

```bash
terraform init
terraform validate
terraform apply
```

**Read the plan before typing `yes`.** It should create one `Standard_B1s`, one
storage account and container, one VNet with a subnet and an NSG, one static
public IP, a Log Analytics workspace, a monitor agent extension, an action
group, four alerts, three role assignments, and a budget. If you see **Load
Balancer**, **Application Gateway**, **Azure Cache for Redis**, or **Azure
Database for PostgreSQL**, type `no` — those four are what turn $8/month into
$40/month.

Type `yes`. Takes about five minutes; most of it is the monitor agent
extension, and `apply` waits for it.

### Step 14 — Capture the outputs · 💻 Mac · same directory

```bash
export FRUGAL_IP="$(terraform output -raw public_ip)"
export FRUGAL_STORAGE="$(terraform output -raw storage_account)"
echo "IP=$FRUGAL_IP  STORAGE=$FRUGAL_STORAGE"
```

Copy both into your note as ⑤ and ④. **Keep this Terminal tab open** — those two
variables are used through Step 27, and they vanish if you close it.

Two things here are not errors:

- **A 403 from the storage account right now.** Role assignments take a minute
  or two to propagate. Wait, then retry before debugging anything.
- **No confirmation email to click.** Azure action groups are live as soon as
  they are created — there is no SNS-style subscription to confirm, so the
  failure mode is a typo in the address rather than a link nobody clicked.
  Step 28 is what catches that.

### Step 15 — Point a hostname at the VM · 🌐 Browser, then 💻 Mac

The VM needs a real hostname: Let's Encrypt will not issue a certificate for a
bare IP address, and the proxy hop from Render crosses the public internet
carrying bearer tokens, so plain HTTP is not acceptable.

🌐 [duckdns.org](https://duckdns.org) → sign in with GitHub → type a subdomain
such as `frugal-api` → **add domain**. Copy the **token** shown at the top.

💻 **Mac**, same tab as Step 14:

```bash
export DUCKDNS_TOKEN="paste-your-token"
export FRUGAL_HOST="frugal-api.duckdns.org"

curl -s "https://www.duckdns.org/update?domains=frugal-api&token=${DUCKDNS_TOKEN}&ip=${FRUGAL_IP}"
echo
dig +short "${FRUGAL_HOST}"
```

The `curl` prints `OK`, and `dig` must print exactly your `FRUGAL_IP`. Save the
hostname as ⑥.

> **The public IP is static and survives a reboot or a deallocate.** This is one
> place Azure is simply better than the AWS setup, which used an auto-assigned
> address that moved on stop/start because an Elastic IP billed hourly. Standard
> SKU costs the same either way here, so there was no reason to accept a moving
> address.

---

# Phase 5 — Deploy the backend

### Step 16 — Wait for cloud-init · 💻 Mac

```bash
ssh azureuser@${FRUGAL_IP} 'test -f /opt/frugal/.bootstrapped && echo ready'
```

Prints `ready` once the VM has installed Docker and built its swap file. Give it
2–3 minutes after `terraform apply`; retry until it prints. Accept the host
fingerprint when SSH asks.

### Step 17 — Get onto the server · 💻 Mac

```bash
ssh azureuser@${FRUGAL_IP}
```

Your prompt changes to `azureuser@frugal-app`. The next step runs there.

### Step 18 — Write the secrets file · 🖥️ Server

Secrets are created by hand on the VM and never deployed from the repository —
cloud-init `custom_data` is readable by anything on the box that can reach the
metadata service, so it is not a place for them either.

```bash
nano /opt/frugal/.env
```

Paste this, replacing each ① … ⑥ with the value from your note:

```
DATABASE_URL=①
DATABASE_DIRECT_URL=②
REDIS_URL=redis://redis:6379/0
JWT_SECRET=③
AZURE_STORAGE_ACCOUNT=④
AZURE_BLOB_CONTAINER=receipts
DOMAIN=⑥
CORS_ORIGINS=https://frugal-web.onrender.com
```

Save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**.

- `CORS_ORIGINS` is a guess until Step 21 tells you the real Render URL. Step 22
  corrects it — in two places now, because the storage account has its own CORS
  rule.
- Note what is **absent**: no storage credential of any kind. The adapter
  authenticates as the VM's system-assigned managed identity over IMDS and signs
  its upload URLs with a 24-hour user delegation key. The storage account has
  shared access keys **disabled outright**, so there is nothing to put here even
  if you wanted to — which is the one place this migration is a security
  improvement rather than a lateral move (ADR-010).

Lock it down, confirm it, and leave:

```bash
chmod 600 /opt/frugal/.env
cat /opt/frugal/.env
exit
```

`exit` returns you to your Mac.

### Step 19 — Deploy · 💻 Mac · in `REPO/infra/azure`

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/azure
./deploy.sh "${FRUGAL_IP}"
```

This rsyncs the source, builds the images **on the VM**, runs
`alembic upgrade head` against the direct endpoint, starts all five containers,
and polls `/health/ready` until it answers.

**The first run takes 10–20 minutes** — a `Standard_B1s` compiling OpenCV and
Prophet wheels is genuinely slow. It ends with `Deployed`.

### Step 20 — Verify the API from outside · 💻 Mac

```bash
curl -fsS "https://${FRUGAL_HOST}/health/ready"
```

Expect:

```json
{"status":"ready","dependencies":{"database":true,"redis":true}}
```

Run it from your Mac, not from the VM — this also proves the network security
group and the certificate, which a request from localhost would not.

- **Hangs or TLS error?** Caddy is still fetching its certificate. Wait a minute,
  then:
  `ssh azureuser@${FRUGAL_IP} 'cd /opt/frugal/infra/azure && docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env logs caddy'`
- **`"database":false`?** ① or ② is wrong — most likely the query string is still
  on the end. Re-read Step 7.
- **`ImportError: azure`?** The image was built without the `azure` extra.
  Rebuild with `--no-cache`; an older cached layer is the usual cause.

---

# Phase 6 — Deploy the frontend

### Step 21 — Create the Render service · 🌐 Browser

1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**.
2. **Connect** your GitHub account and pick the `frugal` repository.
3. Render reads [`render.yaml`](../render.yaml) and proposes one free web
   service called **frugal-web**. Give the blueprint any name.
4. It prompts for the two variables marked `sync: false`:

   | Key | Value |
   |---|---|
   | `BACKEND_ORIGIN` | `https://frugal-api.duckdns.org` — your ⑥, with `https://` |
   | `NEXT_PUBLIC_API_URL` | `https://frugal-web.onrender.com` — a placeholder for now |

5. **Apply** / **Create resources**. The first build takes 5–10 minutes.
6. When it goes live, copy the URL from the top of the service page. Save as ⑦.

### Step 22 — Reconcile the real URL · 🌐 Browser, then 💻 Mac

Render appends a suffix when a service name is taken, so ⑦ is often
`frugal-web-a1b2.onrender.com` rather than what you guessed. If ⑦ is **not**
exactly `https://frugal-web.onrender.com`, fix both ends.

🌐 **Browser** — Render → **frugal-web** → **Environment** → edit
`NEXT_PUBLIC_API_URL` to ⑦ → **Save changes** → **Manual Deploy** → **Deploy
latest commit**.

> A restart is not enough. `NEXT_PUBLIC_*` values are inlined into the client
> bundle at build time, so this needs a full rebuild.

💻 **Mac** — point the backend's CORS at the real origin:

```bash
ssh azureuser@${FRUGAL_IP} \
  "sed -i 's|^CORS_ORIGINS=.*|CORS_ORIGINS=https://YOUR-REAL-RENDER-HOST|' /opt/frugal/.env"

cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/azure
./deploy.sh "${FRUGAL_IP}"
```

This second deploy is fast — the images are cached.

**Then fix it in the second place**, which has no AWS counterpart. The browser
PUTs receipt bytes straight to Blob Storage, and Azure requires an
`x-ms-blob-type` header on that PUT. It is not a safelisted header, so every
upload preflights and the storage account's own CORS rule decides whether it is
allowed — S3 needed no bucket CORS policy at all.

```bash
cd terraform
$EDITOR terraform.tfvars     # cors_allowed_origins = ["https://YOUR-REAL-RENDER-HOST"]
terraform apply
```

Skip this and receipt uploads fail in the browser with a CORS error that reads
exactly like a broken URL. Step 24 will not catch it — registering an account
does not upload anything.

---

# Phase 7 — Verify

Not "the page loaded". These are the failures specific to this topology, and
each one is silent.

### Step 23 — Port 8000 is not public · 💻 Mac

```bash
curl -fsS "https://${FRUGAL_HOST}/health/ready" && echo "OK: TLS works"
curl -fsS --max-time 5 "http://${FRUGAL_IP}:8000/health/ready" && echo "LEAK: port 8000 is public"
```

The first must succeed, the second must fail. The API is never published to the
host — only Caddy reaches it.

### Step 24 — Requests are same-origin · 🌐 Browser

Open ⑦ → DevTools (**F12**) → **Network** → register an account.

Every API request must go to **`https://<your-render-host>/api/v1/...`**. If you
see `duckdns.org` in that column, `NEXT_PUBLIC_API_URL` is wrong — sessions will
not survive. Redo Step 22.

### Step 25 — The refresh cookie survives · 🌐 Browser

The whole reason for the proxy. With the app open:

DevTools → **Application** → **Cookies** → select your Render origin. There must
be a `frugal_refresh` cookie, `HttpOnly` ticked, path `/api/v1/auth`.

Then leave the tab open **more than 15 minutes** and click to another page. The
access token expires at 15 minutes; if you are bounced to the login screen, the
refresh cookie is not travelling.

### Step 26 — Rate limiting sees real clients · 💻 Mac

Every request now arrives via Render. If the original client address is lost,
all users are throttled as one.

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code} " -X POST \
    "https://YOUR-REAL-RENDER-HOST/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"email":"nobody@example.com","password":"wrong-password"}'
done; echo
```

Expect `401`s turning into `429`s. **Then open the site on a phone using mobile
data** and confirm you can still log in. If the phone is blocked too, the
left-most `X-Forwarded-For` entry reaching
[`client_ip`](../backend/app/core/dependencies.py) is Render's address rather
than the client's.

### Step 27 — Backups restore · 💻 Mac · in `REPO/infra/ops`

A backup that has never been restored is a hypothesis.

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/ops
export DATABASE_DIRECT_URL='②'
export AZURE_STORAGE_ACCOUNT="${FRUGAL_STORAGE}"
./backup.sh ~/frugal-backups
./restore.sh ~/frugal-backups/<newest-directory> --into-scratch
```

`--into-scratch` restores into a throwaway container, counts rows, and **exits
non-zero if the restore is clean but empty** — which is what a backup pointed at
the wrong database looks like at every other step.

### Step 28 — Fire an alert on purpose

Follow [RUNBOOK §4](../infra/azure/RUNBOOK.md). An alert nobody has triggered is
a configuration, not a safety net.

Start with §4.1, which sends a test notification through the action group. It
takes ten seconds and catches the failure this stack is most likely to have: a
typo in `alert_email`. Then do §4.2 — the 5xx alert — because its AWS
counterpart was silently broken for the life of that deployment, and confirming
this one works is the whole reason the log driver changed (ADR-010).

---

## Afterwards

- **Monthly:** Portal → **Cost Management + Billing** → **Credits**. Two
  minutes. Unlike a bill it tells you how much runway is *left* rather than how
  much is already gone, which is the number that matters on a fixed credit.
- **Deallocate the VM when nobody is looking at it.** `az vm deallocate -g
  frugal-rg -n frugal-app` costs nothing to do or undo and halves the burn rate.
  The public IP is static, so DNS survives it.
- **Answer the renewal email.** The student offer re-verifies academic status
  annually and continues while you are enrolled — but not automatically.
- **Re-run the load test against the deployed URL.**
  `infra/load/api-load-test.js` measured a laptop; NFR-1's 6× headroom says
  nothing about a `Standard_B1s` talking to Neon across a network.
- **The first request after idle takes ~1 minute.** Render free services sleep
  after 15 minutes. The 750 instance-hours are a workspace-wide budget, so a
  second free service shares them.
- **Redeploying after a code change:** push to GitHub — Render rebuilds the
  frontend by itself. The backend needs
  `cd REPO/infra/azure && ./deploy.sh "${FRUGAL_IP}"`.

Logs, restarts, migration status, and teardown are in
[RUNBOOK §3](../infra/azure/RUNBOOK.md). If you are coming from the AWS
deployment, [RUNBOOK §7](../infra/azure/RUNBOOK.md) covers decommissioning it —
final backup first, and not on the same day you cut over.
