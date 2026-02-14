# =========================================================
# Файл: install.ps1
# Проект: LPR GateBox
# Версия: v0.3.11-installer
# Автор: Александр
# Что сделано:
# - Кросс-платформенный установщик (Windows)
# - Цветной UI, логотип, мастер-настройка
# - Команды: install/update/uninstall/status/logs
# - NEW: создаёт config\live и debug
# - NEW: проверка volume ./config:/config у rtsp_worker (snapshot contract)
# - NEW: soft-check snapshot endpoint /api/rtsp/frame.jpg
# =========================================================

param(
  [ValidateSet("install","update","uninstall","status","logs")]
  [string]$Action = "install",

  [string]$Dir = "$env:USERPROFILE\lpr_gatebox",
  [string]$RepoUrl = "https://github.com/pirsasha/lpr_gatebox.git"
)

$ErrorActionPreference = "Stop"

function Write-Ok($m){ Write-Host "✓ $m" -ForegroundColor Green }
function Write-Info($m){ Write-Host "→ $m" -ForegroundColor Cyan }
function Write-Warn($m){ Write-Host "! $m" -ForegroundColor Yellow }
function Write-Err($m){ Write-Host "✗ $m" -ForegroundColor Red }

function Logo {
  Write-Host ""
  Write-Host " _     ____  ____     ____       _       ____            " -ForegroundColor Cyan
  Write-Host "| |   |  _ \|  _ \   / ___| __ _ | |_ ___| __ )  _____  __" -ForegroundColor Cyan
  Write-Host "| |   | |_) | |_) | | |  _ / _` || __/ _ \  _ \ / _ \ \/ /" -ForegroundColor Cyan
  Write-Host "| |___|  __/|  _ <  | |_| | (_| || ||  __/ |_) | (_) >  < " -ForegroundColor Cyan
  Write-Host "|_____|_|   |_| \_\  \____|\__,_| \__\___|____/ \___/_/\_\" -ForegroundColor Cyan
  Write-Host ""
  Write-Host "Установщик LPR GateBox (Windows). Русский интерфейс + цвета." -ForegroundColor DarkGray
  Write-Host ""
}

function Need-Cmd($name){
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Не найдено: $name. Установи и повтори."
  }
}

function Compose-Cmd {
  try { docker compose version | Out-Null; return "docker compose" } catch {}
  try { docker-compose version | Out-Null; return "docker-compose" } catch {}
  throw "Не найден docker compose. Нужен Docker Desktop (docker compose)."
}

function Ensure-Repo($dir,$url){
  if (Test-Path "$dir\.git") { Write-Ok "Репозиторий уже есть: $dir"; return }
  Need-Cmd git
  Write-Info "Клонирую репозиторий: $url"
  git clone $url $dir
  Write-Ok "Клонирование завершено"
}

function Check-SnapshotContract($composePath){
  $txt = Get-Content $composePath -Raw
  # грубая, но практичная проверка: в секции rtsp_worker должно встретиться "./config:/config"
  if ($txt -notmatch "(?s)rtsp_worker:.*?volumes:.*?\./config:/config") {
    Write-Warn "ВАЖНО: в docker-compose.prod.yml у rtsp_worker нет volume ./config:/config."
    Write-Warn "Иначе snapshot в UI будет 404 (gatebox не видит /config/live/frame.jpg)."
    Write-Warn "Исправление: добавь в rtsp_worker -> volumes: - ./config:/config"
  } else {
    Write-Ok "Проверка snapshot: rtsp_worker видит ./config (OK)"
  }
}

