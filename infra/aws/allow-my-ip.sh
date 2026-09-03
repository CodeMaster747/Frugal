#!/usr/bin/env bash
#
# Point the SSH rule at whatever address your ISP has given you now.
#
#   ./allow-my-ip.sh
#
# Your address is dynamic, and when it moves port 22 stops answering while 443
# keeps serving -- which looks like the instance is down and is not. This is the
# whole remedy, in one command, because doing it by hand means editing a tfvars
# and reading a plan every time, and the plan is where a region mistake once
# turned "update a firewall rule" into "rebuild the stack in Mumbai".
#
# It refuses to apply anything except the security group. If Terraform wants to
# touch anything else, that is a signal worth reading, not a prompt to click
# through.
set -euo pipefail
cd "$(dirname "$0")/terraform"

MYIP=$(curl -fsS --max-time 15 https://checkip.amazonaws.com)
[[ "$MYIP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Could not determine your IP: $MYIP" >&2; exit 1; }

CURRENT=$(grep -E '^ssh_ingress_cidr' terraform.tfvars | sed 's/.*"\(.*\)".*/\1/')
echo "current allowlist : ${CURRENT}"
echo "your address now  : ${MYIP}/32"

if [ "${CURRENT}" = "${MYIP}/32" ]; then
  echo "Already correct. If SSH still fails, the cause is not the firewall."
  exit 0
fi

sed -i.bak "s|^ssh_ingress_cidr.*|ssh_ingress_cidr = \"${MYIP}/32\"|" terraform.tfvars
rm -f terraform.tfvars.bak

# Read the plan before applying it. `-detailed-exitcode` gives 2 for "changes
# present", which is the expected case here.
terraform plan -input=false -no-color -out=.tfplan >/dev/null 2>&1 || true
CHANGES=$(terraform show -no-color .tfplan | grep -cE '^  # .* will be' || true)
TOUCHES=$(terraform show -no-color .tfplan | grep -E '^  # .* will be' || true)

echo
echo "${TOUCHES}"
echo

if [ "${CHANGES}" != "1" ] || ! echo "${TOUCHES}" | grep -q 'aws_security_group.app will be updated in-place'; then
  rm -f .tfplan
  cat >&2 <<'EOF'
Refusing to apply.

Expected exactly one change -- the security group, updated in place. Terraform
wants to do something else, and the most likely reason is that it is looking in
the wrong region: `region` in variables.tf defaults to ap-south-1 while this
stack lives in ap-southeast-1. Check that terraform.tfvars still pins it.

A plan that offers to *create* an instance is Terraform telling you it cannot
see the one you already have.
EOF
  exit 1
fi

terraform apply -input=false .tfplan
rm -f .tfplan
echo
echo "SSH now allowed from ${MYIP}/32"
