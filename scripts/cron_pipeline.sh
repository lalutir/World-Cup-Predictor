#!/usr/bin/env bash
# cron_pipeline.sh — Runs on the droplet itself (via cron), not on a dev machine.
#
# Pulls the latest code + hand-edited fixtures.csv, re-runs the full
# fetch -> train -> simulate -> build-site pipeline, and (since Caddy's root
# points directly at this repo's site/ subdirectory) the freshly built
# dashboard is live the moment this script finishes. No scp/deploy step
# needed for the automated path -- deploy_site.sh remains available for
# manual pushes from a dev machine.
#
# Usage: cron_pipeline.sh [cron-marker-to-self-remove]
#   If a marker is passed (e.g. WC_CRON_20260712), any crontab line
#   containing that marker is removed after this run completes -- used for
#   one-off scheduled runs so they don't fire again on the same date next year.

set -uo pipefail

REPO_DIR="/home/lalutir/world-cup-predictor"
LOG_DIR="${REPO_DIR}/logs"
LOG_FILE="${LOG_DIR}/pipeline_$(date -u +%Y%m%dT%H%M%SZ).log"
MARKER="${1:-}"

mkdir -p "${LOG_DIR}"

{
  echo "=== cron_pipeline.sh started at $(date -u --iso-8601=seconds) ==="

  cd "${REPO_DIR}" || { echo "FATAL: repo dir missing"; exit 1; }

  echo "--- git pull ---"
  git pull

  echo "--- running pipeline (timeout 20m) ---"
  timeout 20m .venv/bin/python -m src.simulator.montecarlo --rebuild
  status=$?

  if [[ ${status} -eq 0 ]]; then
    echo "=== pipeline succeeded, site/ rebuilt ==="
  else
    echo "=== pipeline FAILED (exit ${status}) -- previous site/ left untouched ==="
  fi

  # Keep only the last 20 log files.
  ls -1t "${LOG_DIR}"/pipeline_*.log 2>/dev/null | tail -n +21 | xargs -r rm --

  exit ${status}
} >> "${LOG_FILE}" 2>&1

run_status=$?

if [[ -n "${MARKER}" ]]; then
  crontab -l 2>/dev/null | grep -v "${MARKER}" | crontab -
  echo "Removed one-off cron entry tagged ${MARKER}" >> "${LOG_FILE}"
fi

exit ${run_status}