function Ensure-Examples($dir){
  $compose = Join-Path $dir "docker-compose.prod.yml"
  if (-not (Test-Path $compose)) { throw "Не найден docker-compose.prod.yml в $dir" }

  $envFile = Join-Path $dir ".env"
  if (-not (Test-Path $envFile)) {
    $envExample = Join-Path $dir ".env.example"
    if (Test-Path $envExample) {
      Copy-Item $envExample $envFile
      Write-Ok "Создан .env из .env.example"
    } else {
      Write-Warn "Не найден .env.example — создам минимальный .env"
      @(
        "TAG=stable",
        "GATEBOX_PORT=8080",
        "UPDATER_PORT=9010",
        "RTSP_URL=",
        "MQTT_ENABLED=0",
        "MQTT_HOST=",
        "MQTT_PORT=1883",
        "MQTT_USER=",
        "MQTT_PASS=",
        "MQTT_TOPIC=gate/open"
      ) | Set-Content -Encoding UTF8 $envFile
      Write-Ok "Создан .env (минимальный)"
    }
  } else {
    Write-Ok ".env уже существует — не трогаю"
  }

  $cfgDir = Join-Path $dir "config"
  New-Item -ItemType Directory $cfgDir -Force | Out-Null
  New-Item -ItemType Directory (Join-Path $cfgDir "live") -Force | Out-Null
  New-Item -ItemType Directory (Join-Path $dir "debug") -Force | Out-Null
  Write-Ok "Созданы папки: config, config\live, debug"

  $settings = Join-Path $cfgDir "settings.json"
  if (-not (Test-Path $settings)) {
    $settingsEx = Join-Path $cfgDir "settings.example.json"
    if (Test-Path $settingsEx) {
      Copy-Item $settingsEx $settings
      Write-Ok "Создан config\settings.json из example"
    } else {
      Write-Warn "Нет settings.example.json — создам базовый settings.json"
      @'
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
'@ | Set-Content -Encoding UTF8 $settings
      Write-Ok "Создан config\settings.json (базовый)"
    }
  } else {
    Write-Ok "config\settings.json уже существует — не трогаю"
  }

  $wl = Join-Path $cfgDir "whitelist.json"
  if (-not (Test-Path $wl)) {
    $wlEx = Join-Path $cfgDir "whitelist.example.json"
    if (Test-Path $wlEx) {
      Copy-Item $wlEx $wl
      Write-Ok "Создан config\whitelist.json из example"
    } else {
      '{ "enabled": 0, "plates": [] }' | Set-Content -Encoding UTF8 $wl
      Write-Ok "Создан config\whitelist.json (базовый)"
    }
  } else {
    Write-Ok "config\whitelist.json уже существует — не трогаю"
  }

  $modelsDir = Join-Path $dir "models"
  if (-not (Test-Path $modelsDir)) { Write-Warn "Папка models отсутствует. Убедись, что модели лежат в .\models" }
  else { Write-Ok "models\ найден" }

  Check-SnapshotContract $compose
}

function Set-EnvKV($envFile,$key,$value){
  $lines = Get-Content $envFile -ErrorAction SilentlyContinue
  if ($lines -match "^$key=") {
    $lines = $lines | ForEach-Object { $_ -replace "^$key=.*", "$key=$value" }
  } else {
    $lines += "$key=$value"
  }
  $lines | Set-Content -Encoding UTF8 $envFile
}

