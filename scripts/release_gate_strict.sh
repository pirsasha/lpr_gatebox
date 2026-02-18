#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"

log() { echo "[release-gate] $*"; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || { log "missing command: $1"; exit 2; }; }
need_cmd npm
need_cmd python
need_cmd bash

log "BASE_URL=$BASE_URL"

log "1/5 lint UI"
npm --prefix ui run lint

log "2/5 build UI"
npm --prefix ui run build

log "3/5 compile backend"
python -m compileall app

log "4/5 strict runtime smoke"
STRICT_INTEGRATIONS=1 BASE_URL="$BASE_URL" bash scripts/smoke_runtime_ui.sh

log "5/5 strict operator e2e"
STRICT_INTEGRATIONS=1 BASE_URL="$BASE_URL" bash scripts/e2e_operator_flow.sh

log "OK: strict release gate passed"
