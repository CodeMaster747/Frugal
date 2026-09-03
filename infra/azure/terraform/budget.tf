# Cost alerting.
#
# Read infra/azure/COST-SAFETY.md before assuming this protects anything. It
# notifies; it does not stop. That is acceptable here only because the real
# control is the subscription itself: Azure for Students has no payment
# instrument attached, so when the credit is spent the subscription is
# *disabled* rather than billed. This budget exists to tell you the year's
# runway is being consumed faster than a year, while there is still time to act.

resource "azurerm_consumption_budget_resource_group" "main" {
  name              = "${var.prefix}-budget"
  resource_group_id = azurerm_resource_group.main.id

  amount     = var.monthly_budget
  time_grain = "Monthly"

  time_period {
    # Azure requires a start date on the first of a month, at or after the
    # current month. Computed rather than hardcoded so this does not become
    # invalid the moment the month turns.
    start_date = formatdate("YYYY-MM-01'T'00:00:00'Z'", timestamp())
  }

  # Scoped to the resource group rather than the subscription, deliberately.
  # A subscription-scoped budget needs permissions a student account may not
  # have on a university tenant, and everything this deployment creates lives
  # in one resource group anyway -- so the two scopes measure the same spend.

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.alert_email]
  }

  notification {
    enabled   = true
    threshold = 100
    operator  = "GreaterThan"
    # Forecast, not actual: by the time actual spend crosses the month's budget
    # the money is already gone. This is the one that arrives early enough to
    # matter.
    threshold_type = "Forecasted"
    contact_emails = [var.alert_email]
  }

  lifecycle {
    # `timestamp()` above changes on every plan, which would otherwise show
    # this resource as needing replacement forever.
    ignore_changes = [time_period]
  }
}