function Edit-EnvInteractive($dir){
  $envFile = Join-Path $dir ".env"
  $content = Get-Content $envFile

  $getVal = { param($k) (($content | Where-Object { $_ -like "$k=*" } | Select-Object -First 1) -replace "^$k=","") }

  Write-Host "Настройка .env (можно просто нажимать Enter)" -ForegroundColor White
  Write-Host ""

  $tag = & $getVal "TAG"
  $in = Read-Host "Версия (TAG) [$($tag ?? "stable")]"
  if ($in) { $tag = $in } elseif (-not $tag) { $tag = "stable" }

  $rtsp = & $getVal "RTSP_URL"
  $in = Read-Host "RTSP_URL [$rtsp]"
  if ($in) { $rtsp = $in }

  $mqttEnabled = & $getVal "MQTT_ENABLED"
  $in = Read-Host "Включить MQTT? (0/1) [$($mqttEnabled ?? "0")]"
  if ($in) { $mqttEnabled = $in } elseif (-not $mqttEnabled) { $mqttEnabled = "0" }

  Set-EnvKV $envFile "TAG" $tag
  Set-EnvKV $envFile "RTSP_URL" $rtsp
  Set-EnvKV $envFile "MQTT_ENABLED" $mqttEnabled

  if ($mqttEnabled -eq "1") {
    $mqttHost = & $getVal "MQTT_HOST"
    $in = Read-Host "MQTT_HOST [$mqttHost]"
    if ($in) { $mqttHost = $in }

    $mqttPort = & $getVal "MQTT_PORT"
    $in = Read-Host "MQTT_PORT [$($mqttPort ?? "1883")]"
    if ($in) { $mqttPort = $in } elseif (-not $mqttPort) { $mqttPort = "1883" }

    $mqttUser = & $getVal "MQTT_USER"
    $in = Read-Host "MQTT_USER [$mqttUser]"
    if ($in) { $mqttUser = $in }

    Write-Warn "Пароль будет сохранён в .env (обычно это нормально для локальной установки)."
    $sec = Read-Host "MQTT_PASS (ввод скрыт)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    $mqttPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)

    $mqttTopic = & $getVal "MQTT_TOPIC"
    $in = Read-Host "MQTT_TOPIC [$($mqttTopic ?? "gate/open")]"
    if ($in) { $mqttTopic = $in } elseif (-not $mqttTopic) { $mqttTopic = "gate/open" }

    Set-EnvKV $envFile "MQTT_HOST" $mqttHost
    Set-EnvKV $envFile "MQTT_PORT" $mqttPort
    Set-EnvKV $envFile "MQTT_USER" $mqttUser
    Set-EnvKV $envFile "MQTT_PASS" $mqttPass
    Set-EnvKV $envFile "MQTT_TOPIC" $mqttTopic
  }

  Write-Ok ".env обновлён"
}

function Pull-And-Up($dir){
  $dc = Compose-Cmd
  Push-Location $dir
  Write-Info "Pull образов (это может занять время)..."
  if ($dc -eq "docker compose") { docker compose -f docker-compose.prod.yml pull }
  else { docker-compose -f docker-compose.prod.yml pull }
  Write-Ok "Образы загружены"

  Write-Info "Запуск сервисов..."
  if ($dc -eq "docker compose") { docker compose -f docker-compose.prod.yml up -d }
  else { docker-compose -f docker-compose.prod.yml up -d }
  Write-Ok "Сервисы запущены"
  Pop-Location
}

function Health($dir){
  $envFile = Join-Path $dir ".env"
  $gateboxPort = (Get-Content $envFile | Where-Object { $_ -like "GATEBOX_PORT=*" } | Select-Object -First 1) -replace "^GATEBOX_PORT=",""
  if (-not $gateboxPort) { $gateboxPort = "8080" }

  $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1).IPAddress
  if (-not $ip) { $ip = "127.0.0.1" }

  $healthUrl = "http://$ip`:$gateboxPort/api/v1/health"
  Write-Info "Проверка health: $healthUrl"
  try { Invoke-RestMethod $healthUrl -TimeoutSec 3 | Out-Null; Write-Ok "health OK" }
  catch { Write-Warn "health пока не ответил (проверь логи)" }

  $snapUrl = "http://$ip`:$gateboxPort/api/rtsp/frame.jpg"
  Write-Info "Проверка snapshot: $snapUrl"
  try {
    Invoke-WebRequest $snapUrl -TimeoutSec 3 | Out-Null
    Write-Ok "Snapshot доступен: /api/rtsp/frame.jpg"
  } catch {
    Write-Warn "Snapshot пока недоступен (/api/rtsp/frame.jpg). Если в UI 404 — проверь volume ./config:/config у rtsp_worker и gatebox."
  }

  Write-Host ""
  Write-Host "Готово!" -ForegroundColor Green
  Write-Host "UI: http://$ip`:$gateboxPort" -ForegroundColor Cyan
  Write-Host "Логи: .\install.ps1 -Action logs" -ForegroundColor DarkGray
}

