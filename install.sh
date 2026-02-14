#!/usr/bin/env bash
set -euo pipefail

# =========================================================
# Файл: install.sh
# Проект: LPR GateBox
# Версия: v0.3.11-installer
# Автор: Александр
# Что сделано:
# - Кросс-платформенный установщик (Linux/macOS)
# - Цветной UI, логотип, шаги, проверки
# - Команды: install/update/uninstall/status/logs
# - NEW: создаёт config/live + debug
# - NEW: проверка volume ./config:/config у rtsp_worker (snapshot contract)
# - NEW: soft-check snapshot endpoint /api/rtsp/frame.jpg
# =========================================================

# ---------- UI (colors) ----------
RED="\033[31m"; GREEN="\033[32m"; YELLOW="\033[33m"; BLUE="\033[34m"; CYAN="\033[36m"; DIM="\033[2m"; RESET="\033[0m"
BOLD="\033[1m"

logo() {
  echo -e "${CYAN}${BOLD}"
  cat <<'EOF'
   _     ____  ____     ____       _       ____
  | |   |  _ \|  _ \   / ___| __ _ | |_ ___| __ )  _____  __
  | |   | |_) | |_) | | |  _ / _` || __/ _ \  _ \ / _ \ \/ /
  | |___|  __/|  _ <  | |_| | (_| || ||  __/ |_) | (_) >  <
  |_____|_|   |_| \_\  \____|\__,_| \__\___|____/ \___/_/\_\
EOF
  echo -e "${RESET}${DIM}Установщик LPR GateBox (Linux/macOS). Русский интерфейс + цвета.${RESET}"
  echo
}

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}!${RESET} $*"; }
err()  { echo -e "${RED}✗${RESET} $*"; }
info() { echo -e "${BLUE}→${RESET} $*"; }
die()  { err "$*"; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Не найдено: $1. Установи и повтори."; }

# ---------- Defaults ----------
ACTION="${1:-install}"
REPO_URL_DEFAULT="https://github.com/pirsasha/lpr_gatebox.git"
DIR_DEFAULT="$HOME/lpr_gatebox"
COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env"
CFG_DIR="config"
MODELS_DIR="models"

detect_ip() {
  local ip=""
  ip="$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"
  if [[ -z "$ip" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  echo "${ip:-127.0.0.1}"
}

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    die "Не найден docker compose. Нужен 'docker compose' или 'docker-compose'."
  fi
}

ensure_repo() {
  local dir="$1" url="$2"
  if [[ -d "$dir/.git" ]]; then
    ok "Репозиторий уже есть: $dir"
    return 0
  fi
  info "Клонирую репозиторий: $url"
  need_cmd git
  git clone "$url" "$dir"
  ok "Клонирование завершено"
}

check_snapshot_contract() {
  # Проверка: в rtsp_worker.volumes должен быть - ./config:/config
  # (иначе gatebox не увидит /config/live/frame.jpg)
  local f="$1"
  if ! awk '
    $1=="rtsp_worker:" {in=1; vol=0; found=0; next}
    in && $1=="volumes:" {vol=1; next}
    in && vol && $0 ~ /- \.\/config:\/config/ {found=1}
    in && $1 ~ /^[A-Za-z0-9_]+:$/ && $1!="rtsp_worker:" {exit}
    END {exit(found?0:1)}
  ' "$f"; then
    warn "ВАЖНО: в $f у rtsp_worker нет volume ./config:/config."
    warn "Иначе snapshot в UI будет 404 (gatebox не видит /config/live/frame.jpg)."
    warn "Исправление: добавь в rtsp_worker -> volumes: - ./config:/config"
  else
    ok "Проверка snapshot: rtsp_worker видит ./config (OK)"
  fi
}

ensure_examples() {
  local dir="$1"
  cd "$dir"
  [[ -f "$COMPOSE_FILE" ]] || die "Не найден $COMPOSE_FILE в $dir"

  # .env
  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f ".env.example" ]]; then
      cp ".env.example" "$ENV_FILE"
      ok "Создан $ENV_FILE из .env.example"
    else
      warn "Не найден .env.example — создам минимальный .env"
      cat > "$ENV_FILE" <<EOF
TAG=stable
GATEBOX_PORT=8080
UPDATER_PORT=9010
RTSP_URL=
MQTT_ENABLED=0
MQTT_HOST=
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
MQTT_TOPIC=gate/open
EOF
      ok "Создан $ENV_FILE (минимальный)"
    fi
  else
    ok "$ENV_FILE уже существует — не трогаю"
  fi

  # config + обязательные папки live/debug
  mkdir -p "$CFG_DIR" "$CFG_DIR/live" "debug"
  ok "Созданы папки: config, config/live, debug"

  # settings.json
  if [[ ! -f "$CFG_DIR/settings.json" ]]; then
    if [[ -f "$CFG_DIR/settings.example.json" ]]; then
      cp "$CFG_DIR/settings.example.json" "$CFG_DIR/settings.json"
      ok "Создан config/settings.json из example"
    else
      warn "Нет settings.example.json — создам базовый config/settings.json"
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
    "confirm_window_sec": 2.0
  },
  "runtime": { "debug_log": 0 }
}
EOF
      ok "Создан config/settings.json (базовый)"
    fi
  else
    ok "config/settings.json уже существует — не трогаю"
  fi

  # whitelist.json
  if [[ ! -f "$CFG_DIR/whitelist.json" ]]; then
    if [[ -f "$CFG_DIR/whitelist.example.json" ]]; then
      cp "$CFG_DIR/whitelist.example.json" "$CFG_DIR/whitelist.json"
      ok "Создан config/whitelist.json из example"
    else
      warn "Нет whitelist.example.json — создам базовый config/whitelist.json"
      cat > "$CFG_DIR/whitelist.json" <<'EOF'
{ "enabled": 0, "plates": [] }
EOF
      ok "Создан config/whitelist.json (базовый)"
    fi
  else
    ok "config/whitelist.json уже существует — не трогаю"
  fi

  # models presence check
  if [[ ! -d "$MODELS_DIR" ]]; then
    warn "Папка models отсутствует. Убедись, что модели лежат в ./models"
  else
    ok "models/ найден"
  fi

  check_snapshot_contract "$COMPOSE_FILE"
}

edit_env_interactive() {
  local dir="$1"
  cd "$dir"

  local tag rtsp mqtt_enabled mqtt_host mqtt_port mqtt_user mqtt_pass mqtt_topic input
  echo -e "${BOLD}Настройка .env (можно просто нажимать Enter)${RESET}"
  echo

  tag="$(grep -E '^TAG=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
  read -rp "Версия (TAG) [${tag:-stable}] : " input || true
  [[ -n "${input:-}" ]] && tag="$input" || tag="${tag:-stable}"

  rtsp="$(grep -E '^RTSP_URL=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
  read -rp "RTSP_URL [${rtsp:-}] : " input || true
  [[ -n "${input:-}" ]] && rtsp="$input"

  mqtt_enabled="$(grep -E '^MQTT_ENABLED=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
  read -rp "Включить MQTT? (0/1) [${mqtt_enabled:-0}] : " input || true
  [[ -n "${input:-}" ]] && mqtt_enabled="$input" || mqtt_enabled="${mqtt_enabled:-0}"

  if [[ "$mqtt_enabled" == "1" ]]; then
    mqtt_host="$(grep -E '^MQTT_HOST=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
    read -rp "MQTT_HOST [${mqtt_host:-}] : " input || true
    [[ -n "${input:-}" ]] && mqtt_host="$input"

    mqtt_port="$(grep -E '^MQTT_PORT=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
    read -rp "MQTT_PORT [${mqtt_port:-1883}] : " input || true
    [[ -n "${input:-}" ]] && mqtt_port="$input" || mqtt_port="${mqtt_port:-1883}"

    mqtt_user="$(grep -E '^MQTT_USER=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
    read -rp "MQTT_USER [${mqtt_user:-}] : " input || true
    [[ -n "${input:-}" ]] && mqtt_user="$input"

    mqtt_pass="$(grep -E '^MQTT_PASS=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
    read -rsp "MQTT_PASS [скрыто] (Enter оставить как есть): " input || true
    echo
    [[ -n "${input:-}" ]] && mqtt_pass="$input"

    mqtt_topic="$(grep -E '^MQTT_TOPIC=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
    read -rp "MQTT_TOPIC [${mqtt_topic:-gate/open}] : " input || true
    [[ -n "${input:-}" ]] && mqtt_topic="$input" || mqtt_topic="${mqtt_topic:-gate/open}"
  fi

  set_kv() {
    local key="$1" value="$2"
    if grep -qE "^${key}=" "$ENV_FILE"; then
      sed -i.bak "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
      echo "${key}=${value}" >> "$ENV_FILE"
    fi
  }

  set_kv "TAG" "$tag"
  set_kv "RTSP_URL" "$rtsp"
  set_kv "MQTT_ENABLED" "$mqtt_enabled"

  if [[ "$mqtt_enabled" == "1" ]]; then
    set_kv "MQTT_HOST" "${mqtt_host:-}"
    set_kv "MQTT_PORT" "${mqtt_port:-1883}"
    set_kv "MQTT_USER" "${mqtt_user:-}"
    set_kv "MQTT_PASS" "${mqtt_pass:-}"
    set_kv "MQTT_TOPIC" "${mqtt_topic:-gate/open}"
  fi

  rm -f "$ENV_FILE.bak" || true
  ok ".env обновлён"
}

pull_and_up() {
  local dir="$1"
  cd "$dir"
  local dc; dc="$(compose_cmd)"

  info "Pull образов (это может занять время)..."
  $dc -f "$COMPOSE_FILE" pull
  ok "Образы загружены"

  info "Запуск сервисов..."
  $dc -f "$COMPOSE_FILE" up -d
  ok "Сервисы запущены"
}

health_check() {
  local ip port
  ip="$(detect_ip)"
  port="$(grep -E '^GATEBOX_PORT=' "$ENV_FILE" | head -n1 | cut -d= -f2- || echo "8080")"

  info "Проверка health: http://${ip}:${port}/api/v1/health"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "http://${ip}:${port}/api/v1/health" >/dev/null && ok "health OK" || warn "health пока не ответил (проверь логи)"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "http://${ip}:${port}/api/v1/health" >/dev/null && ok "health OK" || warn "health пока не ответил (проверь логи)"
  else
    warn "Нет curl/wget — пропускаю health-check"
  fi

  # soft-check snapshot
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "http://${ip}:${port}/api/rtsp/frame.jpg" >/dev/null 2>&1; then
      ok "Snapshot доступен: /api/rtsp/frame.jpg"
    else
      warn "Snapshot пока недоступен (/api/rtsp/frame.jpg). Если в UI 404 — проверь volume ./config:/config у rtsp_worker и gatebox."
    fi
  fi

  echo
  echo -e "${GREEN}${BOLD}Готово!${RESET}"
  echo -e "UI: ${CYAN}http://${ip}:${port}${RESET}"
  echo -e "${DIM}Логи: ./install.sh logs${RESET}"
}

do_install() {
  logo
  need_cmd docker
  compose_cmd >/dev/null

  local repo_url="$REPO_URL_DEFAULT"
  local dir="$DIR_DEFAULT"
  local input

  echo -e "${BOLD}Параметры установки${RESET}"
  read -rp "Папка установки [$dir] : " input || true
  [[ -n "${input:-}" ]] && dir="$input"

  read -rp "Git репозиторий [$repo_url] : " input || true
  [[ -n "${input:-}" ]] && repo_url="$input"

  ensure_repo "$dir" "$repo_url"
  ensure_examples "$dir"

  echo
  read -rp "Открыть мастер-настройку .env сейчас? (y/N): " input || true
  if [[ "${input:-}" =~ ^[Yy]$ ]]; then
    edit_env_interactive "$dir"
  else
    warn "Пропускаю мастер — ты можешь отредактировать $dir/.env вручную"
  fi

  pull_and_up "$dir"
  cd "$dir"
  health_check
}

do_update() {
  logo
  need_cmd docker
  compose_cmd >/dev/null

  local dir="$DIR_DEFAULT"
  local input
  read -rp "Папка установки [$dir] : " input || true
  [[ -n "${input:-}" ]] && dir="$input"
  [[ -f "$dir/$COMPOSE_FILE" ]] || die "Не найден $COMPOSE_FILE в $dir"

  cd "$dir"
  local dc; dc="$(compose_cmd)"

  check_snapshot_contract "$COMPOSE_FILE"

  info "Pull новых образов..."
  $dc -f "$COMPOSE_FILE" pull
  info "Recreate..."
  $dc -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans
  ok "Обновление завершено"
  health_check
}

do_uninstall() {
  logo
  need_cmd docker
  compose_cmd >/dev/null

  local dir="$DIR_DEFAULT"
  local input
  read -rp "Папка установки [$dir] : " input || true
  [[ -n "${input:-}" ]] && dir="$input"

  if [[ ! -f "$dir/$COMPOSE_FILE" ]]; then
    warn "Не найден $COMPOSE_FILE в $dir — пропускаю down"
  else
    cd "$dir"
    local dc; dc="$(compose_cmd)"
    info "Останавливаю и удаляю сервисы + volumes..."
    $dc -f "$COMPOSE_FILE" down --remove-orphans --volumes
    ok "Сервисы удалены"
  fi

  read -rp "Удалить папку $dir полностью? (y/N): " input || true
  if [[ "${input:-}" =~ ^[Yy]$ ]]; then
    rm -rf "$dir"
    ok "Папка удалена"
  else
    warn "Папка оставлена: $dir"
  fi
}

do_status() {
  logo
  local dir="$DIR_DEFAULT" input
  read -rp "Папка установки [$dir] : " input || true
  [[ -n "${input:-}" ]] && dir="$input"
  [[ -f "$dir/$COMPOSE_FILE" ]] || die "Не найден $COMPOSE_FILE в $dir"
  cd "$dir"
  local dc; dc="$(compose_cmd)"
  $dc -f "$COMPOSE_FILE" ps
}

do_logs() {
  logo
  local dir="$DIR_DEFAULT" input
  read -rp "Папка установки [$dir] : " input || true
  [[ -n "${input:-}" ]] && dir="$input"
  [[ -f "$dir/$COMPOSE_FILE" ]] || die "Не найден $COMPOSE_FILE в $dir"
  cd "$dir"
  local dc; dc="$(compose_cmd)"
  $dc -f "$COMPOSE_FILE" logs -f --tail 200 gatebox rtsp_worker updater
}

case "$ACTION" in
  install)   do_install ;;
  update)    do_update ;;
  uninstall) do_uninstall ;;
  status)    do_status ;;
  logs)      do_logs ;;
  *)
    logo
    echo "Использование:"
    echo "  ./install.sh install   # установка"
    echo "  ./install.sh update    # обновление"
    echo "  ./install.sh uninstall # удаление"
    echo "  ./install.sh status    # статус"
    echo "  ./install.sh logs      # логи"
    exit 1
    ;;
esac