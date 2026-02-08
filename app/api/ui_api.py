# =========================================================
# Файл: app/api/ui_api.py
# Проект: LPR GateBox
# Версия: v0.3.2
# Изменено: 2026-02-07  (UTC+3)
# Автор: Александр
# Что сделано:
# - CHG: router без prefix (prefix задаёт main.py: /api и /api/v1)
# - NEW: SSE stream /events/stream для "как у взрослых" (без кнопок обновить)
# =========================================================

from __future__ import annotations

import os
import time
import json
import copy
import asyncio
import requests  # NEW
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.store import EventStore, EventItem, SettingsStore
from fastapi.responses import Response
from fastapi import HTTPException
# ВАЖНО: без prefix. Префиксы вешаем в main.py через include_router(..., prefix="/api") и "/api/v1"
router = APIRouter(tags=["ui"])
# =========================
# UPDATER proxy (metrics/update)
# =========================


UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:9010")


@router.get("/system/metrics")
def system_metrics():
    """
    UI -> gatebox -> updater
    Метрики хоста/контейнеров для вкладки "Система".
    """
    try:
        r = requests.get(f"{UPDATER_URL}/metrics", timeout=8.0)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"updater unavailable: {e}")
# =========================
# LIVE кадр/боксы (пишет rtsp_worker в /config/live)
# =========================
LIVE_DIR = Path(os.environ.get("LIVE_DIR", "/config/live"))
LIVE_FRAME_PATH = LIVE_DIR / "frame.jpg"
LIVE_META_PATH = LIVE_DIR / "meta.json"
LIVE_BOXES_PATH = LIVE_DIR / "boxes.json"


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# =========================
# EVENTS (единое хранилище)
# =========================
EVENTS_MAX = int(os.environ.get("EVENTS_MAX", "200"))
_event_store = EventStore(maxlen=EVENTS_MAX)


def _f(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    try:
        return float(x)
    except Exception:
        return default


def _s(x: Any, default: str = "") -> str:
    if x is None:
        return default
    try:
        return str(x)
    except Exception:
        return default


def _derive_status(payload: Dict[str, Any]) -> Tuple[str, str]:
    """Нормализуем статус/сообщение для UI."""
    if isinstance(payload.get("status"), str) and payload["status"]:
        return payload["status"], _s(payload.get("message") or payload.get("reason") or "")

    reason = _s(payload.get("reason"))
    mqtt_pub = payload.get("mqtt_published") is True
    valid = payload.get("valid")
    allowed = payload.get("allowed")

    if mqtt_pub:
        return "sent", "MQTT отправлено"
    if "cooldown" in reason:
        return "cooldown", reason or "cooldown"
    if valid is False:
        return "invalid", reason or "invalid"
    if allowed is False:
        return "denied", reason or "not allowed"
    if payload.get("ok") is False:
        return "info", reason or "not ok"
    return "info", reason or ""


def push_event_from_infer(payload: Dict[str, Any]) -> None:
    """Сохраняем событие из /infer в кольцевой буфер для UI.

    Принцип:
    - полезные события → level=info → видны в UI по умолчанию
    - мусор OCR → level=debug → скрыт, но доступен через include_debug=1
    """
    ts = _f(payload.get("ts"), None) or time.time()
    plate = _s(payload.get("plate") or payload.get("number") or payload.get("plate_norm") or payload.get("raw") or "—")
    raw = payload.get("raw")
    conf = payload.get("conf")

    status, message = _derive_status(payload)

    # уровень логирования события
    level = _s(payload.get("log_level") or payload.get("level") or "info")
    if level not in ("info", "debug"):
        level = "info"

    # диагностические метаданные (best-effort)
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {
            "variant": payload.get("variant") or payload.get("ocr_variant"),
            "warped": payload.get("warped") if payload.get("warped") is not None else payload.get("ocr_warped"),
            "timing_ms": payload.get("timing_ms"),
        }

    _event_store.add(
        EventItem(
            ts=float(ts),
            plate=str(plate),
            raw=_s(raw) if raw is not None else None,
            conf=_f(conf),
            status=str(status),
            message=str(message),
            level=level,
            meta=meta if isinstance(meta, dict) else None,
        )
    )


@router.get("/events")
def api_events(limit: int = 50, after_ts: Optional[float] = None, include_debug: bool = False):
    return {"ok": True, "items": _event_store.latest(limit=limit, after_ts=after_ts, include_debug=include_debug)}


@router.get("/events/stream")
async def api_events_stream(
    request: Request,
    after_ts: Optional[float] = None,
    include_debug: bool = False,
    poll_ms: int = 250,
    heartbeat_sec: int = 15,
):
    """SSE stream событий.

    Клиент:
      - подключается один раз
      - получает события мгновенно без кнопок/обновлений
      - при реконнекте может передать after_ts

    Формат:
      event: event
      data: {EventItem JSON}
    """

    last_ts: float = float(after_ts or 0.0)

    async def gen():
        nonlocal last_ts
        # совет браузеру по автоповтору (мс)
        yield "retry: 2000\n\n"

        last_send = time.time()

        while True:
            if await request.is_disconnected():
                break

            # latest() возвращает newest-first → для стрима отдаём по возрастанию ts
            batch = _event_store.latest(limit=200, after_ts=last_ts, include_debug=include_debug)
            if batch:
                batch.sort(key=lambda x: float(x.get("ts") or 0.0))
                for item in batch:
                    ts = float(item.get("ts") or 0.0)
                    if ts > last_ts:
                        last_ts = ts
                    data = json.dumps(item, ensure_ascii=False)
                    yield f"event: event\ndata: {data}\n\n"
                    last_send = time.time()

            # keepalive, чтобы прокси/браузер не рвал соединение
            if (time.time() - last_send) >= float(heartbeat_sec):
                yield f": ping {int(time.time())}\n\n"
                last_send = time.time()

            await asyncio.sleep(max(0.05, float(poll_ms) / 1000.0))

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # если будет nginx — важно, чтобы он не буферизовал SSE
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)

