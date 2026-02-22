# =========================================================
# Файл: app/api/ui_api.py
# Проект: LPR GateBox
# Версия: v0.3.34-ui-cloudpub-missing-fns-fix
# Изменено: 2026-02-20 (UTC+1)
# Автор: Александр + ChatGPT
#
# FIX:
# - Добавлены отсутствующие функции CloudPub:
#   _cloudpub_cfg_from_settings, _cloudpub_apply_auto_expire,
#   _cloudpub_connection_state, _cloudpub_append_audit
# - Из-за отсутствия этих функций /api/v1/cloudpub/status и /connect падали 500 (NameError)
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

# CloudPub (remote tunnel)
from app.integrations.cloudpub.manager import cloudpub_manager

# ВАЖНО: без prefix. Префиксы вешаем в main.py:
# app.include_router(ui_router, prefix="/api")
# app.include_router(ui_router, prefix="/api/v1")
router = APIRouter(tags=["ui"])

CLOUDPUB_SIMULATION = str(os.environ.get("CLOUDPUB_SIMULATION", "0")).strip() not in ("0", "false", "False")

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
RECENT_PLATES_DIR = Path(os.environ.get("RECENT_PLATES_DIR", "/config/live/recent_plates"))
RECENT_PLATES_INDEX = RECENT_PLATES_DIR / "index.json"


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


@router.get("/recent_plates")
def api_recent_plates():
    """Последние распознанные номера с мини-кропами (ring buffer)."""
    data = _read_json(RECENT_PLATES_INDEX, {"ok": True, "items": []})
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []

    safe_items = []
    for it in items:
        if not isinstance(it, dict):
            continue
        fname = str(it.get("file") or "")
        if not fname:
            continue
        safe_items.append(
            {
                "ts": it.get("ts"),
                "plate": it.get("plate"),
                "conf": it.get("conf"),
                "reason": it.get("reason"),
                "ok": bool(it.get("ok")),
                "file": fname,
                "image_url": f"/api/v1/recent_plates/image/{fname}",
            }
        )

    return {"ok": True, "items": safe_items}


@router.get("/recent_plates/image/{filename}")
def api_recent_plate_image(filename: str):
    name = os.path.basename(filename)
    if name != filename or not name.lower().endswith(".jpg"):
        raise HTTPException(status_code=400, detail="bad filename")
    path = RECENT_PLATES_DIR / name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(str(path), media_type="image/jpeg")


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

    if bool(payload.get("noise")):
        return False

    if not _looks_like_ru_plate(plate, strict=UI_EVENTS_RU_STRICT):
        return False

    if status == "invalid" and not UI_EVENTS_INCLUDE_INVALID:
        return False

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

        if isinstance(payload.get("log_level"), str) and payload["log_level"]:
            lvl = str(payload["log_level"]).strip().lower()
            level = "debug" if lvl == "debug" else "info"
        else:
            level = "debug" if bool(payload.get("debug")) else "info"

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else None

        if not _should_add_event_for_ui(payload, plate=plate, status=status):
            return

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
        return


def _q_int(v: Any, default: int, min_v: int | None = None, max_v: int | None = None) -> int:
    try:
        if v is None:
            x = int(default)
        else:
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
                    continue

            if (time.time() - last_send) >= float(hb):
                yield f": ping {int(time.time())}\n\n"
                last_send = time.time()

            await asyncio.sleep(max(0.05, float(poll) / 1000.0))

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


# =========================
# SETTINGS (settings.json)
# =========================

_settings_store: Optional[SettingsStore] = None
_apply_callback: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
_DEFAULT_SETTINGS: Dict[str, Any] = {}
_cloudpub_state: Dict[str, Any] = {"connected": False, "last_error": "", "last_ok_ts": 0.0, "target": ""}
_cloudpub_audit: list[Dict[str, Any]] = []


def init_settings(path: str, defaults: Dict[str, Any]) -> None:
    global _settings_store, _DEFAULT_SETTINGS
    _DEFAULT_SETTINGS = copy.deepcopy(defaults or {})
    _settings_store = SettingsStore(path=path, defaults=_DEFAULT_SETTINGS)


def set_apply_callback(fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
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
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="settings payload must be object")
    inner = payload.get("settings")
    if isinstance(inner, dict):
        return inner
    return payload