function Do-Install {
  Logo
  Need-Cmd docker
  Need-Cmd git
  [void](Compose-Cmd)

  $in = Read-Host "Папка установки [$Dir]"
  if ($in) { $script:Dir = $in }

  $in = Read-Host "Git репозиторий [$RepoUrl]"
  if ($in) { $script:RepoUrl = $in }

  Ensure-Repo $Dir $RepoUrl
  Ensure-Examples $Dir

  $in = Read-Host "Открыть мастер-настройку .env сейчас? (y/N)"
  if ($in -match "^[Yy]$") { Edit-EnvInteractive $Dir }
  else { Write-Warn "Пропускаю мастер — отредактируй $Dir\.env при необходимости" }

  Pull-And-Up $Dir
  Health $Dir
}

function Do-Update {
  Logo
  Need-Cmd docker
  [void](Compose-Cmd)

  $in = Read-Host "Папка установки [$Dir]"
  if ($in) { $script:Dir = $in }

  $compose = Join-Path $Dir "docker-compose.prod.yml"
  if (-not (Test-Path $compose)) { throw "Не найден docker-compose.prod.yml в $Dir" }
  Check-SnapshotContract $compose

  Push-Location $Dir
  $dc = Compose-Cmd
  Write-Info "Pull новых образов..."
  if ($dc -eq "docker compose") { docker compose -f docker-compose.prod.yml pull }
  else { docker-compose -f docker-compose.prod.yml pull }

  Write-Info "Recreate..."
  if ($dc -eq "docker compose") { docker compose -f docker-compose.prod.yml up -d --force-recreate --remove-orphans }
  else { docker-compose -f docker-compose.prod.yml up -d --force-recreate --remove-orphans }
  Pop-Location

  Write-Ok "Обновление завершено"
  Health $Dir
}

function Do-Uninstall {
  Logo
  Need-Cmd docker
  [void](Compose-Cmd)

  $in = Read-Host "Папка установки [$Dir]"
  if ($in) { $script:Dir = $in }

  $compose = Join-Path $Dir "docker-compose.prod.yml"
  if (Test-Path $compose) {
    Push-Location $Dir
    $dc = Compose-Cmd
    Write-Info "Останавливаю и удаляю сервисы + volumes..."
    if ($dc -eq "docker compose") { docker compose -f docker-compose.prod.yml down --remove-orphans --volumes }
    else { docker-compose -f docker-compose.prod.yml down --remove-orphans --volumes }
    Pop-Location
    Write-Ok "Сервисы удалены"
  } else {
    Write-Warn "compose-файл не найден — пропускаю down"
  }

  $in = Read-Host "Удалить папку $Dir полностью? (y/N)"
  if ($in -match "^[Yy]$") { Remove-Item -Recurse -Force $Dir; Write-Ok "Папка удалена" }
  else { Write-Warn "Папка оставлена: $Dir" }
}

function Do-Status {
  Logo
  $in = Read-Host "Папка установки [$Dir]"
  if ($in) { $script:Dir = $in }
  Push-Location $Dir
  $dc = Compose-Cmd
  if ($dc -eq "docker compose") { docker compose -f docker-compose.prod.yml ps }
  else { docker-compose -f docker-compose.prod.yml ps }
  Pop-Location
}

function Do-Logs {
  Logo
  $in = Read-Host "Папка установки [$Dir]"
  if ($in) { $script:Dir = $in }
  Push-Location $Dir
  $dc = Compose-Cmd
  if ($dc -eq "docker compose") { docker compose -f docker-compose.prod.yml logs -f --tail 200 gatebox rtsp_worker updater }
  else { docker-compose -f docker-compose.prod.yml logs -f --tail 200 gatebox rtsp_worker updater }
  Pop-Location
}

switch ($Action) {
  "install"   { Do-Install }
  "update"    { Do-Update }
  "uninstall" { Do-Uninstall }
  "status"    { Do-Status }
  "logs"      { Do-Logs }
}