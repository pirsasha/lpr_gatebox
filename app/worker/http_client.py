# =========================================================
# Файл: app/worker/http_client.py
# Проект: LPR GateBox
# Версия: v0.3.7-all-in-one-split
# =========================================================

from __future__ import annotations

import json
from typing import Optional, Tuple

import cv2
import requests
import numpy as np


def infer_base_url(infer_url: str) -> str:
    u = (infer_url or "").strip()
    if not u:
        return ""
    if u.endswith("/infer"):
        return u[: -len("/infer")]
    return u.rstrip("/")


def post_heartbeat(url: str, payload: dict, timeout_sec: float = 1.0) -> None:
    if not url:
        return
    try:
        requests.post(url, json=payload, timeout=timeout_sec)
    except Exception:
        return


def get_json(url: str, timeout_sec: float = 2.0) -> Optional[dict]:
    try:
        r = requests.get(url, timeout=timeout_sec)
        if not r.ok:
            return None
        return r.json()
    except Exception:
        return None


def fetch_camera_settings(settings_base: str) -> Tuple[Optional[str], Optional[bool]]:
    if not settings_base:
        return (None, None)

    data = get_json(f"{settings_base}/api/v1/settings", timeout_sec=2.0)
    if data is None:
        data = get_json(f"{settings_base}/api/settings", timeout_sec=2.0)
    if not isinstance(data, dict):
        return (None, None)

    settings = data.get("settings") if isinstance(data.get("settings"), dict) else data
    camera = settings.get("camera") if isinstance(settings, dict) and isinstance(settings.get("camera"), dict) else None
    if not isinstance(camera, dict):
        return (None, None)

    rtsp_url = camera.get("rtsp_url")
    enabled = camera.get("enabled")

    rtsp_url = str(rtsp_url).strip() if rtsp_url else None
    enabled = bool(enabled) if enabled is not None else None
    return (rtsp_url, enabled)


def fetch_settings_json(settings_base: str, timeout_sec: float = 2.0) -> Optional[dict]:
    """Читает весь settings.json из gatebox (через API).

    Возвращает dict настроек (без обёртки {"settings": ...}), либо None.
    Поддерживает оба маршрута: /api/v1/settings и /api/settings.
    """
    if not settings_base:
        return None

    data = get_json(f"{settings_base}/api/v1/settings", timeout_sec=timeout_sec)
    if data is None:
        data = get_json(f"{settings_base}/api/settings", timeout_sec=timeout_sec)
    if not isinstance(data, dict):
        return None

    settings = data.get("settings") if isinstance(data.get("settings"), dict) else data
    return settings if isinstance(settings, dict) else None


def fetch_rtsp_worker_overrides(settings_base: str, timeout_sec: float = 2.0) -> dict:
    """Достаёт из settings.json блок rtsp_worker.overrides.

    Формат в settings.json:
      {
        "rtsp_worker": {
          "overrides": {
            "SAVE_EVERY": "1",
            "SAVE_FULL_FRAME": "1",
            ...
          }
        }
      }

    Возвращает словарь str->str (как env), только по разрешённым ключам.
    """

    settings = fetch_settings_json(settings_base, timeout_sec=timeout_sec)
    if not isinstance(settings, dict):
        return {}

    rtsp = settings.get("rtsp_worker")
    if not isinstance(rtsp, dict):
        return {}

    overrides = rtsp.get("overrides")
    if not isinstance(overrides, dict):
        return {}

    # whitelist: чтобы клиент не мог подменять опасные вещи в рантайме.
    allowed = {
        "SAVE_DIR",
        "SAVE_EVERY",
        "SAVE_FULL_FRAME",
        "SAVE_WITH_ROI",
        "LOG_EVERY_SEC",
    }

    out = {}
    for k, v in overrides.items():
        if k not in allowed:
            continue
        if v is None:
            continue
        out[str(k)] = str(v)
    return out


def post_crop(
    infer_url: str,
    crop_bgr: np.ndarray,
    timeout_sec: float,
    jpeg_quality: int,
    pre_variant: str = "crop",
    pre_warped: bool = False,
    pre_timing: Optional[dict] = None,
) -> Tuple[dict, Optional[bytes]]:
    ok, buf = cv2.imencode(".jpg", crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    if not ok:
        return {"ok": False, "reason": "jpeg_encode_failed"}, None

    jpeg_bytes = buf.tobytes()

    files = {"file": ("crop.jpg", jpeg_bytes, "image/jpeg")}
    data = {
        "pre_variant": str(pre_variant or "crop"),
        "pre_warped": "1" if bool(pre_warped) else "0",
        "pre_timing_ms": json.dumps(pre_timing or {}, ensure_ascii=False),
    }

    r = requests.post(infer_url, files=files, data=data, timeout=timeout_sec)
    r.raise_for_status()
    return r.json(), jpeg_bytes
    
def fetch_settings(settings_base_url: str) -> dict:
    """Получить весь settings.json через gatebox UI API.
    Бэкенд отдаёт формат: { ok: true, settings: {...} }.
    """
    url = f"{settings_base_url.rstrip('/')}/settings"
    data = http_get_json(url, timeout_sec=1.2)
    if not isinstance(data, dict):
        return {}
    s = data.get("settings")
    return s if isinstance(s, dict) else {}