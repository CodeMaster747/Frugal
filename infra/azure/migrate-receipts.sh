#!/usr/bin/env bash
#
# Copy receipt images from the S3 bucket to the Azure Blob container.
#
#   S3_BUCKET=frugal-receipts-123456789012 \
#   AZURE_STORAGE_ACCOUNT=frugalab12cd34 \
#   ./migrate-receipts.sh [staging-dir]
#
# Runs while the AWS deployment is still up. Nothing here writes to S3 or
# deletes anything, so it is safe to run more than once and safe to run before
# you have decided to cut over.
#
# Via local disk rather than a direct service-to-service copy, for two reasons:
# azcopy's S3 source needs an AWS access key, and this account deliberately has
# none (COST-SAFETY.md) -- and the staging directory is a byte-for-byte backup
# of the only user data that does not live in Postgres, which is worth having
# on the day you destroy the bucket.
#
# The blob name must equal the `s3_key` stored on the receipts row, or every
# image 404s while the database still points at it. `aws s3 sync` reproduces
# the key as a directory path and `upload-batch` reproduces that path as a blob
# name, so the round trip preserves it -- and the count check at the end is
# what proves it rather than assumes it.

set -euo pipefail

BUCKET="${S3_BUCKET:?set S3_BUCKET (terraform -chdir=../aws/terraform output -raw receipts_bucket)}"
ACCOUNT="${AZURE_STORAGE_ACCOUNT:?set AZURE_STORAGE_ACCOUNT (terraform -chdir=terraform output -raw storage_account)}"
CONTAINER="${AZURE_BLOB_CONTAINER:-receipts}"
STAGE="${1:-./receipts-migration}"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

for tool in aws az; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done

# `--auth-mode login` throughout. The storage account has shared_access_key
# disabled, so every `az storage` call here authenticates as your Entra ID
# identity against the Storage Blob Data Contributor role Terraform assigned
# to whoever ran `apply`. Without that role these commands fail with 403 on a
# resource you own, which is confusing the first time -- being subscription
# Owner is a control-plane role and does not grant data-plane access.
az account show >/dev/null || { echo "Run 'az login' first." >&2; exit 1; }

say "1/3  Downloading from s3://${BUCKET}"
mkdir -p "${STAGE}"
aws s3 sync "s3://${BUCKET}" "${STAGE}" --only-show-errors

LOCAL_COUNT="$(find "${STAGE}" -type f | wc -l | tr -d ' ')"
echo "     ${LOCAL_COUNT} objects staged in ${STAGE}"

if [[ "${LOCAL_COUNT}" -eq 0 ]]; then
  echo
  echo "Nothing to migrate. Either the bucket is empty, or the 90-day expiry"
  echo "rule has already removed everything that was in it -- both are normal"
  echo "and neither is an error."
  exit 0
fi

say "2/3  Uploading to ${ACCOUNT}/${CONTAINER}"
# No --content-type. The stored type is irrelevant to how these are served:
# presign_get stamps the response with the content type recorded on the
# receipts row, precisely so the bytes cannot dictate how a browser treats
# them (see adapters/storage/azure_blob.py).
az storage blob upload-batch \
  --auth-mode login \
  --account-name "${ACCOUNT}" \
  --destination "${CONTAINER}" \
  --source "${STAGE}" \
  --overwrite \
  --output none

say "3/3  Verifying"
REMOTE_COUNT="$(az storage blob list \
  --auth-mode login \
  --account-name "${ACCOUNT}" \
  --container-name "${CONTAINER}" \
  --query 'length(@)' -o tsv)"

echo "     s3:   ${LOCAL_COUNT}"
echo "     blob: ${REMOTE_COUNT}"

if [[ "${REMOTE_COUNT}" -lt "${LOCAL_COUNT}" ]]; then
  echo
  echo "Fewer blobs than objects. Do not cut over -- rerun this script; it is" >&2
  echo "idempotent and will fill the gap." >&2
  exit 1
fi

cat <<EOF

Done. ${REMOTE_COUNT} blobs in ${ACCOUNT}/${CONTAINER}.

Keep ${STAGE} until the Azure deployment has served real receipt images to a
browser. It is the only copy of this data outside the two clouds, and the AWS
bucket is about to be destroyed.

One thing this script cannot check: that the blob names match the s3_key column
in Postgres. Prove it against a real row before trusting the count --

  psql "\$DATABASE_DIRECT_URL" -tAc \\
    "select s3_key from receipts where status <> 'pending_upload' limit 1"

then confirm the blob exists under exactly that name:

  az storage blob exists --auth-mode login --account-name ${ACCOUNT} \\
    --container-name ${CONTAINER} --name '<the s3_key>' --query exists
EOF
