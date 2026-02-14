# ==========================================================
# LPR GateBox Installer (Windows PowerShell)
# Version: v0.3.27
# - default TAG=stable
# - writes .env to avoid compose WARNs
# ==========================================================

$ProjectDir = "$env:USERPROFILE\lpr_gatebox"
$RepoUrl    = "https://github.com/pirsasha/lpr_gatebox.git"
$ComposeFile = "docker-compose.prod.yml"
$EnvFile = ".env"
$CfgDir = "config"

function Die($msg){ Write-Host "❌ $msg" -ForegroundColor Red; exit 1 }
function Info($msg){ Write-Host "ℹ️  $msg" }
function Ok($msg){ Write-Host "✅ $msg" -ForegroundColor Green }
function Warn($msg){ Write-Host "⚠️  $msg" -ForegroundColor Yellow }

# prereq
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git не найден" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die "docker не найден" }

# repo
if (Test-Path "$ProjectDir\.git") {
  Ok "Repo exists: $ProjectDir"
  Push-Location $ProjectDir
  try { git pull --ff-only | Out-Host } catch { Warn "git pull failed (not critical)" }
  Pop-Location
} else {
  Info "Cloning repo..."
  git clone $RepoUrl $ProjectDir | Out-Host
  Ok "Cloned."
}

Push-Location $ProjectDir

# .env
if (-not (Test-Path $EnvFile)) {
  Info "Creating .env"
@"
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
"@ | Set-Content -Encoding UTF8 $EnvFile
  Ok ".env created"
} else {
  $envText = Get-Content $EnvFile -Raw
  if ($envText -match "(?m)^TAG=$") {
    Warn "TAG empty in .env, setting TAG=stable"
    $envText = $envText -replace "(?m)^TAG=$", "TAG=stable"
    $envText | Set-Content -Encoding UTF8 $EnvFile
  }
  Ok ".env exists"
}

# config
New-Item -ItemType Directory -Force -Path $CfgDir, "$CfgDir\live", "debug" | Out-Null
Ok "Folders ok"

if (-not (Test-Path "$CfgDir\settings.json")) {
  if (Test-Path "$CfgDir\settings.example.json") {
    Copy-Item "$CfgDir\settings.example.json" "$CfgDir\settings.json"
    Ok "settings.json from example"
  } else {
    Warn "settings.example.json not found, creating minimal settings.json"
@"
{
  "telegram": { "bot_token": "", "enabled": false }
}
"@ | Set-Content -Encoding UTF8 "$CfgDir\settings.json"
  }
}

# start
Info "docker compose pull"
docker compose -f $ComposeFile pull | Out-Host
Info "docker compose up"
docker compose -f $ComposeFile up -d --force-recreate --remove-orphans | Out-Host
Ok "Started"

Info "Images:"
docker compose -f $ComposeFile images | Out-Host

Pop-Location
Ok "UI: http://127.0.0.1:8080"