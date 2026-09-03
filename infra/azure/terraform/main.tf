# Compute and network.
#
# A purpose-built VNet rather than a default one, because Azure has no default
# VNet to borrow -- but the shape is the same single public subnet the AWS
# module used, and for the same reason: a private tier would need a NAT Gateway
# (~$32/month plus data, billed idle) and Frugal has exactly one public host.
#
# There is no load balancer, again deliberately. A Standard Load Balancer is
# ~$18/month before it carries a byte. Caddy on the VM terminates TLS, which is
# what a single-instance deployment actually needs.
#
# The one unavoidable standing cost is the public IP: Basic SKU was retired in
# September 2025, so Standard is the only option and it bills ~$3.65/month
# whether or not traffic flows. That is the price of being reachable, and it is
# spent from credit rather than billed.

locals {
  tags = {
    project    = var.prefix
    managed_by = "terraform"
  }
}

resource "azurerm_resource_group" "main" {
  name     = "${var.prefix}-rg"
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "main" {
  name                = "${var.prefix}-vnet"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.20.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "app" {
  name                 = "${var.prefix}-app"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.20.1.0/24"]
}

# --- firewall ---------------------------------------------------------------
# Azure NSGs deny inbound by default through a rule at priority 65500, so only
# the three openings below need stating. Lower priority numbers win.

resource "azurerm_network_security_group" "app" {
  name                = "${var.prefix}-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags

  security_rule {
    name                       = "ssh-from-operator"
    description                = "SSH, restricted to one address by the variable's validation"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.ssh_source_address
    destination_address_prefix = "*"
  }

  security_rule {
    name = "http-acme"
    # 80 is not for serving. Caddy needs it to answer the ACME HTTP-01
    # challenge, and it redirects everything else to 443.
    description                = "HTTP, for the ACME challenge and the redirect to HTTPS"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "https"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "app" {
  subnet_id                 = azurerm_subnet.app.id
  network_security_group_id = azurerm_network_security_group.app.id
}

# --- address ----------------------------------------------------------------

resource "azurerm_public_ip" "app" {
  name                = "${var.prefix}-ip"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags

  # Static, unlike the AWS deployment's auto-assigned address.
  #
  # On AWS a dynamic address was free and an Elastic IP billed, so the address
  # was allowed to change on stop/start and the runbook covered it. Azure
  # prices Standard SKU identically either way, so there is no longer a reason
  # to accept a moving address -- and a stable one means the DNS record set
  # after `apply` stays correct across a reboot.
  allocation_method = "Static"
  sku               = "Standard"
}

resource "azurerm_network_interface" "app" {
  name                = "${var.prefix}-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.app.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.app.id
  }
}

# --- the VM -----------------------------------------------------------------

resource "azurerm_linux_virtual_machine" "app" {
  name                  = "${var.prefix}-app"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = var.vm_size
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.app.id]
  tags                  = local.tags

  # Keys only. Azure defaults this to true for Linux images, but stating it
  # means a future edit that adds a password cannot silently also enable
  # password auth on a host with port 22 open.
  disable_password_authentication = true

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"

    # 32, not 30. Azure bills managed disks by tier, and 30 GiB lands in the
    # same E4 (32 GiB) tier as 32 does -- asking for 30 pays for 32 and uses
    # 30. Premium SSD would be faster and roughly four times the price for a
    # workload whose disk is idle between deploys.
    disk_size_gb = 32
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    # Resolved at apply time rather than pinned. A stale hardcoded image is a
    # machine that launches years behind on security patches.
    version = "latest"
  }

  # The equivalent of the EC2 instance profile: an identity Azure issues and
  # rotates, reachable only from inside this VM over IMDS. Nothing durable to
  # leak into a repository or a log, which is the failure mode that actually
  # produces incidents.
  identity {
    type = "SystemAssigned"
  }

  # templatefile, not file: the admin username is a variable and the bootstrap
  # needs it to add the right account to the docker group.
  custom_data = base64encode(templatefile("${path.module}/cloud-init.sh", {
    admin_username = var.admin_username
  }))

  # Serial console and boot screenshots, into a managed storage account Azure
  # provides at no charge. This is the only way to see a VM that fails before
  # sshd starts -- which is exactly when you need it.
  boot_diagnostics {}

  lifecycle {
    # `latest` resolves to a newer image whenever Canonical publishes one.
    # Without this an unrelated `terraform apply` would destroy and rebuild the
    # running VM to adopt it.
    ignore_changes = [source_image_reference]
  }
}
