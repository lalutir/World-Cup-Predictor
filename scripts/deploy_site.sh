#!/usr/bin/env bash
# deploy_site.sh — Push the generated dashboard to your DigitalOcean droplet.
#
# ── Quick start ────────────────────────────────────────────────────────────────
#   1. Run the simulation to build the site/:
#        python -m src.simulator.montecarlo
#
#   2. Set DROPLET_HOST (once, or export it from your shell profile):
#        export DROPLET_HOST=<your-droplet-ip>
#
#   3. Deploy:
#        bash scripts/deploy_site.sh
#
# ── Configuration ──────────────────────────────────────────────────────────────
#   Override any variable by setting it as an environment variable before running.
#
#   DROPLET_USER   SSH username on the droplet          (default: root)
#   DROPLET_HOST   Droplet IP address or hostname       (REQUIRED)
#   REMOTE_PATH    Absolute path nginx serves the site  (default: /var/www/world-cup-simulation)
#   SSH_KEY        Path to your private SSH key         (optional — omit if ssh-agent handles it)
#
# ── First-time nginx setup (run once on the droplet) ──────────────────────────
#   See the printed instructions at the bottom of a first-run deploy.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DROPLET_USER="${DROPLET_USER:-root}"
DROPLET_HOST="${DROPLET_HOST:?Error: DROPLET_HOST is not set. Run: DROPLET_HOST=<ip-or-hostname> bash scripts/deploy_site.sh}"
REMOTE_PATH="${REMOTE_PATH:-/var/www/world-cup-simulation}"
SSH_KEY="${SSH_KEY:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE_DIR="${REPO_ROOT}/site"

# ── Pre-flight checks ──────────────────────────────────────────────────────────
if [[ ! -f "${SITE_DIR}/index.html" ]]; then
  echo "Error: ${SITE_DIR}/index.html not found."
  echo "Build the dashboard first:"
  echo "  python -m src.simulator.montecarlo"
  exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=accept-new"
if [[ -n "${SSH_KEY}" ]]; then
  SSH_OPTS+=" -i ${SSH_KEY}"
fi

TARGET="${DROPLET_USER}@${DROPLET_HOST}"

echo "──────────────────────────────────────────────────────"
echo "Deploying to ${TARGET}:${REMOTE_PATH}"
echo "──────────────────────────────────────────────────────"

# Ensure the remote directory exists
# shellcheck disable=SC2086
ssh $SSH_OPTS "${TARGET}" "mkdir -p ${REMOTE_PATH}"

# Sync the generated site to the droplet, deleting stale remote files
# shellcheck disable=SC2086
rsync -avz --delete -e "ssh ${SSH_OPTS}" \
  "${SITE_DIR}/" \
  "${TARGET}:${REMOTE_PATH}/"

echo ""
echo "Deploy complete."
echo "Visit: https://world-cup-simulation.lalutir.com"
echo ""

# ── First-time nginx instructions ─────────────────────────────────────────────
# shellcheck disable=SC2086
if ssh $SSH_OPTS "${TARGET}" \
  "test ! -f /etc/nginx/sites-available/world-cup-simulation" 2>/dev/null; then

  # shellcheck disable=SC2086
  DROPLET_IP="$(ssh $SSH_OPTS "${TARGET}" "curl -sf ifconfig.me" 2>/dev/null || echo "<droplet-ip>")"

  cat <<SETUP

──────────────────────────────────────────────────────
First-time setup: nginx not yet configured for this subdomain.
Run the following ON THE DROPLET (ssh ${TARGET}):
──────────────────────────────────────────────────────

# 1. Create the nginx server block
cat > /etc/nginx/sites-available/world-cup-simulation << 'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name world-cup-simulation.lalutir.com;
    root ${REMOTE_PATH};
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # Cloudflare handles TLS when the record is "Proxied" (orange cloud).
    # If using grey-cloud / DNS-only, add certbot TLS here instead.
}
NGINX

# 2. Enable the site
ln -sf /etc/nginx/sites-available/world-cup-simulation \
        /etc/nginx/sites-enabled/world-cup-simulation

# 3. Test & reload nginx
nginx -t && systemctl reload nginx

──────────────────────────────────────────────────────
Then add a DNS record in Cloudflare:
  Type:    A
  Name:    world-cup-simulation
  Content: ${DROPLET_IP}
  Proxy:   Proxied (orange cloud) — recommended for CDN + free TLS
──────────────────────────────────────────────────────
SETUP
fi
