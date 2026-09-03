# Logs, metrics, and alerts.
#
# The CloudWatch setup this replaces had a gap worth naming: its agent config
# tailed /var/log/frugal/app.log, and nothing ever wrote that file -- the
# containers logged to Docker's json-file driver. Application logs never
# reached CloudWatch at all, and the 5xx metric filter therefore matched
# nothing. The fix here is the `journald` log driver in docker-compose.prod.yml,
# which puts container stdout into the systemd journal, which the Azure Monitor
# agent collects for real.

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.prefix}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags

  # PerGB2018 is the only sane choice at this size. It carries a standing free
  # allowance of 5 GB of ingestion per month; the commitment tiers start at
  # 100 GB/day and are billed whether used or not.
  sku               = "PerGB2018"
  retention_in_days = var.log_retention_days

  # A hard ceiling, in GB/day. Without it a container stuck in a crash loop
  # ingests without bound and spends the credit on its own error messages.
  # Ingestion stops for the rest of the day when this is hit, which is the
  # right failure: logs are the thing you can afford to lose.
  daily_quota_gb = 1
}

# --- the agent --------------------------------------------------------------
# Azure Monitor Agent replaces the CloudWatch agent. What it collects is
# declared out-of-band in a Data Collection Rule rather than in a file on the
# box, so changing it does not mean reprovisioning the VM.

resource "azurerm_virtual_machine_extension" "monitor_agent" {
  name                       = "AzureMonitorLinuxAgent"
  virtual_machine_id         = azurerm_linux_virtual_machine.app.id
  publisher                  = "Microsoft.Azure.Monitor"
  type                       = "AzureMonitorLinuxAgent"
  type_handler_version       = "1.33"
  auto_upgrade_minor_version = true
  automatic_upgrade_enabled  = true
  tags                       = local.tags
}

resource "azurerm_monitor_data_collection_rule" "app" {
  name                = "${var.prefix}-dcr"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags

  destinations {
    log_analytics {
      workspace_resource_id = azurerm_log_analytics_workspace.main.id
      name                  = "logs"
    }
  }

  # Disk and memory, which the hypervisor cannot see from outside the guest --
  # the same reason the CloudWatch agent existed. Every counter here is a row
  # in the workspace, so the list is the two the alerts below actually use plus
  # nothing.
  data_flow {
    streams      = ["Microsoft-Perf"]
    destinations = ["logs"]
  }

  data_flow {
    streams      = ["Microsoft-Syslog"]
    destinations = ["logs"]
  }

  data_sources {
    performance_counter {
      name = "host-health"
      # 300s, matching the AWS agent. A 60s interval would be six times the
      # ingestion for a signal that changes over hours.
      sampling_frequency_in_seconds = 300
      streams                       = ["Microsoft-Perf"]
      counter_specifiers = [
        "Logical Disk(*)\\% Free Space",
        "Memory(*)\\% Used Memory",
      ]
    }

    syslog {
      name    = "container-logs"
      streams = ["Microsoft-Syslog"]
      # `daemon` is where the journald Docker driver writes container stdout.
      # `user` and `syslog` carry the host's own messages, which is where an
      # OOM kill or a failed unit shows up.
      facility_names = ["daemon", "user", "syslog"]
      log_levels     = ["Warning", "Error", "Critical", "Alert", "Emergency"]
    }
  }
}

resource "azurerm_monitor_data_collection_rule_association" "app" {
  name                    = "${var.prefix}-dcr-link"
  target_resource_id      = azurerm_linux_virtual_machine.app.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.app.id

  # The agent must be installed before a rule can be attached to it.
  depends_on = [azurerm_virtual_machine_extension.monitor_agent]
}

# --- delivery ---------------------------------------------------------------

