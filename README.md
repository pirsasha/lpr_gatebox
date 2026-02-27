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

## rtsp_worker: stabilization & best-crop ENV (v0.5.1)

Для улучшения стабильности confirm (когда `track_new=1` слишком часто и `hits` не копятся):

- Новые дефолты tracking:
  - `TRACK_IOU_MIN=0.18` (было 0.10)
  - `TRACK_HOLD_SEC=1.6` (было 1.0)
  - `TRACK_ALPHA=0.75` (было 0.65)

- Новый режим стабилизации:
  - `STAB_MODE=track|plate|hybrid` (по умолчанию `track`)
  - `track` — только track-based поведение (backward compatible)
  - `plate` — plate-based накопление hits по `plate_norm`
  - `hybrid` — комбинация track + plate

- Опциональный best-crop буфер:
  - `BEST_CROP_ENABLE=0`
  - `BEST_CROP_WINDOW_SEC=1.5`
  - `BEST_CROP_MAX_SEND=1`

  В пределах окна выбирается лучший crop по score:
  `score = conf * area_ratio * sharpness`

- Логи решения отправки (rate-limit):
  - `DECISION_LOG_EVERY_SEC=2.0`

---

## rtsp_worker: Candidate debug

Для диагностики кейса `det>0`, но `best='-'` / `sanity=no_candidate_crop`:

- `CANDIDATE_DEBUG_ENABLE=0` — включить/выключить диагностические логи кандидатов
- `CANDIDATE_DEBUG_EVERY_SEC=2.0` — интервал rate-limit для cand/debug snapshot
- `CANDIDATE_DEBUG_SAMPLE=0` — логировать 1 sample отфильтрованной детекции (reason + bbox + conf)
- `CANDIDATE_DEBUG_COORDS=0` — печатать bbox в системах ROI/full-frame + warning на mismatch
- `CANDIDATE_DEBUG_SAVE=0` — сохранить 1 JPEG с bbox до фильтров, когда `det_total>0` и `after=0`

Полезная команда:

```bash
docker compose logs -f rtsp_worker | rg "cand_dbg|no_candidate_crop|rejected_unsane"
```

---

## Быстрый старт (чистая установка на Linux/Proxmox)

### 1) Клонировать репозиторий
```bash
cd ~
git clone https://github.com/pirsasha/lpr_gatebox.git
cd lpr_gatebox
