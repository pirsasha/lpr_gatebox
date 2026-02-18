#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
STRICT_INTEGRATIONS="${STRICT_INTEGRATIONS:-0}"

echo "[smoke] BASE_URL=$BASE_URL"
echo "[smoke] STRICT_INTEGRATIONS=$STRICT_INTEGRATIONS"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "[smoke] missing command: $1"; exit 2; }; }
need_cmd curl
need_cmd python

api_get() {
  local p="$1"
  curl -fsS "$BASE_URL$p"
}

api_post() {
  local p="$1"
  curl -fsS -X POST "$BASE_URL$p"
}

api_put_json() {
  local p="$1"
  local body="$2"
  curl -fsS -X PUT -H 'content-type: application/json' "$BASE_URL$p" -d "$body"
}

# 1) health
api_get "/health" >/dev/null
api_get "/api/v1/health" >/dev/null

# 2) settings roundtrip (safe patch)
cur_json="$(api_get /api/v1/settings)"

CUR_JSON="$cur_json" python -c '
import json, os
obj = json.loads(os.environ.get("CUR_JSON") or "{}")
s = obj.get("settings") or {}
ui = s.get("ui") or {}
ui["events_max"] = int(ui.get("events_max", 200))
print(json.dumps({"settings": {"ui": {"events_max": ui["events_max"]}}}, ensure_ascii=False))
' >/tmp/lpr_smoke_patch.json

api_put_json "/api/v1/settings" "$(cat /tmp/lpr_smoke_patch.json)" >/dev/null
api_post "/api/v1/settings/apply" >/dev/null

# 3) rtsp/live endpoints should at least respond (may be no frame yet)
set +e
curl -fsS "$BASE_URL/api/rtsp/status" >/dev/null
status_rc=$?
curl -fsS "$BASE_URL/api/v1/recent_plates" >/dev/null
plates_rc=$?
set -e

if [[ $status_rc -ne 0 ]]; then
  echo "[smoke] WARN: /api/rtsp/status not available"
fi
if [[ $plates_rc -ne 0 ]]; then
  echo "[smoke] WARN: /api/v1/recent_plates not available"
fi

# 4) integration diagnostics (non-fatal): MQTT + Telegram + CloudPub
set +e
curl -fsS -X POST "$BASE_URL/api/v1/mqtt/check" >/dev/null
mqtt_check_rc=$?
curl -fsS -X POST -H 'content-type: application/json' "$BASE_URL/api/v1/mqtt/test_publish" -d '{}' >/dev/null
mqtt_publish_rc=$?
curl -fsS "$BASE_URL/api/v1/telegram/bot_info" >/dev/null
tg_info_rc=$?

cloudpub_status_json="$(curl -fsS "$BASE_URL/api/v1/cloudpub/status")"
cloudpub_status_rc=$?
cloudpub_enabled="0"
if [[ $cloudpub_status_rc -eq 0 ]]; then
  cloudpub_enabled="$(CLOUDPUB_STATUS_JSON="$cloudpub_status_json" python - <<'PY2'
import json, os
try:
    obj = json.loads(os.environ.get("CLOUDPUB_STATUS_JSON") or "{}")
except Exception:
    print("0")
else:
    print("1" if bool(obj.get("enabled")) else "0")
PY2
)"
fi

cloudpub_connect_rc=0
cloudpub_disconnect_rc=0
if [[ $cloudpub_status_rc -eq 0 && "$cloudpub_enabled" == "1" ]]; then
  curl -fsS -X POST -H 'content-type: application/json' "$BASE_URL/api/v1/cloudpub/connect" -d '{}' >/dev/null
  cloudpub_connect_rc=$?
  curl -fsS -X POST "$BASE_URL/api/v1/cloudpub/disconnect" >/dev/null
  cloudpub_disconnect_rc=$?
fi
set -e

if [[ $mqtt_check_rc -ne 0 ]]; then
  echo "[smoke] WARN: /api/v1/mqtt/check failed (MQTT may be disabled/unreachable)"
fi
if [[ $mqtt_publish_rc -ne 0 ]]; then
  echo "[smoke] WARN: /api/v1/mqtt/test_publish failed (MQTT may be disabled/unreachable)"
fi
if [[ $tg_info_rc -ne 0 ]]; then
  echo "[smoke] WARN: /api/v1/telegram/bot_info failed (token may be unset)"
fi
if [[ $cloudpub_status_rc -ne 0 ]]; then
  echo "[smoke] WARN: /api/v1/cloudpub/status failed (CloudPub may be unconfigured)"
elif [[ "$cloudpub_enabled" != "1" ]]; then
  echo "[smoke] WARN: CloudPub disabled in settings; connect/disconnect checks skipped"
fi
if [[ $cloudpub_connect_rc -ne 0 ]]; then
  echo "[smoke] WARN: /api/v1/cloudpub/connect failed (CloudPub may be not configured)"
fi
if [[ $cloudpub_disconnect_rc -ne 0 ]]; then
  echo "[smoke] WARN: /api/v1/cloudpub/disconnect failed"
fi

if [[ "$STRICT_INTEGRATIONS" == "1" ]]; then
  if [[ $mqtt_check_rc -ne 0 || $mqtt_publish_rc -ne 0 || $tg_info_rc -ne 0 || $cloudpub_status_rc -ne 0 ]]; then
    echo "[smoke] ERROR: integration checks failed in strict mode"
    exit 1
  fi

  if [[ "$cloudpub_enabled" == "1" && $cloudpub_connect_rc -ne 0 ]] || [[ "$cloudpub_enabled" == "1" && $cloudpub_disconnect_rc -ne 0 ]]; then
    echo "[smoke] ERROR: CloudPub connect/disconnect failed in strict mode"
    exit 1
  fi
fi

echo "[smoke] OK"
