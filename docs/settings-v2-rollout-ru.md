# Settings v2: единый source-of-truth (RU)

## Правила приоритета
- Рабочий тюнинг (детекция/OCR/gate decision): **только settings.json v2**.
- ENV для тюнинга помечаются deprecated и в effective выводятся как `ignored_tuning_env`.
- Системные lock-параметры (пути/бэкенды): `MODEL_PATH`, `SETTINGS_PATH`, `INFER_URL`, `CAPTURE_BACKEND`, `SAVE_DIR`, `WHITELIST_PATH`.

## Вкладки UI
### Базовые
- camera.rtsp_url, camera.enabled, ROI/ROI_POLY
- profiles.*.rtsp_worker.det_conf/det_iou/det_img_size
- profiles.*.gate.min_conf/confirm_n/cooldown_sec
- profiles.*.ocr.warp_try/postcrop

### Продвинутые
- rectify/refine/upscale/min plate size
- tracking/freeze/auto day-night
- transport/read timeouts/opencv ffmpeg options

### Диагностика
- live dir/every/jpg quality/save quad
- save_every/save_full_frame/save_send_bytes
- sanity_fail_reason, deny_reason, pre_variant, pre_timing

## Миграция v1 -> v2
1. Создать backup `settings.json.v1.bak.<timestamp>`.
2. Перенести:
   - `gate` -> `profiles.day.gate`
   - `ocr` -> `profiles.day.ocr`
   - `rtsp_worker.overrides` -> `profiles.day.rtsp_worker`
   - `mqtt/telegram/cloudpub` -> `system.*`
3. Конфликты: приоритет `settings.*` > legacy duplicate > defaults.
4. `ui.last_profile_snapshot` не участвует в runtime, только `ui.draft`.

## SemVer
Изменение формата settings и API effective/apply = **MAJOR** (предложение: `v0.4.0`).
