# LPR GateBox

Система для распознавания автомобильных номеров и открытия ворот.

## Архитектура (ВАЖНО)
- `rtsp_worker` — читает RTSP, делает детекцию номера (YOLO), crop/refine/rectify и отправляет JPEG в gatebox
- `gatebox` — OCR, нормализация, проверка формата РФ, confirm, whitelist, и только при ok=True публикует MQTT
- `updater` — обновляет docker-compose сервисы (pull/up) и пишет лог

---


## API заметки (v0.4.0)
- `PUT /api/v1/settings` теперь возвращает дополнительный блок `overrides_apply`:
  - `applied`: override-ключи `rtsp_worker.overrides`, которые применяются hot в runtime,
  - `queued_restart`: ключи, сохранённые в `settings.json`, для которых нужен рестарт/перезапуск worker,
  - `unknown`: ключи, не распознанные сервером классификации overrides.

Это сделано для UI-индикации «применилось сразу / нужен рестарт / неизвестный ключ».

---

## rtsp_worker: sanity ENV (v0.5.0)

Для кейсов "камера сверху" (aspect номера часто 1.6–1.75) можно настраивать sanity без изменения кода:

- `SANITY_ASPECT_MIN_BASE=1.80`
- `SANITY_ASPECT_MIN_ADAPTIVE=1.60`
- `SANITY_ADAPTIVE_CONF_MIN=0.75`
- `SANITY_ADAPTIVE_AREA_MIN=0.0065`
- `SANITY_MIN_WIDTH_PX=140`
- `SANITY_MIN_HEIGHT_PX=60`
- `SANITY_DEBUG_REJECT_EVERY_SEC=3`

Логика:
- базово используется `SANITY_ASPECT_MIN_BASE`,
- при высоком `conf` и достаточной площади bbox включается adaptive-порог `SANITY_ASPECT_MIN_ADAPTIVE`.

Debug-артефакты rejected_unsane пишутся в `SAVE_DIR`:
- `unsane_frame_vis_*.jpg`
- `unsane_crop_*.jpg`

---

## Быстрый старт (чистая установка на Linux/Proxmox)

### 1) Клонировать репозиторий
```bash
cd ~
git clone https://github.com/pirsasha/lpr_gatebox.git
cd lpr_gatebox