# =========================================================
# NEW: updater proxy (v0.3.1)
# UI -> gatebox -> updater (без CORS и без прямого доступа)
# =========================================================

UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:9010")
UPDATER_TIMEOUT_SEC = float(os.environ.get("UPDATER_TIMEOUT_SEC", "8.0"))

def _updater_get(path: str) -> requests.Response:
    return requests.get(f"{UPDATER_URL}{path}", timeout=UPDATER_TIMEOUT_SEC)

def _updater_post(path: str) -> requests.Response:
    return requests.post(f"{UPDATER_URL}{path}", timeout=UPDATER_TIMEOUT_SEC)

@router.get("/update/status")
def update_status():
    try:
        r = _updater_get("/status")
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"updater unavailable: {e}")

@router.get("/update/log")
def update_log():
    try:
        r = _updater_get("/log")
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"updater unavailable: {e}")

@router.post("/update/check")
def update_check():
    try:
        r = _updater_post("/check")
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"updater unavailable: {e}")

@router.post("/update/start")
def update_start():
    try:
        r = _updater_post("/start")
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"updater unavailable: {e}")

@router.get("/update/report")
def update_report():
    """
    Проксируем zip отчёт от updater.
    """
    try:
        r = _updater_get("/report")
        r.raise_for_status()
        content = r.content
        # отдаём как attachment
        headers = {
            "Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="gatebox_report.zip"',
        }
        return Response(content=content, headers=headers)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"updater unavailable: {e}")
# =========================
# RTSP heartbeat/status
# =========================
_last_rtsp_hb: Dict[str, Any] = {"ts": 0.0}


class RtspHeartbeatIn(BaseModel):
    ts: Optional[float] = None
    alive: Optional[bool] = None
    frozen: Optional[bool] = None
    last_frame_ts: Optional[float] = None
    note: Optional[str] = None
    camera_id: Optional[str] = None
    fps: Optional[float] = None
    errors: Optional[int] = None
    sent: Optional[int] = None
    frame: Optional[Dict[str, int]] = None
    roi: Optional[list] = None


@router.post("/rtsp/heartbeat")
def api_rtsp_heartbeat(hb: RtspHeartbeatIn):
    now = time.time()
    data = hb.dict()
    data["ts"] = float(data.get("ts") or now)
    _last_rtsp_hb.clear()
    _last_rtsp_hb.update(data)
    return {"ok": True}


@router.get("/rtsp/status")
def api_rtsp_status():
    now = time.time()
    hb_ts = float(_last_rtsp_hb.get("ts") or 0.0)
    if hb_ts <= 0:
        return {"ok": False, "alive": False, "reason": "no heartbeat"}

    age_ms = int(max(0.0, (now - hb_ts) * 1000.0))
    alive = age_ms < 5000

    return {
        "ok": True,
        "alive": alive,
        "age_ms": age_ms,
        "frozen": bool(_last_rtsp_hb.get("frozen") is True),
        "last_hb_ts": hb_ts,
        "last_frame_ts": _last_rtsp_hb.get("last_frame_ts"),
        "note": _last_rtsp_hb.get("note"),
        "camera_id": _last_rtsp_hb.get("camera_id"),
        "fps": _last_rtsp_hb.get("fps"),
        "errors": _last_rtsp_hb.get("errors"),
        "sent": _last_rtsp_hb.get("sent"),
        "frame": _last_rtsp_hb.get("frame"),
        "roi": _last_rtsp_hb.get("roi"),
    }


