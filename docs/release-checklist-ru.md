# Release checklist (RU)

Короткий чеклист перед публикацией новой версии `lpr_gatebox`.

## 1) Код и сборка
- [ ] `npm --prefix ui run lint`
- [ ] `npm --prefix ui run build`
- [ ] `python -m compileall app`
- [ ] (опционально) `./scripts/smoke_runtime_ui.sh` на тестовом стенде

## 2) Runtime-проверки
- [ ] `/health` и `/api/v1/health` отвечают `ok: true`
- [ ] `Settings` сохраняются и применяются без ошибок
- [ ] ROI (включая `ROI_POLY_STR`) сохраняется и отображается на кадре
- [ ] `rtsp_worker` подхватывает overrides без рестарта контейнера
- [ ] Быстрое добавление в белый список работает из Dashboard/Events
- [ ] Галерея recent plates отображается

## 3) Инфраструктура и deploy
- [ ] `docker-compose.prod.yml` актуален
- [ ] Сеть `updater_net` не external и создаётся compose автоматически
- [ ] `.env` содержит корректный `TAG`
- [ ] GHCR-образы для нужного тега опубликованы

## 4) Документация и коммуникация
- [ ] Обновлён `CHANGELOG.md`
- [ ] Обновлены `docs/product-plan-ui-runtime.md` и инструкции оператора (если нужно)
- [ ] Подготовлен текст релиза (что изменилось/как откатиться)


## Smoke-режим для интеграций (MQTT/Telegram)
- По умолчанию интеграционные проверки в `scripts/smoke_runtime_ui.sh` работают в non-fatal режиме (WARN).
- Для строгой приёмки включи: `STRICT_INTEGRATIONS=1 bash scripts/smoke_runtime_ui.sh`.
- В строгом режиме падение любого из endpoint'ов `/api/v1/mqtt/check`, `/api/v1/mqtt/test_publish`, `/api/v1/telegram/bot_info` завершит smoke с кодом 1.
