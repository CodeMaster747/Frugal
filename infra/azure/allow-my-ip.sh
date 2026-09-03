#!/usr/bin/env bash
#
# Point the SSH rule at your current address.
#
#   ./allow-my-ip.sh
#
# Home connections move. When yours does, SSH stops answering and everything
# else keeps working -- which reads as a broken VM and is not one. This is the
# fix, and deploy.sh names it in the error you will actually hit.

set -euo pipefail

PREFIX="${PREFIX:-frugal}"
RG="${RG:-${PREFIX}-rg}"
NSG="${NSG:-${PREFIX}-nsg}"
RULE="ssh-from-operator"

MY_IP="$(curl -fsS https://ifconfig.me)" || {
  echo "Could not determine your public address." >&2
  exit 1
}

# A bare v4 address. If your ISP handed you IPv6 the NSG rule below is still
# v4-only, and the rewrite would silently produce a rule matching nothing.
if [[ ! "${MY_IP}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Got '${MY_IP}', which is not an IPv4 address." >&2
  echo "Add an IPv6 rule by hand, or connect over v4." >&2
  exit 1
fi

CURRENT="$(az network nsg rule show \
  --resource-group "${RG}" --nsg-name "${NSG}" --name "${RULE}" \
  --query sourceAddressPrefix -o tsv)"

if [[ "${CURRENT}" == "${MY_IP}" ]]; then
  echo "Already ${MY_IP}. Nothing to do."
  echo
  echo "If SSH is still refused, the cause is not your address. Check that the"
  echo "VM is running rather than deallocated -- a student subscription that"
  echo "has spent its credit deallocates rather than bills:"
  echo "  az vm get-instance-view -g ${RG} -n ${PREFIX}-app --query instanceView.statuses[1].displayStatus -o tsv"
  exit 0
fi

echo "Moving SSH access from ${CURRENT} to ${MY_IP}"
az network nsg rule update \
  --resource-group "${RG}" --nsg-name "${NSG}" --name "${RULE}" \
  --source-address-prefixes "${MY_IP}" \
  --output none

echo "Done."
echo
echo "Terraform still has the old address in its state, so the next"
echo "\`terraform apply\` will move it back. Update ssh_source_address in"
echo "terraform.tfvars to ${MY_IP} to make this stick."
