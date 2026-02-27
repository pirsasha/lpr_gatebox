# Changelog

## v0.4.4 — 2026-02-27
### Fixed
- `rtsp_worker` sanity-check for plate crop now uses adaptive lower aspect threshold for high-confidence, non-tiny detections (`1.6` instead of strict `1.8` only in that case), reducing false rejects like `bad_aspect_low` on real plates.
- Added rate-limited unsane debug artifacts (`unsane_frame_vis_*.jpg`, `unsane_crop_*.jpg`) and expanded alive log with sanity metrics (`aspect`, `thr`, `area`, `rule`).


## v0.4.3 — 2026-02-27
### Fixed
- Dashboard mobile overflow: constrained home layout width (`min-width:0`), made recent-plates grid responsive, and wrapped events table in local horizontal scroll to prevent whole-page overflow on narrow screens.



## v0.4.2 — 2026-02-27
### Changed
- Mobile navbar/tabs are now responsive: horizontal in-place scroll on screens <=768px with active-tab auto-scroll into view, while desktop layout remains single-line.


## v0.4.1 — 2026-02-27
### Changed
- Dashboard (home) now renders RTSP preview with YOLO bbox overlay (same live frame + boxes endpoints as camera UI), with responsive layout tuned for desktop/mobile.



## v0.4.0 — 2026-02-27
### Added
- `PUT /api/v1/settings` now includes `overrides_apply` (`applied`, `queued_restart`, `unknown`) to explicitly separate `rtsp_worker.overrides` hot-applied keys vs restart-only keys for UI/operator visibility.
- Settings UI now shows badges after save: hot apply / restart required / unknown override keys.


## v0.3.28 — 2026-02-27
### Fixed
- `rtsp_worker` HTTP client switched to pooled `requests.Session` with keep-alive for settings/heartbeat/infer calls, plus bounded timeout normalization to reduce stuck network calls and TCP reconnect overhead.



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
