#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
STRICT_INTEGRATIONS="${STRICT_INTEGRATIONS:-0}"

log() { echo "[e2e] $*"; }
warn() { echo "[e2e] WARN: $*"; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || { log "missing command: $1"; exit 2; }; }
need_cmd curl

api_get() {
  local p="$1"
  curl -fsS "$BASE_URL$p"
}

api_post_json() {
  local p="$1"
  local body="${2:-{}}"
  curl -fsS -X POST -H 'content-type: application/json' "$BASE_URL$p" -d "$body"
}

log "BASE_URL=$BASE_URL"
log "STRICT_INTEGRATIONS=$STRICT_INTEGRATIONS"

# 1) camera/runtime alive (operator first step)
api_get "/health" >/dev/null
set +e
api_get "/api/rtsp/status" >/dev/null
rtsp_rc=$?
api_get "/api/v1/events?limit=5" >/dev/null
events_rc=$?
api_get "/api/v1/recent_plates" >/dev/null
plates_rc=$?
set -e

if [[ $rtsp_rc -ne 0 ]]; then
  warn "camera runtime endpoint /api/rtsp/status is unavailable"
fi
if [[ $events_rc -ne 0 ]]; then
  warn "events endpoint /api/v1/events is unavailable"
fi
if [[ $plates_rc -ne 0 ]]; then
  warn "recent plates endpoint /api/v1/recent_plates is unavailable"
fi

# 2) integration diagnostics actions (operator flow tail)
set +e
api_post_json "/api/v1/mqtt/check" '{}' >/dev/null
mqtt_check_rc=$?
api_post_json "/api/v1/mqtt/test_publish" '{}' >/dev/null
mqtt_publish_rc=$?
api_post_json "/api/v1/telegram/test" '{"text":"✅ e2e operator flow test","with_photo":false}' >/dev/null
tg_test_rc=$?
set -e

if [[ $mqtt_check_rc -ne 0 ]]; then
  warn "MQTT check failed"
fi
if [[ $mqtt_publish_rc -ne 0 ]]; then
  warn "MQTT test publish failed"
fi
if [[ $tg_test_rc -ne 0 ]]; then
  warn "Telegram test failed"
fi

if [[ "$STRICT_INTEGRATIONS" == "1" ]]; then
  if [[ $mqtt_check_rc -ne 0 || $mqtt_publish_rc -ne 0 || $tg_test_rc -ne 0 ]]; then
    log "ERROR: integration diagnostics failed in strict mode"
    exit 1
  fi
fi

log "Operator flow smoke completed"
