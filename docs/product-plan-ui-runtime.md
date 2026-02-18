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
- Profile presets in Settings UI (day/night + custom save/apply + rollback + import/export profiles JSON).

## Iteration 5
- UX hardening for non-technical testers.
- Add smoke E2E scenarios for core setup flow.
- Add release checklist and operator guide.


## Iteration 6
- Backend validation for settings ranges (`/api/v1/settings`): reject out-of-range values with clear field-level errors.
- Validate polygon ROI format (`ROI_POLY_STR`) and require at least 3 points.
- Keep UI/runtime behavior unchanged but safer against invalid API payloads.


## Iteration 7
- Add smoke script for runtime/UI API sanity checks (`scripts/smoke_runtime_ui.sh`).
- Add release checklist and short operator guide in Russian.


## Iteration 8
- В разделе настроек добавить MQTT-диагностику: кнопка проверки связи с брокером и кнопка отправки тестового топика.
- В Telegram-настройках добавить явное поле `chat_id` (не только токен), чтобы можно было задать вручную при необходимости.
- Показывать ссылку на бота (`https://t.me/<bot_username>`) рядом с Telegram-настройками для быстрого перехода и запуска `/start`.


## Iteration 9
- Перенести интеграционные действия в раздел «Настройки → Диагностика»: MQTT check/test publish и Telegram test.
- Добавить в Telegram-настройках явные поля `bot_token` + `chat_id` и ссылку на бота, получаемую через `getMe`.


## Iteration 10
- Расширить smoke-проверку runtime/UI: добавить non-fatal проверки `mqtt/check`, `mqtt/test_publish` и `telegram/bot_info`.
- Явно фиксировать в smoke-логе, что интеграционные WARN допустимы в средах без MQTT/Telegram токена.


## Iteration 11
- Добавить в smoke-проверку переключаемый strict-режим для интеграций (`STRICT_INTEGRATIONS=1`).
- Обновить release-checklist: когда использовать non-fatal и когда strict для MQTT/Telegram.


## Iteration 12
- Phase 1 implemented: базовые настройки CloudPub в UI + backend status/connect/disconnect (sdk_pending).
- Phase 2 implemented: auto-expire сессии, `management_url` в status и audit trail последних действий connect/disconnect/expire.
- Phase 3 started: добавлены `public_url`, режим `simulation/sdk` в статусе и ручная очистка CloudPub audit из UI.
- Внедрить удалённый доступ через CloudPub (https://cloudpub.ru/docs, Python SDK): добавить в «Настройки → Интеграции» поля `cloudpub.enabled`, `cloudpub.server_ip`, `cloudpub.access_key`.
- Добавить backend-эндпоинты управления CloudPub-сессией: `connect`, `status`, `disconnect`, чтобы клиент мог включать/выключать удалённый доступ без ручных команд.
- В UI показать состояние туннеля (online/offline, последняя ошибка, время последнего успешного подключения) и кнопку «Подключить/Переподключить».
- Добавить политики безопасности: хранение ключа в `settings.json` в masked-виде в API-ответе, явный аудит кто/когда включал удалённый доступ, опциональный auto-expire сессии.
- Расширить smoke-проверку: non-fatal проверка `cloudpub/status` и strict-проверка при `STRICT_INTEGRATIONS=1` для релизного gate.