@router.get("/rtsp/frame.jpg")
def api_rtsp_frame_jpg():
    """Возвращает самый свежий кадр (JPG), который пишет rtsp_worker."""
    if not LIVE_FRAME_PATH.exists():
        raise HTTPException(status_code=404, detail="no frame yet")
    return FileResponse(str(LIVE_FRAME_PATH), media_type="image/jpeg")


@router.get("/rtsp/frame_meta")
def api_rtsp_frame_meta():
    """Метаданные кадра (ts,w,h)."""
    return {"ok": True, "meta": _read_json(LIVE_META_PATH, {"ts": 0})}


@router.get("/rtsp/boxes")
def api_rtsp_boxes():
    """BBox от YOLO для отрисовки на UI."""
    return {"ok": True, "boxes": _read_json(LIVE_BOXES_PATH, {"ts": 0, "items": []})}


# =========================
# SETTINGS (settings.json)
# =========================
_settings_store: Optional[SettingsStore] = None
_apply_callback: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None

_DEFAULT_SETTINGS: Dict[str, Any] = {}


def init_settings(path: str, defaults: Dict[str, Any]) -> None:
    """Инициализируем SettingsStore. Вызывается один раз из main.py."""
    global _settings_store, _DEFAULT_SETTINGS
    _DEFAULT_SETTINGS = copy.deepcopy(defaults or {})
    _settings_store = SettingsStore(path=path, defaults=_DEFAULT_SETTINGS)


def set_apply_callback(fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    """main.py устанавливает сюда функцию, которая применит настройки (MQTT/GateDecider/etc)."""
    global _apply_callback
    _apply_callback = fn


def _require_store() -> SettingsStore:
    global _settings_store
    if _settings_store is None:
        _settings_store = SettingsStore(path="/config/settings.json", defaults=_DEFAULT_SETTINGS)
    return _settings_store


def _strip_nones(x: Any) -> Any:
    """PowerShell/клиенты легко присылают null/None — не даём им затирать настройки."""
    if isinstance(x, dict):
        out: Dict[str, Any] = {}
        for k, v in x.items():
            if v is None:
                continue
            out[k] = _strip_nones(v)
        return out
    if isinstance(x, list):
        return [_strip_nones(v) for v in x]
    return x


def _extract_settings_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Принимаем оба формата:
    - {"settings": {...}}  (рекомендуемый)
    - {...}               (старый)
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="settings payload must be object")
    inner = payload.get("settings")
    if isinstance(inner, dict):
        return inner
    return payload


