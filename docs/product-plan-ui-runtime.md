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


## Iteration 13
- Telegram diagnostics hardening: `/api/v1/telegram/test` now returns synchronous delivery errors/details (instead of only queue ack).
- Telegram poller handles `getUpdates` 409 conflict with clearer warning and longer backoff to reduce log spam.
- CloudPub UX simplified: explicit setup steps + docs link + clearer server address field.

- Iteration 13 phase 2: System UI показывает точный результат Telegram test (фото/текст/fallback) и явную подсказку по 409 getUpdates conflict.
- Iteration 13 phase 3: убран дублирующий Telegram-блок из «Система», чтобы интеграционные действия остались только в «Настройки → Диагностика»; ссылка на Telegram-канал в шапке сделана кликабельной.

## Iteration 14 (next)
- Закрыть CloudPub phase 3 до статуса implemented: выровнять поля `status`/`audit` в UI и backend и зафиксировать конечный контракт в документации API.
- Добавить проверку CloudPub connect/disconnect в `scripts/smoke_runtime_ui.sh` как non-fatal по умолчанию и как strict при `STRICT_INTEGRATIONS=1`.
- Доделать UX для операторов: единые тексты ошибок/подсказок в «Настройки → Интеграции» и явная индикация причины `sdk_pending`/`disabled`/`online`.
- Закрыть gap по релизному качеству: добавить минимальный e2e smoke сценарий для потока «камера → событие → Telegram/MQTT диагностика».
- После стабилизации зафиксировать «Iteration 12/13 done» в этом документе и открыть короткий hardening-спринт по производительности RTSP worker.
- Iteration 14 phase 1: CloudPub API-контракт (status/connect/disconnect) зафиксирован в документации, добавлены нормализованные поля `connection_state` + `state_reason`, UI использует единые статусы (`online/offline/sdk_pending/disabled`).
- Iteration 14 phase 2: добавлен операторский e2e smoke-сценарий `scripts/e2e_operator_flow.sh` и обновлён release-checklist для регулярного прогона и strict-режима.
- Iteration 14 phase 3: CloudPub UX унифицирован (единые тексты ошибок/подсказок), добавлено автообновление статуса и audit в Settings без перезагрузки страницы.
- Iteration 14 phase 4: добавлен единый strict release-gate скрипт `scripts/release_gate_strict.sh` (lint + build + compile + strict smoke + strict operator e2e).
- Iteration 14 phase 5: добавлен `scripts/finalize_iteration14.sh` для автоматического закрытия чеклиста/плана после успешного strict-gate.
