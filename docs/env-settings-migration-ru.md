# Перенос ENV-параметров в настройки программы (UI): рекомендации

Ниже — практичный план, какие переменные из `docker-compose` стоит переносить в `settings.json` и показывать в UI,
а какие лучше оставить в ENV (инфраструктурные/секреты/редкие).

## 1) Что переносить в UI в первую очередь

Это параметры, которые оператор реально крутит в эксплуатации:

- **Gate / допуск**: `MIN_CONF`, `CONFIRM_N`, `CONFIRM_WINDOW_SEC`, `COOLDOWN_SEC`.
- **Проверка региона**: `REGION_CHECK`, `REGION_STAB`, `REGION_STAB_WINDOW_SEC`, `REGION_STAB_MIN_HITS`, `REGION_STAB_MIN_RATIO`.
- **RTSP частоты**: `READ_FPS`, `DET_FPS`, `SEND_FPS`.
- **YOLO пороги**: `DET_CONF`, `DET_IOU`.
- **Auto mode**: `AUTO_MODE`, `AUTO_DROP_ON_BLUR`, `AUTO_DROP_ON_GLARE`, `AUTO_RECTIFY`, `AUTO_PAD_ENABLE`, `AUTO_UPSCALE_ENABLE`.
- **Tracking/Freeze**: `TRACK_ENABLE`, `TRACK_HOLD_SEC`, `TRACK_ALPHA`, `TRACK_IOU_MIN`, `FREEZE_ENABLE`.
- **Live/диагностика**: `LIVE_DRAW_YOLO`, `LIVE_SAVE_QUAD`, `LIVE_JPEG_QUALITY`, `SAVE_SEND_BYTES`.

## 2) Что лучше оставить в ENV

- Пути и окружение контейнера: `MODEL_PATH`, `SETTINGS_PATH`, `WHITELIST_PATH`, `PYTHONPATH`.
- Сетевые/инфраструктурные endpoint'ы: `INFER_URL`, `SETTINGS_BASE_URL`, `UPDATER_URL`.
- Секреты: `MQTT_PASS`, `TELEGRAM_BOT_TOKEN` (можно хранить в settings, но только при шифровании/secret-store).
- Низкоуровневые backend-переключатели: `CAPTURE_BACKEND`, `OPENCV_FFMPEG_CAPTURE_OPTIONS`.

## 3) UX-правила для UI-контролов

- **Тумблеры**: все бинарные флаги (`*_ENABLE`, `*_CHECK`, `*_STAB`, `*_DRAW_*`).
- **Слайдеры**: пороги/частоты/тайминги (`*_CONF`, `*_FPS`, `*_SEC`, `*_RATIO`).
- **Числовой input**: точная доводка после слайдера (optional, рядом справа).
- **Подсказка на русском**: у каждого поля 1 строка «что делает» + безопасный диапазон.

## 4) Рекомендуемые диапазоны (безопасные дефолты)

- `MIN_CONF`: **0.50..0.99** (step 0.01)
- `DET_CONF`: **0.05..0.95** (step 0.01)
- `DET_IOU`: **0.10..0.90** (step 0.01)
- `READ_FPS`: **1..30**
- `DET_FPS`: **1..15**
- `SEND_FPS`: **0.5..15**
- `CONFIRM_WINDOW_SEC`: **0.5..8.0**
- `COOLDOWN_SEC`: **1..60**
- `REGION_STAB_MIN_RATIO`: **0.30..1.00**

## 5) Этапы внедрения

1. **Этап A (быстро)**: UI для самых нужных параметров + сохранение в `settings.rtsp_worker.overrides` и `settings.gate`.
2. **Этап B**: валидация диапазонов на backend (чтобы UI не мог сохранить опасные значения).
3. **Этап C**: «профили» (`день`, `ночь`, `склад`, `двор`) и кнопка rollback на последний рабочий профиль.
4. **Этап D**: аудит изменений (кто/когда поменял параметр), экспорт/импорт профиля JSON.

## 6) Почему так

Главная идея: в UI переносим **операционные** параметры (которые реально меняют в процессе эксплуатации),
а в ENV оставляем **инфраструктуру и секреты**, чтобы не ломать деплой и безопасность.
## 7) Предложенная структура в UI

- **Базовые**: пороги допуска, confirm/cooldown, базовые FPS и DET_CONF, включение AUTO_MODE/Tracking.
- **Продвинутые**: региональная стабилизация, DET_IOU, Rectify/Upscale и тонкие auto-флаги.
- **Диагностика**: MQTT-подключение, LIVE-отладка, `SAVE_*`, `LOG_EVERY_SEC`, `SAVE_DIR`.

