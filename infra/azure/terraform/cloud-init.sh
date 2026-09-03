#!/usr/bin/env bash
#
# First-boot bootstrap for the Frugal VM.
#
# Installs the runtime and leaves the machine ready for a deploy. It does not
# fetch the application: that needs secrets, and custom_data is stored
# unencrypted and readable by anything on the VM that can reach the instance
# metadata service. Deployment is a separate, authenticated step (deploy.sh).
#
# Output lands in /var/log/cloud-init-output.log.

set -euxo pipefail

ADMIN_USER="${admin_username}"

# --- swap -------------------------------------------------------------------
# B1s has 1 GB, the same as the t3.micro this replaces. A Prophet fit peaks
# near 450 MB and OpenCV's preprocessing is comparable, so a worker and the API
# together will touch the ceiling. Swap turns an out-of-memory kill -- which
# takes the whole container down and shows up as a failed health probe -- into
# a slow request.
#
# Azure attaches an ephemeral resource disk at /mnt on most sizes and cloud-init
# will happily put swap there, which is wrong: that disk is wiped on
# deallocation. This swapfile is on the OS disk and survives.

if [[ ! -f /swapfile ]]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >>/etc/fstab

  # Default is 60, which swaps eagerly. 10 keeps swap as the fallback it is
  # meant to be here rather than something the kernel reaches for routinely.
  sysctl -w vm.swappiness=10
  echo 'vm.swappiness=10' >/etc/sysctl.d/99-frugal-swap.conf
fi

# --- docker -----------------------------------------------------------------

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg unattended-upgrades

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
  gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  >/etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

usermod -aG docker "$ADMIN_USER"
systemctl enable --now docker

# Container logs go to the systemd journal rather than json-file.
#
# This is the one deliberate difference from the AWS bootstrap, and it fixes a
# real gap: there, the CloudWatch agent was configured to tail
# /var/log/frugal/app.log, nothing ever wrote that file, and so no application
# log ever reached CloudWatch. The Azure Monitor agent collects the journal,
# so routing container stdout into it makes the 5xx alert match real data
# instead of nothing.
#
# `docker logs` still works -- journald is one of the drivers that supports it.
cat >/etc/docker/daemon.json <<'JSON'
{
  "log-driver": "journald"
}
JSON
systemctl restart docker

# The json-file driver's max-size/max-file caps went away with it, so the
# journal needs its own ceiling or a chatty container fills a 32 GB disk and
# takes the application down -- which is the likeliest way the disk alert
# fires.
mkdir -p /etc/systemd/journald.conf.d
cat >/etc/systemd/journald.conf.d/99-frugal.conf <<'JCONF'
[Journal]
Storage=persistent
SystemMaxUse=512M
SystemMaxFileSize=64M
MaxRetentionSec=7day
JCONF
systemctl restart systemd-journald

# --- unattended security updates -------------------------------------------
# Security patches only, and no automatic reboot: an unannounced reboot during
# a Celery task is worse than a patch applied a day late.

cat >/etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

# --- application directory --------------------------------------------------
# No monitoring agent is installed here. On AWS the CloudWatch agent was a
# curl-and-dpkg in this script, which meant a broken install was invisible
# until an alarm quietly reported "no data". Azure Monitor Agent is a VM
# extension managed by Terraform instead, so its health is a property of the
# resource and shows up in `terraform plan`.

mkdir -p /opt/frugal
chown "$ADMIN_USER:$ADMIN_USER" /opt/frugal

echo "bootstrap complete: $(date -Is)" >/opt/frugal/.bootstrapped
