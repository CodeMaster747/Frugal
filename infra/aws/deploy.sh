#!/usr/bin/env bash
#
# Deploy Frugal to the instance.
#
#   ./deploy.sh <public-ip>
#
# Builds nothing locally and pushes no images. The instance builds from source,
# which on a t3.micro is slow (several minutes for the worker image) but avoids
# a registry: ECR outside the free tier is a recurring charge, and Docker Hub
# needs credentials on the box. For a single instance, building in place is the
# cheaper trade.
#
# Secrets are not in this script and not in the repository. They live in
# /opt/frugal/.env on the instance, created once by hand — see RUNBOOK.md §2.

set -euo pipefail

HOST="${1:?Usage: $0 <public-ip>   (terraform -chdir=terraform output -raw public_ip)}"
SSH_USER="${SSH_USER:-ubuntu}"
REMOTE="${SSH_USER}@${HOST}"
APP_DIR="/opt/frugal"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

say "Deploying to ${REMOTE}"

# --- preflight --------------------------------------------------------------

# Reachability and bootstrap state are different failures with different
# remedies, and conflating them sent one operator to read a log on a machine
# they could not connect to. Distinguish them before saying anything.
if ! nc -z -G 8 "${HOST}" 22 2>/dev/null; then
  cat >&2 <<EOF
Cannot reach ${HOST} on port 22.

The instance is probably fine -- check whether it is serving HTTPS:

  curl -fsS https://${HOST}/health/ready

If that answers, only SSH is blocked, and the cause is almost always that your
ISP moved your address since the security group was last updated. One command
fixes it:

  ./allow-my-ip.sh

If port 443 is also silent, the instance really is down or terminated.
EOF
  exit 1
fi

ssh -o ConnectTimeout=10 "${REMOTE}" 'test -f /opt/frugal/.bootstrapped' || {
  echo "Connected, but the instance has not finished cloud-init." >&2
  echo "Check /var/log/cloud-init-output.log on the box." >&2
  exit 1
}

ssh "${REMOTE}" "test -f ${APP_DIR}/.env" || {
  cat >&2 <<EOF
No ${APP_DIR}/.env on the instance.

Secrets are never deployed from here — cloud-init user-data is readable by
anything on the box that can reach the metadata service, and this repository is
not a place for credentials. Create it once by hand:

  ssh ${REMOTE}
  sudo -u ubuntu tee ${APP_DIR}/.env >/dev/null <<'ENVFILE'
  DATABASE_URL=postgresql+asyncpg://...    # Neon pooled endpoint
  DATABASE_DIRECT_URL=postgresql+asyncpg://...   # Neon direct, for migrations
  REDIS_URL=rediss://...                   # Upstash
  JWT_SECRET=<openssl rand -hex 32>
  S3_BUCKET=<terraform output receipts_bucket>
  AWS_REGION=ap-south-1
  CORS_ORIGINS=https://<your-app>.vercel.app
  ENVFILE
  chmod 600 ${APP_DIR}/.env

Then run this again.
EOF
  exit 1
}

# --- ship the source --------------------------------------------------------
# rsync rather than git clone: no deploy key on the instance, and no
# requirement that the commit be pushed before it can be tested.

say "Copying source"
rsync -az --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.next' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'backups' \
  --exclude '.env' \
  "${REPO_ROOT}/backend" "${REPO_ROOT}/infra" \
  "${REMOTE}:${APP_DIR}/"

# --- migrate, then restart --------------------------------------------------
# Migrations run before the new code starts, against the direct endpoint:
# Alembic through Neon's pooler can fail partway and leave the schema in a state
# no migration describes.

say "Applying migrations"
ssh "${REMOTE}" bash -euo pipefail <<'REMOTE_SCRIPT'
cd /opt/frugal/infra/aws
set -a; . /opt/frugal/.env; set +a

docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env build api

docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env \
  run --rm -e DATABASE_URL="${DATABASE_DIRECT_URL}" api \
  alembic upgrade head

# The second database (ADR-011), when there is one. The wrapper skips cleanly
# and says so when SIGNALS_DATABASE_URL is unset -- `alembic_signals/env.py`
# itself raises rather than no-opping, because a migration runner that silently
# does nothing is how a schema drifts.
docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env \
  run --rm \
  -e SIGNALS_DATABASE_URL="${SIGNALS_DATABASE_DIRECT_URL:-${SIGNALS_DATABASE_URL:-}}" api \
  python -m scripts.migrate_signals
REMOTE_SCRIPT

say "Starting services"
ssh "${REMOTE}" bash -euo pipefail <<'REMOTE_SCRIPT'
cd /opt/frugal/infra/aws
docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env up -d --build

# Caddy's config is a mounted file, not part of its image, so `up -d --build`
# sees nothing to change and leaves the container running the config it started
# with. A new route in the Caddyfile then 404s exactly as though the API did not
# implement it -- which cost an afternoon once, because the API *did* implement
# it and answered correctly on localhost the whole time.
docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env \
  up -d --force-recreate --no-deps caddy

echo "waiting for the API to report ready"
for _ in $(seq 1 30); do
  if docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env \
       exec -T api python -c "
import sys, urllib.request
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/ready', timeout=5).status == 200 else 1)
" 2>/dev/null; then
    echo "ready"; exit 0
  fi
  sleep 5
done

echo "The API did not become ready. Recent logs:" >&2
docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env logs --tail=40 api >&2
exit 1
REMOTE_SCRIPT

say "Deployed"

# By DOMAIN, not by IP. Caddy serves its certificate by SNI, so a request
# addressed to the raw address presents no matching name and the handshake is
# aborted before any certificate is even offered -- which fails identically to a
# broken deploy, and fails the same way with `-k`.
DOMAIN=$(ssh "${REMOTE}" 'grep -E "^DOMAIN=" /opt/frugal/.env | cut -d= -f2-' 2>/dev/null || true)
CHECK_HOST="${DOMAIN:-$HOST}"

echo "Check it from here, not from the instance — that also proves the security"
echo "group, DNS and TLS are right, which a request from localhost does not:"
echo "  curl -fsS https://${CHECK_HOST}/health/ready"
if [ -z "${DOMAIN}" ]; then
  echo
  echo "(No DOMAIN in /opt/frugal/.env, so Caddy is serving plain HTTP on :80.)"
fi