def _mask_settings_for_get(settings: Dict[str, Any]) -> Dict[str, Any]:
    s = copy.deepcopy(settings or {})
    mqtt = s.get("mqtt")
    if isinstance(mqtt, dict) and mqtt.get("pass"):
        mqtt["pass"] = "***"
    cloudpub = s.get("cloudpub")
    if isinstance(cloudpub, dict):
        if cloudpub.get("access_key"):
            cloudpub["access_key"] = "***"
        if cloudpub.get("password"):
            cloudpub["password"] = "***"
    return s


def _as_float(v: Any, field: str) -> float:
    try:
        return float(v)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field}: ожидалось число")


def _as_int(v: Any, field: str) -> int:
    try:
        return int(float(v))
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field}: ожидалось целое число")


def _check_range(v: float, field: str, min_v: float, max_v: float) -> None:
    if v < min_v or v > max_v:
        raise HTTPException(status_code=400, detail=f"{field}: допустимый диапазон {min_v}..{max_v}")


def _validate_roi_poly_str(v: Any, field: str) -> None:
    s = str(v or "").strip()
    if not s:
        return
    pts = []
    for raw in s.split(";"):
        p = raw.strip()
        if not p:
            continue
        xy = [x.strip() for x in p.split(",")]
        if len(xy) != 2:
            raise HTTPException(status_code=400, detail=f"{field}: формат x1,y1;x2,y2;...")
        try:
            x = float(xy[0])
            y = float(xy[1])
        except Exception:
            raise HTTPException(status_code=400, detail=f"{field}: координаты должны быть числами")
        pts.append((x, y))
    if pts and len(pts) < 3:
        raise HTTPException(status_code=400, detail=f"{field}: нужно минимум 3 точки")


def _validate_settings_patch(patch: Dict[str, Any]) -> None:
    if not isinstance(patch, dict):
        return

    gate = patch.get("gate")
    if isinstance(gate, dict):
        if "min_conf" in gate:
            _check_range(_as_float(gate.get("min_conf"), "gate.min_conf"), "gate.min_conf", 0.5, 0.99)
        if "confirm_n" in gate:
            _check_range(float(_as_int(gate.get("confirm_n"), "gate.confirm_n")), "gate.confirm_n", 1, 10)
        if "confirm_window_sec" in gate:
            _check_range(
                _as_float(gate.get("confirm_window_sec"), "gate.confirm_window_sec"),
                "gate.confirm_window_sec",
                0.5,
                8.0,
            )
        if "cooldown_sec" in gate:
            _check_range(_as_float(gate.get("cooldown_sec"), "gate.cooldown_sec"), "gate.cooldown_sec", 1.0, 120.0)
        if "region_stab_window_sec" in gate:
            _check_range(
                _as_float(gate.get("region_stab_window_sec"), "gate.region_stab_window_sec"),
                "gate.region_stab_window_sec",
                0.5,
                8.0,
            )
        if "region_stab_min_hits" in gate:
            _check_range(float(_as_int(gate.get("region_stab_min_hits"), "gate.region_stab_min_hits")), "gate.region_stab_min_hits", 1, 10)
        if "region_stab_min_ratio" in gate:
            _check_range(
                _as_float(gate.get("region_stab_min_ratio"), "gate.region_stab_min_ratio"),
                "gate.region_stab_min_ratio",
                0.3,
                1.0,
            )

    rt = patch.get("rtsp_worker")
    if isinstance(rt, dict):
        ov = rt.get("overrides")
        if isinstance(ov, dict):
            if "READ_FPS" in ov:
                _check_range(_as_float(ov.get("READ_FPS"), "rtsp_worker.overrides.READ_FPS"), "rtsp_worker.overrides.READ_FPS", 1.0, 30.0)
            if "DET_FPS" in ov:
                _check_range(_as_float(ov.get("DET_FPS"), "rtsp_worker.overrides.DET_FPS"), "rtsp_worker.overrides.DET_FPS", 0.5, 15.0)
            if "SEND_FPS" in ov:
                _check_range(_as_float(ov.get("SEND_FPS"), "rtsp_worker.overrides.SEND_FPS"), "rtsp_worker.overrides.SEND_FPS", 0.5, 15.0)
            if "DET_CONF" in ov:
                _check_range(_as_float(ov.get("DET_CONF"), "rtsp_worker.overrides.DET_CONF"), "rtsp_worker.overrides.DET_CONF", 0.05, 0.95)
            if "DET_IOU" in ov:
                _check_range(_as_float(ov.get("DET_IOU"), "rtsp_worker.overrides.DET_IOU"), "rtsp_worker.overrides.DET_IOU", 0.1, 0.9)
            if "JPEG_QUALITY" in ov:
                _check_range(float(_as_int(ov.get("JPEG_QUALITY"), "rtsp_worker.overrides.JPEG_QUALITY")), "rtsp_worker.overrides.JPEG_QUALITY", 60, 100)
            if "LOG_EVERY_SEC" in ov:
                _check_range(_as_float(ov.get("LOG_EVERY_SEC"), "rtsp_worker.overrides.LOG_EVERY_SEC"), "rtsp_worker.overrides.LOG_EVERY_SEC", 0.0, 120.0)
            if "SAVE_EVERY" in ov:
                _check_range(float(_as_int(ov.get("SAVE_EVERY"), "rtsp_worker.overrides.SAVE_EVERY")), "rtsp_worker.overrides.SAVE_EVERY", 0, 300)
            if "ROI_POLY_STR" in ov:
                _validate_roi_poly_str(ov.get("ROI_POLY_STR"), "rtsp_worker.overrides.ROI_POLY_STR")