resource "azurerm_monitor_action_group" "alerts" {
  name                = "${var.prefix}-alerts"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "frugal"
  tags                = local.tags

  email_receiver {
    name          = "operator"
    email_address = var.alert_email

    # Azure's own schema rather than the legacy one. The legacy format omits
    # the alert's dimensions, so a disk alert arrives without saying which
    # disk -- which on a one-disk host is survivable and on anything else is
    # an email that tells you nothing.
    use_common_alert_schema = true
  }
}

# --- alerts -----------------------------------------------------------------

resource "azurerm_monitor_metric_alert" "vm_unavailable" {
  name                = "${var.prefix}-vm-unavailable"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_linux_virtual_machine.app.id]
  description         = "The VM is not responding to the platform's health probe. Usually a kernel panic or an out-of-memory kill."
  severity            = 1
  frequency           = "PT1M"
  window_size         = "PT5M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.Compute/virtualMachines"
    # The direct equivalent of EC2's StatusCheckFailed. 1 is healthy, 0 is not.
    metric_name = "VmAvailabilityMetric"
    aggregation = "Average"
    operator    = "LessThan"
    threshold   = 1
  }

  action {
    action_group_id = azurerm_monitor_action_group.alerts.id
  }
}

resource "azurerm_monitor_metric_alert" "cpu_credits_low" {
  name                = "${var.prefix}-cpu-credits-low"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_linux_virtual_machine.app.id]
  description         = "Burstable CPU credits are nearly gone. At zero the VM is throttled to its baseline (10% of a core on B1s) and the API becomes very slow."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.Compute/virtualMachines"
    metric_name      = "CPU Credits Remaining"
    aggregation      = "Minimum"
    operator         = "LessThan"
    threshold        = 20
  }

  action {
    action_group_id = azurerm_monitor_action_group.alerts.id
  }

  # No Azure equivalent of the t3 `unlimited` trap this guarded against on AWS:
  # B-series VMs throttle at zero credits and cannot bill for surplus. So this
  # is purely a performance warning now, not a cost one -- which is why its
  # severity is lower than the AWS alarm's.
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "disk_nearly_full" {
  name                = "${var.prefix}-disk-nearly-full"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  scopes              = [azurerm_log_analytics_workspace.main.id]
  description         = "The root filesystem is nearly full. Docker images and logs are the usual cause."
  severity            = 2
  tags                = local.tags

  evaluation_frequency = "PT15M"
  window_duration      = "PT30M"

  criteria {
    # Free space, so the comparison is inverted relative to the AWS alarm's
    # `used_percent > 85`.
    query                   = <<-KQL
      Perf
      | where ObjectName == "Logical Disk" and CounterName == "% Free Space"
      | where InstanceName == "/"
      | summarize FreePercent = min(CounterValue) by bin(TimeGenerated, 15m)
      | where FreePercent < 15
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = [azurerm_monitor_action_group.alerts.id]
  }

  # Depends on the agent being alive. If it dies there is no data and this
  # stays quiet -- "no data" here means "unknown", not "full", unlike the
  # availability alert above where silence is itself the symptom. The
  # vm_unavailable alert is what covers a host that has stopped reporting.
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "server_errors" {
  name                = "${var.prefix}-5xx-errors"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  scopes              = [azurerm_log_analytics_workspace.main.id]
  description         = "The API is returning 500s. One is noise; a sustained rate is an incident."
  severity            = 1
  tags                = local.tags

  evaluation_frequency = "PT5M"
  window_duration      = "PT15M"

  criteria {
    # Matches the JSON the application already emits (app/core/logging.py),
    # arriving through journald as the syslog message body. Parsing it in the
    # query rather than publishing a custom metric from the app: same signal,
    # no per-metric charge, and nothing to keep in sync in the code.
    query                   = <<-KQL
      Syslog
      | where SyslogMessage has "status_code"
      | extend parsed = parse_json(extract(@"\{.*\}", 0, SyslogMessage))
      | extend status_code = toint(parsed.status_code)
      | where status_code >= 500
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 5
  }

  action {
    action_groups = [azurerm_monitor_action_group.alerts.id]
  }
}
