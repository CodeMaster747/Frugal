variable "subscription_id" {
  description = "Azure subscription. Empty falls back to ARM_SUBSCRIPTION_ID."
  type        = string
  default     = ""
}

variable "prefix" {
  description = "Name prefix for every resource. Keeps this deployment distinct from the VM module's `frugal-*`."
  type        = string
  default     = "frugal-ca"
}

variable "location" {
  description = <<-EOT
    Region. **Indonesia Central, and the reasons are all measured rather than
    preferred.**

    1. The subscription's "Allowed resource deployment regions" policy permits
       exactly five: centralindia, austriaeast, uaenorth, eastasia,
       indonesiacentral. Anything else -- Southeast Asia included -- is denied
       before the resource provider sees the request.
    2. Central India, the obvious choice and where the VM ran, refuses Container
       Apps environments on this subscription with
       `MaxNumberOfEnvironmentsInSubExceeded` -- even though the usages API
       (`Microsoft.App/locations/{loc}/usages`) reports 0/1 there, the same as
       everywhere else. Azure for Students carries a per-region block that API
       does not expose, so a clean quota number is not evidence an apply will
       succeed.
    3. Of the remaining four, Jakarta is closest to both halves of the data
       path: Neon runs in ap-southeast-1 (Singapore) and Render in Singapore.
       Central India was always the far side of both.

    If an apply here also returns that 409, try eastasia, then uaenorth, then
    austriaeast -- in that order of distance to Singapore. A refused
    environment is never created, so a failed attempt costs nothing.
  EOT
  type        = string
  default     = "indonesiacentral"
}

variable "image" {
  description = <<-EOT
    Fully qualified API image.

    GitHub Container Registry rather than ACR, and that is a cost decision
    rather than a preference: ACR Basic is $5/month, which is more than this
    entire deployment is budgeted for. A public GHCR package needs no
    registry credentials on the Container App at all.
  EOT
  type        = string
  default     = "ghcr.io/codemaster747/frugal-api:latest"
}

variable "frontend_origin" {
  description = "Public URL of the frontend, for CORS."
  type        = string
  default     = "https://frugal-web.onrender.com"
}

variable "worker_image" {
  description = <<-EOT
    Fully qualified worker image, used by the receipts jobs.

    Separate from `image` because it is a different build: `auth,azure,ocr`
    plus tesseract and OpenCV's system libraries, against the API image's
    `auth,azure`. About 944 MB versus a few hundred, which is why the API does
    not simply use this one -- its cold start is on the request path and the
    jobs' is not.

    Pin a commit SHA for the same reason as `image`: Container Apps does not
    re-pull a tag that moved, so `:latest` silently keeps whatever was there.
  EOT
  type        = string
  default     = "ghcr.io/codemaster747/frugal-worker:latest"
}

variable "jobs_environment_name" {
  description = <<-EOT
    Name of the **standard** Container Apps environment that holds the jobs.

    Referenced as a data source, never created here, and that is not a
    stylistic choice: the property that matters is `properties.environmentMode`,
    and azurerm 4.81 exposes no argument for it. Only
    `az containerapp env create --environment-mode WorkloadProfiles` can set it.

    A `workload_profile` block is not the equivalent -- the Express environment
    already has an identical `workloadProfiles: [Consumption]` array. That is
    not what makes an environment Express.

    `terraform apply` fails until this environment exists. RUNBOOK.md has the
    one command that creates it.
  EOT
  type        = string
  default     = "frugal-ca-jobs-env"
}

variable "receipts_cron" {
  description = <<-EOT
    How often the receipt drain runs.

    Every thirty minutes by default, which puts the three scheduled jobs at
    roughly 21% of the monthly free grant. `*/15` doubles the receipts share
    for a latency win that the manual `receipts-now` job already provides on
    demand.

    The estimate behind that 21% assumes ~40s per execution *including* pulling
    a 944 MB image on a cold start, and it is an estimate. Measure with
    `az containerapp job execution list` before tightening this.
  EOT
  type        = string
  default     = "*/30 * * * *"
}

# --- secrets -----------------------------------------------------------------
#
# These are the four values that cannot be defaulted, and none of them are ever
# written to this repository. Supply them from a gitignored terraform.tfvars, or
# from TF_VAR_* in the environment.

variable "database_url" {
  description = "Neon Postgres, async driver: postgresql+asyncpg://user:pass@host/db"
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Upstash Redis, TLS: rediss://default:pass@host:6379"
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "Signing key for access tokens. 32+ random bytes."
  type        = string
  sensitive   = true
}

variable "contribution_pepper" {
  description = <<-EOT
    Pepper for `contributor_hash` (ADR-013).

    Rotating this silently breaks the one-contribution-per-day constraint,
    because every existing hash was computed with the old value. Set it once
    and keep it. Empty falls back to jwt_secret, which is what the VM
    deployment did.
  EOT
  type        = string
  sensitive   = true
  default     = ""
}

variable "receipt_expiry_days" {
  description = <<-EOT
    Days before a receipt image is deleted.

    The extracted data outlives the photograph: merchant, amount, date, line
    items and the transaction it became all live in Postgres and are unaffected.
    Ninety days is long enough to re-run OCR after a parser fix and short enough
    that the blob bill stays rounding error.
  EOT
  type        = number
  default     = 90
}
