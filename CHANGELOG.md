# Changelog

<<<<<<< codex/conduct-project-audit-for-errors-and-performance-mmkdb9
## v0.3.28 — 2026-02-27
### Fixed
- `rtsp_worker` HTTP client switched to pooled `requests.Session` with keep-alive for settings/heartbeat/infer calls, plus bounded timeout normalization to reduce stuck network calls and TCP reconnect overhead.


=======
>>>>>>> main
## v0.3.27 — 2026-02-27
### Fixed
- UI API `/api/v1/camera/test` now enforces `timeout_sec` with fail-fast behavior via executor timeout (returns `{ok:false,error:"timeout"}` instead of hanging request on slow/broken RTSP).


## v0.3.26 — 2026-02-18
### Changed
- gatebox runtime config now follows unified priority: `settings.json` (cfg) -> ENV fallback -> default.
- Added one-shot strict release gate script: `scripts/release_gate_strict.sh`.

### Fixed
- Effective runtime settings now match UI/settings values when set (e.g. Telegram token priority, MQTT runtime fields).
- Removed hardcoded secrets from `docker-compose.yml` (dev): use `${VAR:-}` placeholders.


## v0.2.4 — 2026-02-04
### Added
- app/core/plate_rectifier.py: поиск quad + выпрямление номера через warpPerspective.
- app/main.py: OCR orientation (0/180, +90/270 для вертикальных кадров) и попытка quad/warp перед OCR.
- app/ocr_onnx.py: infer_bgr() для OCR без повторного decode/encode.
### Changed
- app/rtsp_worker.py: использует общий rectifier из app/core/plate_rectifier.py.
