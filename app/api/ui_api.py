# =========================================================
# Файл: app/api/ui_api.py
# Проект: LPR GateBox
# Версия: v0.3.9-ui-events-filter+ru-i18n
# Изменено: 2026-02-11 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - NEW: UI-фильтр событий (скрываем OCR-мусор, показываем только РФ/похожие номера)
# - NEW: ENV-переключатели фильтра:
#        UI_EVENTS_ONLY_RU=1
#        UI_EVENTS_RU_STRICT=0
#        UI_EVENTS_INCLUDE_DENIED=1
#        UI_EVENTS_INCLUDE_INVALID=0
# - NEW: русификация сообщений/статусов в UI (UI_I18N_RU=1)
# - CHG: push_event_from_infer() теперь может "молча" не добавлять мусор в EventStore
# - НЕ ЛОМАЕМ контракт: /api/... и /api/v1/... работают одинаково (router без prefix)
# =========================================================

from __future__ import annotations

import os
import time
import json
import copy
import asyncio
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import requests
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, Response

from app.store import EventStore, EventItem, SettingsStore

# ВАЖНО: без prefix. Префиксы вешаем в main.py:
# app.include_router(ui_router, prefix="/api")
# app.include_router(ui_router, prefix="/api/v1")
router = APIRouter(tags=["ui"])

# =========================
# UPDATER proxy (metrics/update)
# =========================

UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:9010")
UPDATER_TIMEOUT_SEC = float(os.environ.get("UPDATER_TIMEOUT_SEC", "8.0"))


def _updater_get(path: str) -> requests.Response:
    return requests.get(f"{UPDATER_URL}{path}", timeout=UPDATER_TIMEOUT_SEC)


def _updater_post(path: str) -> requests.Response:
    return requests.post(f"{UPDATER_URL}{path}", timeout=UPDATER_TIMEOUT_SEC)


@router.get("/system/metrics")
def system_metrics():
    """
    Метрики для UI "Система -> Ресурсы".

    FIX: updater в текущей версии НЕ имеет /metrics (404), и это не должно ломать UI.
    Поэтому 404 => 200 + {ok:false, supported:false}. Реальная недоступность updater => 502.
    """
    url = f"{UPDATER_URL}/metrics"
    try:
        r = requests.get(url, timeout=6.0)

        # FIX: /metrics не поддерживается этим updater (у тебя BaseHTTPServer, есть только /status и /log)
        if r.status_code == 404:
            return {
                "ok": False,
                "supported": False,
                "error": "metrics_not_supported",
                "updater_url": UPDATER_URL,
                "hint": "updater supports only /status and /log in this build",
            }

        r.raise_for_status()

        # на будущее: если появится /metrics и он будет JSON
        try:
            return r.json()
        except Exception:
            return {"ok": True, "raw": r.text}

    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"updater unavailable: {e}")


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
    """Проксируем zip отчёт от updater."""
    try:
        r = _updater_get("/report")
        r.raise_for_status()
        return Response(
            content=r.content,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="gatebox_report.zip"'},
        )
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
# EVENTS (единое хранилище)
# =========================

EVENTS_MAX = int(os.environ.get("EVENTS_MAX", "200"))
_event_store = EventStore(maxlen=EVENTS_MAX)

# NEW: UI filter switches (по умолчанию включаем чистку мусора)
UI_EVENTS_ONLY_RU = os.environ.get("UI_EVENTS_ONLY_RU", "1").strip().lower() in ("1", "true", "yes", "y", "on")
UI_EVENTS_RU_STRICT = os.environ.get("UI_EVENTS_RU_STRICT", "0").strip().lower() in ("1", "true", "yes", "y", "on")
UI_EVENTS_INCLUDE_DENIED = os.environ.get("UI_EVENTS_INCLUDE_DENIED", "1").strip().lower() in ("1", "true", "yes", "y", "on")
UI_EVENTS_INCLUDE_INVALID = os.environ.get("UI_EVENTS_INCLUDE_INVALID", "0").strip().lower() in ("1", "true", "yes", "y", "on")

