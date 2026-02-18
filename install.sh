#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# LPR GateBox Installer (Linux)
# Version: v0.3.27
# - default TAG=stable (no manual version needed)
# - writes .env with all vars to avoid compose WARNs
# - telegram token is taken from /config/settings.json
# - shows installed image Created timestamps
# ==========================================================

PROJECT_DIR="${PROJECT_DIR:-$HOME/lpr_gatebox}"
REPO_URL="${REPO_URL:-https://github.com/pirsasha/lpr_gatebox.git}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env}"
CFG_DIR="${CFG_DIR:-config}"

log()  { echo -e "$*"; }
info() { log "ℹ️  $*"; }
ok()   { log "✅ $*"; }
warn() { log "⚠️  $*"; }
die()  { log "❌ $*"; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Не найдено: $1"; }

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    die "Не найден docker compose. Нужен 'docker compose' (v2) или 'docker-compose' (v1)."
  fi
}

ensure_repo() {
  local dir="$1" url="$2"
  need_cmd git
  if [[ -d "$dir/.git" ]]; then
    ok "Репозиторий уже есть: $dir"
    info "git pull..."
    if ! (cd "$dir" && git pull --ff-only); then
      die "git pull --ff-only не прошёл. Скорее всего локальная ветка расходится с origin/main или есть ручные правки. Выполни: cd $dir && git fetch --all && git reset --hard origin/main && git clean -fd"
    fi
    return 0
  fi
  info "Клонирую репозиторий: $url"
  git clone "$url" "$dir"
  ok "Клонирование завершено"
}

ensure_env() {
  local dir="$1"
  cd "$dir"

  if [[ ! -f "$ENV_FILE" ]]; then
    info "Создаю $ENV_FILE (минимальный, но полный набор переменных)"
    cat > "$ENV_FILE" <<EOF
# LPR GateBox env
# default tag used by docker-compose.prod.yml: \${TAG:-stable}
TAG=stable

# ports
GATEBOX_PORT=8080
UPDATER_PORT=9010

# camera
RTSP_URL=

# mqtt
MQTT_ENABLED=0
MQTT_HOST=
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
MQTT_TOPIC=gate/open
EOF
    ok "Создан $ENV_FILE"
  else
    # если .env есть, но TAG пустой — починим (это частая причина “почему не stable”)
    if grep -qE '^TAG=$' "$ENV_FILE"; then
      warn "В $ENV_FILE TAG пустой — ставлю TAG=stable"
      sed -i 's/^TAG=$/TAG=stable/' "$ENV_FILE"
      ok "TAG=stable установлен"
    else
      ok "$ENV_FILE уже существует — не трогаю"
    fi
  fi
}

ensure_config() {
  local dir="$1"
  cd "$dir"

  mkdir -p "$CFG_DIR" "$CFG_DIR/live" "debug"
  ok "Папки готовы: $CFG_DIR, $CFG_DIR/live, debug"

  if [[ ! -f "$CFG_DIR/settings.json" ]]; then
    if [[ -f "$CFG_DIR/settings.example.json" ]]; then
      cp "$CFG_DIR/settings.example.json" "$CFG_DIR/settings.json"
      ok "Создан $CFG_DIR/settings.json из example"
    else
      warn "settings.example.json не найден — создам минимальный settings.json"
      cat > "$CFG_DIR/settings.json" <<'EOF'
{
  "ocr": {
    "ocr_orient_try": 1,
    "ocr_warp_try": 1,
    "ocr_warp_w": 320,
    "ocr_warp_h": 96,
    "postcrop": 1,
    "postcrop_lrbt": "0.040,0.040,0.080,0.080",
    "min_conf": 0.78,
    "confirm_n": 2,
    "confirm_window_sec": 2
  },
  "runtime": { "debug_log": 0 },
  "camera": { "rtsp_url": "", "enabled": true },
  "gate": {
    "min_conf": 0.8,
    "confirm_n": 1,
    "confirm_window_sec": 3,
    "cooldown_sec": 15,
    "whitelist_path": "/config/whitelist.json"
  },
  "telegram": { "bot_token": "", "enabled": false },
  "mqtt": { "host": "", "port": 1883, "user": "", "pass": "", "topic": "gate/open", "enabled": false }
}
EOF
      ok "Создан минимальный $CFG_DIR/settings.json"
    fi
    # права (как у тебя сейчас: 600)
    chmod 600 "$CFG_DIR/settings.json" || true
  else
    ok "$CFG_DIR/settings.json уже есть — сохраняю"
  fi

  if [[ ! -f "$CFG_DIR/whitelist.json" ]]; then
    if [[ -f "$CFG_DIR/whitelist.example.json" ]]; then
      cp "$CFG_DIR/whitelist.example.json" "$CFG_DIR/whitelist.json"
      ok "Создан $CFG_DIR/whitelist.json из example"
    else
      echo '[]' > "$CFG_DIR/whitelist.json"
      ok "Создан пустой $CFG_DIR/whitelist.json"
    fi
  fi
}

start_stack() {
  local dir="$1"
  cd "$dir"

  [[ -f "$COMPOSE_FILE" ]] || die "Не найден $COMPOSE_FILE в $dir"

  local dc; dc="$(compose_cmd)"
  info "Compose: $dc"

  info "TAG сейчас: $(grep -E '^TAG=' "$ENV_FILE" || true)"
  info "Pull images..."
  $dc -f "$COMPOSE_FILE" pull

  info "Up..."
  $dc -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans

  ok "Готово. Текущее состояние:"
  $dc -f "$COMPOSE_FILE" ps
}

show_versions() {
  local dir="$1"
  cd "$dir"

  local dc; dc="$(compose_cmd)"
  echo
  info "Версии/даты образов (stable может быть непонятен, поэтому показываю Created):"
  $dc -f "$COMPOSE_FILE" images || true
  echo

  for img in \
    "ghcr.io/pirsasha/lpr_gatebox-gatebox:stable" \
    "ghcr.io/pirsasha/lpr_gatebox-rtsp-worker:stable" \
    "ghcr.io/pirsasha/lpr_gatebox-updater:stable"
  do
    if docker image inspect "$img" >/dev/null 2>&1; then
      echo -n "• $img -> "
      docker image inspect "$img" --format 'id={{.Id}} created={{.Created}}' || true
    fi
  done
}

main() {
  need_cmd docker

  ensure_repo "$PROJECT_DIR" "$REPO_URL"
  ensure_env "$PROJECT_DIR"
  ensure_config "$PROJECT_DIR"
  start_stack "$PROJECT_DIR"
  show_versions "$PROJECT_DIR"

  ok "UI: http://<server-ip>:${GATEBOX_PORT:-8080}"
  ok "Updater: http://<server-ip>:${UPDATER_PORT:-9010}"
}

main "$@"
