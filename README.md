# LPR GateBox

Система для распознавания автомобильных номеров и открытия ворот.

## Архитектура (ВАЖНО)
- `rtsp_worker` — читает RTSP, делает детекцию номера (YOLO), crop/refine/rectify и отправляет JPEG в gatebox
- `gatebox` — OCR, нормализация, проверка формата РФ, confirm, whitelist, и только при ok=True публикует MQTT
- `updater` — обновляет docker-compose сервисы (pull/up) и пишет лог

---

## Быстрый старт (чистая установка на Linux/Proxmox)

### 1) Клонировать репозиторий
```bash
cd ~
git clone https://github.com/pirsasha/lpr_gatebox.git
cd lpr_gatebox