def _drop_empty_cloudpub_access_key(patch: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(patch, dict):
        return patch
    cp_patch = patch.get("cloudpub")
    if isinstance(cp_patch, dict) and "access_key" in cp_patch:
        val = str(cp_patch.get("access_key") or "").strip()
        if val in ("", "***"):
            cp_patch.pop("access_key", None)
    return patch


def _drop_empty_cloudpub_password(patch: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(patch, dict):
        return patch
    cp_patch = patch.get("cloudpub")
    if isinstance(cp_patch, dict) and "password" in cp_patch:
        val = str(cp_patch.get("password") or "").strip()
        if val in ("", "***"):
            cp_patch.pop("password", None)
    return patch


def _drop_empty_mqtt_pass(patch: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(patch, dict):
        return patch
    mqtt_patch = patch.get("mqtt")
    if isinstance(mqtt_patch, dict) and "pass" in mqtt_patch:
        val = str(mqtt_patch.get("pass") or "").strip()
        if val in ("", "***"):
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
    data_in = _drop_empty_cloudpub_access_key(data_in)
    data_in = _drop_empty_cloudpub_password(data_in)
    _validate_settings_patch(data_in)
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


# =========================
# CloudPub helpers (FIX: missing functions)
# =========================

def _cloudpub_append_audit(action: str, ok: bool, detail: str) -> None:
    """Единый audit (для UI). В SDK режиме отдельный audit есть в manager.state()."""
    try:
        _cloudpub_audit.insert(0, {"ts": int(time.time()), "action": str(action), "ok": bool(ok), "detail": str(detail)[:500]})
        # не раздуваем память
        del _cloudpub_audit[200:]
    except Exception:
        pass

from urllib.parse import urlparse

def _normalize_origin_target_ui(v: str, default_port: int = 8080) -> str:
    s = str(v or "").strip()
    if not s:
        return ""  # пусто = пусть manager возьмёт 127.0.0.1:8080

    # если вставили URL
    if "://" in s:
        try:
            p = urlparse(s)
            host = p.hostname or ""
            port = p.port or default_port
            if host:
                return f"{host}:{port}"
        except Exception:
            pass

    # host:port
    if ":" in s:
        return s

    # host
    return f"{s}:{default_port}"
    
def _cloudpub_cfg_from_settings() -> Dict[str, Any]:
    """Достаём cloudpub cfg из settings.json, с безопасными default-ами."""
    try:
        s = _require_store().get()
    except Exception:
        s = {}

    cp = s.get("cloudpub") if isinstance(s, dict) else None
    cp = cp if isinstance(cp, dict) else {}

    enabled = bool(cp.get("enabled", True))
    server_ip = str(cp.get("server_ip") or "").strip()

    # legacy token
    access_key = str(cp.get("access_key") or "").strip()

    # new email/pass
    email = str(cp.get("email") or "").strip()
    password = str(cp.get("password") or "").strip()

    # NEW: backend/protocol
    backend = str(cp.get("backend") or "docker").strip().lower()   # docker|sdk
    protocol = str(cp.get("protocol") or "http").strip().lower()  # http|https|tcp (для docker)

    # auto-expire (минуты)
    try:
        auto_expire_min = int(float(cp.get("auto_expire_min") or 0))
    except Exception:
        auto_expire_min = 0

    return {
        "enabled": enabled,
        "server_ip": server_ip,
        "access_key": access_key,
        "email": email,
        "password": password,
        "backend": backend,
        "protocol": protocol,
        "auto_expire_min": max(0, auto_expire_min),
    }


def _cloudpub_connection_state(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Статус для simulation режима (чтобы UI показывал красиво)."""
    if not bool(cfg.get("enabled")):
        return {"connection_state": "disabled", "state_reason": "cloudpub_disabled"}

    if bool(_cloudpub_state.get("connected")):
        return {"connection_state": "online", "state_reason": ""}

    # если была ошибка — покажем её как reason
    last_err = str(_cloudpub_state.get("last_error") or "").strip()
    if last_err:
        return {"connection_state": "offline", "state_reason": "cloudpub_connect_failed"}

    return {"connection_state": "offline", "state_reason": "offline"}


def _cloudpub_apply_auto_expire(cfg: Dict[str, Any]) -> None:
    """Auto-expire (только для simulation stub). В SDK режиме auto-expire делает manager."""
    try:
        if not CLOUDPUB_SIMULATION:
            return

        if not bool(cfg.get("enabled")):
            return

        minutes = int(cfg.get("auto_expire_min") or 0)
        if minutes <= 0:
            return

        if not bool(_cloudpub_state.get("connected")):
            return

        last_ok = float(_cloudpub_state.get("last_ok_ts") or 0.0)
        if last_ok <= 0:
            return

        if (time.time() - last_ok) >= float(minutes) * 60.0:
            _cloudpub_state.update({"connected": False, "last_error": "", "target": "", "public_url": ""})
            _cloudpub_append_audit("auto_expire", True, f"expired after {minutes} min")
    except Exception:
        return


class CloudPubConnectIn(BaseModel):
    server_ip: Optional[str] = None
    access_key: Optional[str] = None  # token


@router.get("/cloudpub/status")
def api_cloudpub_status():
    """
    Статус CloudPub туннеля.

    В simulation режиме (CLOUDPUB_SIMULATION=1) — старый stub.
    В реальном режиме — состояние берём из cloudpub_manager (docker-only manager).
    """
    cfg = _cloudpub_cfg_from_settings()
    _cloudpub_apply_auto_expire(cfg)

    # --- simulation mode (старый stub) ---
    if CLOUDPUB_SIMULATION:
        state = _cloudpub_connection_state(cfg)
        target = str(_cloudpub_state.get("target") or cfg.get("server_ip") or "").strip()
        management_url = f"http://{target}" if target else ""
        public_url = str(_cloudpub_state.get("public_url") or management_url)

        configured = bool(str(cfg.get("access_key") or "").strip())

        return {
            "ok": True,
            "enabled": bool(cfg.get("enabled")),
            "configured": configured,
            "server_ip": cfg.get("server_ip", ""),
            "connected": bool(_cloudpub_state.get("connected")),
            "connection_state": state["connection_state"],
            "state_reason": state["state_reason"],
            "last_ok_ts": float(_cloudpub_state.get("last_ok_ts") or 0.0),
            "last_error": str(_cloudpub_state.get("last_error") or ""),
            "target": str(_cloudpub_state.get("target") or ""),
            "management_url": management_url,
            "public_url": public_url,
            "provider": "cloudpub",
            "mode": "simulation",
            "simulation": True,
            "note": "docker_only",
            "audit": _cloudpub_audit[:20],
        }

    # --- real docker-only manager ---
    base = cloudpub_manager.state()
    base["target"] = base.get("server_ip") or ""

    if not bool(cfg.get("enabled")):
        base.update(
            {
                "ok": True,
                "enabled": False,
                "configured": False,
                "server_ip": cfg.get("server_ip", ""),
                "connection_state": "disabled",
                "state_reason": "cloudpub_disabled",
                "provider": "cloudpub",
                "mode": "docker",
                "simulation": False,
                "note": "docker_only",
            }
        )
        return base

    configured = bool(str(cfg.get("access_key") or "").strip())

    base.update(
        {
            "ok": True,
            "enabled": True,
            "configured": configured,
            "server_ip": cfg.get("server_ip", ""),
            "provider": "cloudpub",
            "mode": "docker",
            "simulation": False,
            "note": "docker_only",
        }
    )
    return base

    # --- real SDK mode ---
    base = cloudpub_manager.state()
    base["target"] = base.get("server_ip") or ""   # просто alias
    if not bool(cfg.get("enabled")):
        base.update(
            {
                "ok": True,
                "enabled": False,
                "configured": False,
                "server_ip": cfg.get("server_ip", ""),
                "connection_state": "disabled",
                "state_reason": "cloudpub_disabled",
                "provider": "cloudpub",
                "mode": "sdk",
                "simulation": False,
                "note": "sdk_mode",
            }
        )
        return base

    configured = bool(
        (str(cfg.get("email") or "").strip() and str(cfg.get("password") or "").strip())
        or str(cfg.get("access_key") or "").strip()
    )

    base.update(
        {
            "ok": True,
            "enabled": True,
            "configured": configured,
            "server_ip": cfg.get("server_ip", ""),
            "provider": "cloudpub",
            "mode": "sdk",
            "simulation": False,
            "note": "sdk_mode",
        }
    )
    return base


@router.post("/cloudpub/connect")
def api_cloudpub_connect(req: CloudPubConnectIn):
    """
    Подключение CloudPub (DOCKER ONLY).
    Используем только token (access_key) и target (server_ip).
    """
    cfg = _cloudpub_cfg_from_settings()

    if not bool(cfg.get("enabled")):
        _cloudpub_append_audit("connect", False, "cloudpub_disabled")
        return {"ok": False, "error": "cloudpub_disabled"}

    server_ip = _normalize_origin_target_ui(str((req.server_ip or cfg.get("server_ip") or "")).strip())

    token = str((req.access_key or cfg.get("access_key") or "")).strip()

    def _unmask(v: str) -> str:
        s = str(v or "").strip()
        return "" if s in ("***", "•••") else s

    token = _unmask(token)

    if not token:
        _cloudpub_append_audit("connect", False, "cloudpub_not_configured_token")
        return {"ok": False, "error": "cloudpub_not_configured_token"}

    # --- simulation ---
    if CLOUDPUB_SIMULATION:
        _cloudpub_state.update(
            {
                "connected": True,
                "last_error": "",
                "last_ok_ts": time.time(),
                "target": server_ip or "gatebox:8080",
                "public_url": f"http://{server_ip}" if server_ip else "",
            }
        )
        _cloudpub_append_audit("connect", True, "simulation token")
        state = _cloudpub_connection_state(cfg)
        return {
            "ok": True,
            "connected": True,
            "connection_state": state["connection_state"],
            "state_reason": state["state_reason"],
            "public_url": str(_cloudpub_state.get("public_url") or ""),
            "mode": "simulation",
            "note": "docker_only",
        }

    # --- real docker-only manager ---
    try:
        if not server_ip.strip():
            server_ip = ""  # пусть manager решит дефолт (обычно gatebox:8080)
        st = cloudpub_manager.connect(
            enabled=True,
            token=token,
            server_ip=server_ip,
            auto_expire_min=int(cfg.get("auto_expire_min") or 0),
        )
        _cloudpub_append_audit("connect", True, "docker token")
        return {
            "ok": True,
            "connected": True,
            "connection_state": st.get("connection_state") or "online",
            "state_reason": st.get("state_reason") or "",
            "public_url": str(st.get("public_url") or ""),
            "mode": "docker",
            "note": "docker_only",
        }
    except RuntimeError as e:
        _cloudpub_append_audit("connect", False, f"token err={e}")
        return {"ok": False, "error": str(e)}
    except Exception as e:
        _cloudpub_append_audit("connect", False, f"token err={e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cloudpub/disconnect")
def api_cloudpub_disconnect():
    if CLOUDPUB_SIMULATION:
        _cloudpub_state.update({"connected": False, "last_error": "", "target": "", "public_url": ""})
        _cloudpub_append_audit("disconnect", True, "manual")
        return {"ok": True, "connected": False, "connection_state": "offline", "state_reason": "disconnected"}

    try:
        cloudpub_manager.disconnect()
        _cloudpub_append_audit("disconnect", True, "manual")
        return {"ok": True, "connected": False, "connection_state": "offline", "state_reason": "disconnected"}
    except Exception as e:
        _cloudpub_append_audit("disconnect", False, str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cloudpub/audit/clear")
def api_cloudpub_audit_clear():
    if CLOUDPUB_SIMULATION:
        _cloudpub_audit.clear()
        _cloudpub_append_audit("audit_clear", True, "manual")
        return {"ok": True, "size": len(_cloudpub_audit)}

    cloudpub_manager.clear_audit()
    return {"ok": True}


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