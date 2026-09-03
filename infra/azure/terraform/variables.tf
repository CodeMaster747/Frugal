variable "subscription_id" {
  description = <<-EOT
    Azure subscription id. Leave empty to fall back to ARM_SUBSCRIPTION_ID.

    On an Azure for Students subscription this is the id shown by
    `az account show --query id -o tsv`. Getting it wrong deploys into whatever
    subscription happens to be active, which on a shared tenant may be one that
    actually bills.
  EOT
  type        = string
  default     = ""
}

variable "location" {
  description = <<-EOT
    Azure region.

    Southeast Asia (Singapore), matching the ap-southeast-1 the AWS deployment
    actually ran in -- not the ap-south-1 its variables.tf defaulted to. The
    frontend is a Render service in Singapore (render.yaml) and proxies every
    /api call to this host, so the distance between the two is paid on every
    single request. Moving the API to India while the frontend stays in
    Singapore would add a round trip per call for no benefit.

    Central India is the better choice only if the frontend moves there too.

    Student subscriptions carry per-region vCPU quotas that are sometimes zero
    in the busiest regions. Check before applying:

      az vm list-usage --location centralindia -o table | grep -i "B.* Series"
  EOT
  type        = string
  default     = "centralindia"
}

variable "prefix" {
  description = "Name prefix for every resource. Also the tag the teardown and the budget select on."
  type        = string
  default     = "frugal"
}

variable "vm_size" {
  description = <<-EOT
    VM size.

    Constrained by the validation below for the same reason the AWS module
    constrained instance_type: the difference between B1s and B2s is not
    visible when typing it, and is the difference between roughly $7/month and
    roughly $30/month against a $100 credit that has to last a year.

    B1s is 1 vCPU / 1 GB -- the same shape as the t3.micro this replaces, which
    the 2 GB swap in cloud-init.yaml was already sized against.
  EOT
  type        = string
  default     = "Standard_B2ts_v2"

  validation {
    condition     = contains(["Standard_B2ts_v2", "Standard_B2ls_v2", "Standard_B1s", "Standard_B1ms", "Standard_B2ats_v2"], var.vm_size)
    error_message = "Use a burstable B-series size. Anything else spends the student credit in weeks rather than months."
  }
}

variable "admin_username" {
  description = "Login user on the VM. Azure forbids `admin` and `root`."
  type        = string
  default     = "azureuser"

  validation {
    condition     = !contains(["admin", "administrator", "root"], lower(var.admin_username))
    error_message = "Azure rejects these usernames at create time."
  }
}

variable "ssh_public_key" {
  description = "Contents of your SSH public key (~/.ssh/id_ed25519.pub). Azure holds only the public half."
  type        = string
}

variable "ssh_source_address" {
  description = <<-EOT
    Who may reach port 22, as `x.x.x.x` or `x.x.x.x/32`.

    No default on purpose, and `*` is refused below. An SSH port open to the
    internet is found by scanners within hours, and on a subscription with a
    fixed credit balance a mining workload does not produce a bill -- it
    silently consumes the year's budget instead, which is harder to notice and
    impossible to reverse.
  EOT
  type        = string

  validation {
    condition     = !contains(["*", "0.0.0.0/0", "Internet", "Any"], var.ssh_source_address)
    error_message = "Refusing to open SSH to the whole internet. Pass your own address (`curl -s ifconfig.me`)."
  }
}

variable "log_retention_days" {
  description = <<-EOT
    Log Analytics retention.

    30 is the minimum the workspace accepts and is also the point below which
    retention is free -- data kept past 31 days is billed per GB-month, which
    is the quiet way an observability setup outlives and outspends the VM it
    watches.
  EOT
  type        = number
  default     = 30

  validation {
    condition     = var.log_retention_days >= 30 && var.log_retention_days <= 730
    error_message = "Log Analytics accepts 30-730 days. Below 31 days is the free tier."
  }
}

variable "receipt_expiry_days" {
  description = "Days before an uploaded receipt image is deleted from Blob Storage. The extracted fields live in Postgres; the image is only needed while a human might review it."
  type        = number
  default     = 90
}

variable "alert_email" {
  description = <<-EOT
    Where Azure Monitor alerts are sent.

    Unlike SNS, Azure does not require a confirmation click -- the action group
    is live as soon as it is created. The runbook still verifies delivery,
    because an address with a typo fails exactly as silently either way.
  EOT
  type        = string
}

variable "monthly_budget" {
  description = <<-EOT
    Budget threshold in USD, for the alert only.

    Azure budgets notify; they do not stop anything, exactly as AWS budgets do
    not. On this subscription that is acceptable, because the real control is
    the offer itself: with no payment instrument attached, the subscription is
    disabled when the credit is spent rather than billed. This is an early
    warning that the credit is going faster than a year's runway allows.

    $8.33 is a twelfth of the $100 student credit.
  EOT
  type        = number
  default     = 9
}

variable "cors_allowed_origins" {
  description = <<-EOT
    Origins allowed to PUT receipt bytes straight to Blob Storage.

    The frontend's public URL, e.g. ["https://frugal-web.onrender.com"]. This
    has no S3 counterpart in the AWS module because that deployment never set a
    bucket CORS policy -- uploads worked from the browser because S3 permits a
    signed PUT without preflight when no unsafe header is sent. Azure requires
    `x-ms-blob-type`, which *is* unsafe, so every upload preflights and a
    missing origin here fails as a CORS error that looks like a bad URL.

    Never `["*"]`: a wildcard lets any page a user visits replay a leaked SAS.
  EOT
  type        = list(string)

  validation {
    condition     = !contains(var.cors_allowed_origins, "*")
    error_message = "Refusing a wildcard CORS origin. List the frontend's URL explicitly."
  }
}
