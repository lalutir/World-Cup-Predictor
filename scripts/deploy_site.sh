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

DROPLET_USER="${DROPLET_USER:-lalutir}"
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

# Copy site files to the droplet
# shellcheck disable=SC2086
scp -r $SSH_OPTS "${SITE_DIR}/." "${TARGET}:${REMOTE_PATH}/"

echo ""
echo "Deploy complete."
echo "Visit: https://world-cup-simulation.lalutir.com"
echo ""

# ── First-time nginx instructions ─────────────────────────────────────────────
echo ""
echo "If this is the first deploy, ensure Caddy is configured on the droplet:"
echo "  sudo tee -a /etc/caddy/Caddyfile << 'EOF'"
echo "  world-cup-simulation.lalutir.com {"
echo "      root * ${REMOTE_PATH}"
echo "      file_server"
echo "  }"
echo "  EOF"
echo "  sudo systemctl reload caddy"
echo ""
echo "And add a Cloudflare DNS A record:"
echo "  Name: world-cup-simulation  Content: ${DROPLET_HOST}  Proxy: Proxied (orange cloud)"
