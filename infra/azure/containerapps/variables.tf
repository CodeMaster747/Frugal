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
    Region.

    `centralindia` matches where the VM deployment ran and where the users are.
    Container Apps is not available in every region; if an apply fails with a
    location error, `az provider show -n Microsoft.App --query "resourceTypes[?resourceType=='managedEnvironments'].locations"`
    lists the ones that work.
  EOT
  type        = string
  default     = "centralindia"
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