def _mask_settings_for_get(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Не светим пароль в GET/status."""
    s = copy.deepcopy(settings or {})
    mqtt = s.get("mqtt")
    if isinstance(mqtt, dict):
        if mqtt.get("pass"):
            mqtt["pass"] = "***"
    return s


def _drop_empty_mqtt_pass(patch: Dict[str, Any]) -> Dict[str, Any]:
    """mqtt.pass == '' не должен затирать сохранённый пароль."""
    if not isinstance(patch, dict):
        return patch
    mqtt_patch = patch.get("mqtt")
    if isinstance(mqtt_patch, dict) and "pass" in mqtt_patch:
        try:
            if str(mqtt_patch.get("pass") or "") == "":
                mqtt_patch.pop("pass", None)
        except Exception:
            mqtt_patch.pop("pass", None)
    return patch


@router.get("/settings")
def api_get_settings():
    st = _require_store()
    return {"ok": True, "settings": _mask_settings_for_get(st.get())}


@router.put("/settings")
def api_put_settings(patch: Dict[str, Any]):
    st = _require_store()

    data_in = _extract_settings_payload(patch)
    data_in = _strip_nones(data_in)
    data_in = _drop_empty_mqtt_pass(data_in)

    data = st.update(data_in)
    return {"ok": True, "settings": _mask_settings_for_get(data)}


@router.post("/settings/reset")
def api_reset_settings():
    st = _require_store()

    data = st.reset()
    if not isinstance(data, dict) or not data or not isinstance(data.get("mqtt"), dict):
        data = st.update(copy.deepcopy(_DEFAULT_SETTINGS))

    return {"ok": True, "settings": _mask_settings_for_get(data)}


@router.post("/settings/reload")
def api_reload_settings():
    st = _require_store()
    data = st.reload()
    if not isinstance(data, dict) or not data or not isinstance(data.get("mqtt"), dict):
        data = st.update(copy.deepcopy(_DEFAULT_SETTINGS))
    return {"ok": True, "settings": _mask_settings_for_get(data)}


@router.post("/settings/apply")
def api_apply_settings():
    st = _require_store()
    data = st.get()

    if _apply_callback is None:
        raise HTTPException(status_code=500, detail="apply_callback is not set (main.py should call set_apply_callback)")

    applied = _apply_callback(data)  # type: ignore[misc]
    return {"ok": True, "applied": applied, "settings": _mask_settings_for_get(data)}


# =========================
# STATUS
# =========================
@router.get("/status")
def api_status():
    now = time.time()
    hb_ts = float(_last_rtsp_hb.get("ts") or 0.0)
    age_ms = int(max(0.0, (now - hb_ts) * 1000.0)) if hb_ts > 0 else None
    rtsp_alive = bool(age_ms is not None and age_ms < 5000)

    return {
        "ok": True,
        "events": {"count": _event_store.count(), "max": EVENTS_MAX},
        "rtsp": {
            "alive": rtsp_alive,
            "age_ms": age_ms,
            "camera_id": _last_rtsp_hb.get("camera_id"),
            "fps": _last_rtsp_hb.get("fps"),
            "errors": _last_rtsp_hb.get("errors"),
            "sent": _last_rtsp_hb.get("sent"),
            "note": _last_rtsp_hb.get("note"),
        },
        "settings": _mask_settings_for_get(_require_store().get()),
        "ts": now,
    }
    # =========================
# WHITELIST API (для UI)
# =========================
WHITELIST_PATH = os.environ.get("WHITELIST_PATH", "/config/whitelist.json")
_whitelist_reload_cb = None

def set_whitelist_reload_callback(fn):
    global _whitelist_reload_cb
    _whitelist_reload_cb = fn

@router.get("/whitelist")
def api_get_whitelist():
    try:
        if os.path.exists(WHITELIST_PATH):
            with open(WHITELIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            plates = data if isinstance(data, list) else data.get("plates", [])
        else:
            plates = []
        return {"ok": True, "plates": plates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"whitelist read failed: {e}")

@router.put("/whitelist")
def api_put_whitelist(payload: Dict[str, Any]):
    try:
        plates = payload.get("plates", [])
        if not isinstance(plates, list):
            raise HTTPException(status_code=400, detail="plates must be a list")

        os.makedirs(os.path.dirname(WHITELIST_PATH), exist_ok=True)
        with open(WHITELIST_PATH, "w", encoding="utf-8") as f:
            json.dump(plates, f, ensure_ascii=False, indent=2)

        return {"ok": True, "plates": plates}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"whitelist write failed: {e}")

@router.post("/whitelist/reload")
def api_reload_whitelist():
    if _whitelist_reload_cb is None:
        return {"ok": False, "reason": "reload callback not set"}
    try:
        _whitelist_reload_cb()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"whitelist reload failed: {e}")
        # =========================
# SYSTEM / UPDATE / REPORT
# =========================
import requests

UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:9010")

@router.get("/system/versions")
def api_versions():
    return {
        "ok": True,
        "gatebox": os.environ.get("APP_VERSION", "dev"),
        "rtsp_worker": os.environ.get("APP_VERSION", "dev"),
        "ui": os.environ.get("APP_VERSION", "dev"),
    }

@router.post("/system/update/check")
def api_update_check():
    r = requests.post(f"{UPDATER_URL}/check", timeout=5)
    return r.json()

@router.post("/system/update/start")
def api_update_start():
    r = requests.post(f"{UPDATER_URL}/start", timeout=5)
    return r.json()

@router.get("/system/update/status")
def api_update_status():
    r = requests.get(f"{UPDATER_URL}/status", timeout=5)
    return r.json()

@router.get("/system/update/log")
def api_update_log():
    r = requests.get(f"{UPDATER_URL}/log", timeout=5)
    return r.json()

@router.get("/system/report")
def api_system_report():
    r = requests.get(f"{UPDATER_URL}/report", timeout=10)
    return Response(
        content=r.content,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=gatebox_report.zip"},
    )