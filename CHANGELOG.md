# Changelog

## v0.4.0 — 2026-02-24
### Added
- settings.json v2 foundation: profiles (`day/night/custom`), `active_profile`, `revision`, and system split.
- New config APIs: `GET /api/v1/config/effective` and `POST /api/v1/config/apply`.
- v1->v2 runtime migration with backup on first load.
- Schema draft: `config/settings.schema.v2.json` and rollout doc `docs/settings-v2-rollout-ru.md`.

### Changed
- Runtime gate tuning now reads from effective settings profile (single source of truth), while tuning ENV vars are treated as deprecated/ignored.

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