# NEW: RU i18n for UI messages
UI_I18N_RU = os.environ.get("UI_I18N_RU", "1").strip().lower() in ("1", "true", "yes", "y", "on")

# NEW: RU plate patterns
_RU_LETTERS = "АВЕКМНОРСТУХ"
_RE_RU_STRICT = re.compile(rf"^[{_RU_LETTERS}]\d{{3}}[{_RU_LETTERS}]{{2}}\d{{2,3}}$")
_RE_RU_LOOSE = re.compile(rf"^[{_RU_LETTERS}]\d{{2,3}}[{_RU_LETTERS}]{{1,2}}\d{{0,3}}$")

# NEW: RU translations (UI layer only)
_RU_REASON_MAP: Dict[str, str] = {
    # gatebox reasons
    "not_in_whitelist": "Нет в белом списке",
    "invalid_format_or_region": "Неверный формат/регион",
    "invalid_format": "Неверный формат",
    "invalid_region": "Неверный регион",
    "not_enough_hits": "Недостаточно подтверждений",
    "confirmed_but_not_allowed": "Подтверждено, но не разрешено",
    "no_loose_match": "Не похоже на номер РФ",
    "ocr_failed": "OCR не смог распознать",
    "noise_ocr": "OCR-мусор (отфильтровано)",
    "http_error": "Ошибка отправки в gatebox",
    # worker / misc
    "invalid": "Неверно",
    "denied": "Запрещено",
    "sent": "Отправлено",
    "ok": "ОК",
}


_RU_STATUS_MAP: Dict[str, str] = {
    "sent": "ОТПРАВЛЕНО",
    "invalid": "НЕВЕРНО",
    "denied": "ОТКАЗ",
    "debug": "ОТЛАДКА",
    "info": "ИНФО",
}


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


def _looks_like_ru_plate(plate: str, strict: bool = False) -> bool:
    p = (plate or "").strip().upper()
    if not p:
        return False
    if strict:
        return bool(_RE_RU_STRICT.match(p))
    return bool(_RE_RU_LOOSE.match(p))


def _translate_reason_ru(message_or_reason: str) -> str:
    s = (message_or_reason or "").strip()
    if not s:
        return s

    # Если это "http_error: ..." — оставим хвост, но переведём префикс
    if s.startswith("http_error"):
        return "Ошибка отправки в gatebox"

    # Часто приходят чистые reason-коды
    if s in _RU_REASON_MAP:
        return _RU_REASON_MAP[s]

    # Иногда reason лежит в более длинной строке
    # (например "invalid_format_or_region: ...")
    for k, v in _RU_REASON_MAP.items():
        if k and k in s:
            return v

    return s


def _derive_status(payload: Dict[str, Any]) -> Tuple[str, str]:
    """Нормализуем статус/сообщение для UI."""
    if isinstance(payload.get("status"), str) and payload["status"]:
        st = payload["status"]
        msg = _s(payload.get("message") or payload.get("reason") or "")
        return st, msg

    if payload.get("ok") is True:
        return "sent", _s(payload.get("reason") or payload.get("message") or "")

    if payload.get("valid") is False:
        return "invalid", _s(payload.get("reason") or "invalid")

    return "denied", _s(payload.get("reason") or payload.get("message") or "")


def _should_add_event_for_ui(payload: Dict[str, Any], plate: str, status: str) -> bool:
    """
    NEW: фильтр событий именно для UI.
    Gatebox может считать мусор полезным для диагностики, но UI лучше держать чистым.
    """
    if not UI_EVENTS_ONLY_RU:
        return True

    # 1) выкидываем noise
    if bool(payload.get("noise")):
        return False

    # 2) выкидываем пустое/непохожее
    if not _looks_like_ru_plate(plate, strict=UI_EVENTS_RU_STRICT):
        return False

    # 3) invalid обычно = мусор (обрывки, артефакты)
    if status == "invalid" and not UI_EVENTS_INCLUDE_INVALID:
        return False

    # 4) denied (например not_in_whitelist) обычно хотим видеть
    if status == "denied" and not UI_EVENTS_INCLUDE_DENIED:
        return False

    return True


