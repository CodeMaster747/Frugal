output "public_ip" {
  description = "The VM's public address. Static -- it survives a reboot, unlike the AWS deployment's."
  value       = azurerm_public_ip.app.ip_address
}

output "ssh" {
  description = "Ready-to-paste SSH command."
  value       = "ssh ${var.admin_username}@${azurerm_public_ip.app.ip_address}"
}

output "storage_account" {
  description = "Blob storage account. Goes into AZURE_STORAGE_ACCOUNT on the VM."
  value       = azurerm_storage_account.receipts.name
}

output "blob_container" {
  description = "Container holding receipt images. Goes into AZURE_BLOB_CONTAINER."
  value       = azurerm_storage_container.receipts.name
}

output "log_analytics_workspace" {
  description = "Where application and host logs land."
  value       = azurerm_log_analytics_workspace.main.name
}

output "vm_principal_id" {
  description = "The VM's managed identity. This is what holds the blob role assignments -- no keys anywhere."
  value       = azurerm_linux_virtual_machine.app.identity[0].principal_id
}

output "next_steps" {
  description = "What to do after apply."
  value       = <<-EOT
    1. Point DNS at ${azurerm_public_ip.app.ip_address}, then set DOMAIN in
       /opt/frugal/.env. Caddy cannot issue a certificate for a bare address.
    2. Create /opt/frugal/.env by hand -- see RUNBOOK.md section 2. Secrets are
       never in this repository and never in custom_data, which is readable by
       anything on the VM that can reach IMDS.
    3. Role assignments take a minute or two to propagate. A 403 from the
       storage account immediately after apply is usually that, not a
       misconfiguration -- retry before debugging.
    4. Verify an alert actually fires (RUNBOOK.md section 4), rather than
       assuming it will.
  EOT
}
