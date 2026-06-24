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
#   DROPLET_USER   SSH username on the droplet          (default: lalutir)
#   DROPLET_HOST   Droplet IP address or hostname       (REQUIRED)
#   REMOTE_PATH    Absolute path Caddy serves the site  (default: /home/lalutir/world-cup-simulator)
#   SSH_KEY        Path to your private SSH key         (optional — omit if ssh-agent handles it)
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DROPLET_USER="${DROPLET_USER:-lalutir}"
DROPLET_HOST="${DROPLET_HOST:?Error: DROPLET_HOST is not set. Run: DROPLET_HOST=<ip-or-hostname> bash scripts/deploy_site.sh}"
REMOTE_PATH="${REMOTE_PATH:-/home/lalutir/world-cup-predictor}"
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

# Ensure the remote directories exist
# shellcheck disable=SC2086
ssh $SSH_OPTS "${TARGET}" "mkdir -p ${REMOTE_PATH} && sudo mkdir -p /etc/caddy/conf.d"

# Copy site files
# shellcheck disable=SC2086
scp -r $SSH_OPTS "${SITE_DIR}/." "${TARGET}:${REMOTE_PATH}/"

# Deploy the Caddy snippet and reload
# shellcheck disable=SC2086
scp $SSH_OPTS "${REPO_ROOT}/caddy/world-cup.caddy" "${TARGET}:/tmp/world-cup.caddy"
# shellcheck disable=SC2086
ssh $SSH_OPTS "${TARGET}" "sudo mv /tmp/world-cup.caddy /etc/caddy/conf.d/world-cup.caddy && caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy"

echo ""
echo "Deploy complete."
echo "Visit: https://world-cup-simulation.lalutir.com"