def push_event_from_infer(payload: Dict[str, Any]) -> None:
    """main.py вызывает это после /infer, чтобы UI видел события."""
    try:
        ts = float(payload.get("ts") or time.time())
        plate = str(payload.get("plate") or payload.get("plate_norm") or "")
        raw = payload.get("raw")
        conf = payload.get("conf")
        status, message = _derive_status(payload)

        # FIX/CHG: уровень события берём из payload.debug или log_level (если есть)
        # (оставляем старую логику как fallback)
        if isinstance(payload.get("log_level"), str) and payload["log_level"]:
            lvl = str(payload["log_level"]).strip().lower()
            level = "debug" if lvl == "debug" else "info"
        else:
            level = "debug" if bool(payload.get("debug")) else "info"

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else None

        # NEW: фильтр мусора для UI
        if not _should_add_event_for_ui(payload, plate=plate, status=status):
            return

        # NEW: русификация сообщения/статуса в UI
        status_ui = status
        message_ui = message
        if UI_I18N_RU:
            status_ui = _RU_STATUS_MAP.get(status, status)
            message_ui = _translate_reason_ru(message)

        _event_store.add(
            EventItem(
                ts=float(ts),
                plate=str(plate),
                raw=_s(raw) if raw is not None else None,
                conf=_f(conf),
                status=str(status_ui),
                message=str(message_ui),
                level=level,
                meta=meta,
            )
        )
    except Exception:
        # события не должны валить /infer
        return


# ---------- FIX: tolerant query parsing (no 422) ----------

def _q_int(v: Any, default: int, min_v: int | None = None, max_v: int | None = None) -> int:
    try:
        if v is None:
            x = int(default)
        else:
            # FastAPI может передать уже str, но иногда прилетает "[object Object]"
            s = str(v).strip()
            x = int(float(s))
    except Exception:
        x = int(default)
    if min_v is not None:
        x = max(int(min_v), x)
    if max_v is not None:
        x = min(int(max_v), x)
    return x


def _q_float(v: Any, default: float, min_v: float | None = None) -> float:
    try:
        if v is None:
            x = float(default)
        else:
            x = float(str(v).strip())
    except Exception:
        x = float(default)
    if min_v is not None:
        x = max(float(min_v), x)
    return x


def _q_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return bool(default)
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


@router.get("/events")
def api_events(limit: Any = 50, after_ts: Any = None, include_debug: Any = False):
    # FIX: не даём 422 — парсим вручную
    lim = _q_int(limit, default=50, min_v=1, max_v=500)
    aft = _q_float(after_ts, default=0.0, min_v=0.0) if after_ts is not None else None
    inc = _q_bool(include_debug, default=False)
    return {"ok": True, "items": _event_store.latest(limit=lim, after_ts=aft, include_debug=inc)}


