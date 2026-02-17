# LPR GateBox — Product Plan (UI + Runtime settings)

## Iteration 1 (current)
- Fix MQTT password masking flow (`***` no longer overwrites real password).
- Remove duplicated camera settings from generic Settings page.
- Persist ROI from UI to `settings.json` (`rtsp_worker.overrides.ROI_STR`).
- Apply ROI runtime in `rtsp_worker` without restart.

## Iteration 2
- Single “Camera & ROI” page with RTSP test + live preview + ROI editor.
- Add backend `camera.roi` canonical field and migration from `ROI_STR`.
- Validation and user feedback for invalid ROI.

## Iteration 3
- Add “Last 5 recognized plates” gallery in UI (ring buffer, auto-cleanup).
- Store only small JPEG crops + metadata (ts, plate, conf, reason).
- Configurable retention count (`ui.last_crops_max`, default=5).

## Iteration 4
- Expand runtime-configurable knobs (move critical ENV options to settings).
- Add UI sections: camera, detection, OCR, gate, integrations.
- Add import/export settings and reset presets (day/night profiles).

## Iteration 5
- UX hardening for non-technical testers.
- Add smoke E2E scenarios for core setup flow.
- Add release checklist and operator guide.
