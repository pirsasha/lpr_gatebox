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

## Быстрый старт (чистая установка на Linux/Proxmox)

### 1) Клонировать репозиторий
```bash
cd ~
git clone https://github.com/pirsasha/lpr_gatebox.git
cd lpr_gatebox