@router.get("/events/stream")
async def api_events_stream(
    request: Request,
    after_ts: Any = None,
    include_debug: Any = False,
    poll_ms: Any = 250,
    heartbeat_sec: Any = 15,
):
    """SSE stream событий."""
    # FIX: не даём 422 — парсим вручную
    last_ts: float = float(_q_float(after_ts, default=0.0, min_v=0.0)) if after_ts is not None else 0.0
    inc = _q_bool(include_debug, default=False)
    poll = _q_int(poll_ms, default=250, min_v=50, max_v=2000)
    hb = _q_int(heartbeat_sec, default=15, min_v=5, max_v=120)

    async def gen():
        nonlocal last_ts
        yield "retry: 2000\n\n"
        last_send = time.time()

        while True:
            if await request.is_disconnected():
                break

            try:
                items = _event_store.after(last_ts, include_debug=inc)
            except Exception:
                items = []

            for it in items:
                try:
                    last_ts = max(last_ts, float(it.ts))
                    yield f"event: event\ndata: {json.dumps(it.to_dict(), ensure_ascii=False)}\n\n"
                    last_send = time.time()
                except Exception:
                    # если вдруг в одном событии meta "плохая" — не валим поток
                    continue

            if (time.time() - last_send) >= float(hb):
                yield f": ping {int(time.time())}\n\n"
                last_send = time.time()

            await asyncio.sleep(max(0.05, float(poll) / 1000.0))

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


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
    """main.py устанавливает функцию, которая применит настройки (MQTT/GateDecider/etc)."""
    global _apply_callback
    _apply_callback = fn


def _require_store() -> SettingsStore:
    global _settings_store
    if _settings_store is None:
        _settings_store = SettingsStore(path="/config/settings.json", defaults=_DEFAULT_SETTINGS)
    return _settings_store


def get_settings_store() -> SettingsStore:
    return _require_store()


def _strip_nones(x: Any) -> Any:
    """Не даём null/None затирать настройки."""
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
    if isinstance(mqtt, dict) and mqtt.get("pass"):
        mqtt["pass"] = "***"
    return s


def _drop_empty_mqtt_pass(patch: Dict[str, Any]) -> Dict[str, Any]:
    """mqtt.pass == '' не должен затирать сохранённый пароль."""
    if not isinstance(patch, dict):
        return patch
    mqtt_patch = patch.get("mqtt")
    if isinstance(mqtt_patch, dict) and "pass" in mqtt_patch:
        if str(mqtt_patch.get("pass") or "") == "":
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
    if not isinstance(data, dict) or not data:
        data = st.update(copy.deepcopy(_DEFAULT_SETTINGS))
    return {"ok": True, "settings": _mask_settings_for_get(data)}


@router.post("/settings/reload")
def api_reload_settings():
    st = _require_store()
    data = st.reload()
    if not isinstance(data, dict) or not data:
        data = st.update(copy.deepcopy(_DEFAULT_SETTINGS))
    return {"ok": True, "settings": _mask_settings_for_get(data)}


@router.post("/settings/apply")
def api_apply_settings():
    st = _require_store()
    data = st.get()

    if _apply_callback is None:
        raise HTTPException(status_code=500, detail="apply_callback is not set (main.py should call set_apply_callback)")

    applied = _apply_callback(data)
    return {"ok": True, "applied": applied, "settings": _mask_settings_for_get(data)}


# =========================
# RTSP heartbeat/status (rtsp_worker может слать heartbeat)
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


# =========================================================
# Camera RTSP test endpoint (UI)
# =========================================================

class CameraTestIn(BaseModel):
    rtsp_url: str | None = None
    timeout_sec: float = 5.0
    use_settings: bool = False


@router.post("/camera/test")
def camera_test(data: CameraTestIn):
    """
    Проверка RTSP потока:
    - открываем камеру
    - читаем 1 кадр
    """
    import cv2  # локально, чтобы не грузить лишнее при старте

    # 1) Откуда брать RTSP
    if data.use_settings:
        try:
            settings = _require_store().get()
            rtsp_url = settings.get("camera", {}).get("rtsp_url")
        except Exception:
            rtsp_url = None
    else:
        rtsp_url = data.rtsp_url

    if not rtsp_url:
        return {"ok": False, "error": "rtsp_url_not_set"}

    start = time.time()

    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        return {"ok": False, "error": "cannot_open_stream"}

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        return {"ok": False, "error": "cannot_read_frame"}

    h, w = frame.shape[:2]
    elapsed = int((time.time() - start) * 1000)
    return {"ok": True, "width": w, "height": h, "grab_ms": elapsed}