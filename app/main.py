# =========================================================
# Файл: app/main.py
# Проект: LPR GateBox
# Версия: v0.1
# Изменено: 2026-02-09 (UTC+3)
# Автор: Александр
# ---------------------------------------------------------
# Что сделано:
# - CHG: runtime-конфиг gatebox резолвится по правилу cfg -> env -> default.
# - FIX: источник истины для Telegram/MQTT в рантайме — settings.json (ENV как fallback).
# - FIX: безопасное логирование источников конфигурации без утечки секретов.
# - FIX: /infer больше НЕ падает 400 из-за OCR/warp/orient ошибок.
#        (раньше любые исключения внутри _ocr_try_best() превращались в HTTP 400)
# - NEW: "soft reject" при OCR-ошибке: infer_ok=False, ok=False, reason="ocr_failed"/"empty_ocr"
# - NEW: payload.ocr_error для диагностики (best-effort), чтобы понимать причину отказа без 400
#
# Важно:
# - 400 остаётся ТОЛЬКО для невалидного запроса (не image) и битого decode.
# - Любой "плохой OCR" или падение внутри OCR pipeline теперь штатно возвращает 200.
# =========================================================

# ИЗМЕНЕНО v0.2.9:
# - Реализован adaptive OCR (warp только при плохом OCR).
# - В ответ /infer добавлены timing_ms и диагностические поля из GateDecider.

"""FastAPI core for LPR GateBox.

FILE: app/main.py
VERSION: v0.2.9
DATE: 2026-02-06

CHG v0.2.9:
- Adaptive OCR: сначала быстрый OCR без warp, затем (опционально) ориентации и warp
  ТОЛЬКО если результат признан "плохим" по критериям (conf/valid/len).
- В ответ /infer добавлены timing_ms — чтобы быстро локализовать задержку (decode/ocr/warp).

Почему так:
- warp/minAreaRect/quad — дорогие операции на CPU. На мини-ПК выгоднее сначала сделать
  максимально быстрый прогон OCR, и только при явной проблеме подключать стабилизацию.
"""

from __future__ import annotations

import os
import json
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, Tuple

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from pydantic import BaseModel
import paho.mqtt.client as mqtt

from app.ocr_onnx import OnnxOcr
from app.gate_logic import GateDecider, is_valid_ru_plate_strict, normalize_ru_plate, is_noise_ocr
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.integrations.telegram.client import TelegramClient
from app.integrations.telegram.notifier import TelegramNotifier
from app.integrations.telegram.poller import TelegramPoller
from app.api.ui_api import get_settings_store
from app.api.telegram_api import router as telegram_router, set_telegram_hooks
from app.core.config_resolve import (
    get_bool,
    get_float,
    get_int,
    get_str,
    get_str_src,
    describe_secret,
)
from app.core.settings_v2 import default_settings_v2, effective_config

# quad/warp (best-effort внутри gatebox)
# ВАЖНО: это отдельный путь от твоего refiner-а в rtsp_worker.
try:
    from app.core.plate_rectifier import rectify_plate_quad  # type: ignore
except ModuleNotFoundError:
    from core.plate_rectifier import rectify_plate_quad  # type: ignore

# UI API
from app.api import ui_api
from app.api.ui_api import router as ui_router
from app.api.ui_api import push_event_from_infer


# =========================
# ENV / DEFAULTS
# =========================
MODEL_PATH = os.environ.get("MODEL_PATH", "/models/plate_ocr.onnx")
SETTINGS_PATH = os.environ.get("SETTINGS_PATH", "/config/settings.json")

# MQTT defaults from env (используются при первом создании settings.json)
ENV_MQTT_ENABLED = os.environ.get("MQTT_ENABLED", "1").strip().lower() in ("1", "true", "yes")
ENV_MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.1.10")
ENV_MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
ENV_MQTT_USER = os.environ.get("MQTT_USER", "")
ENV_MQTT_PASS = os.environ.get("MQTT_PASS", "")
ENV_MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "gate/open")

# Gate defaults from env
ENV_MIN_CONF = float(os.environ.get("MIN_CONF", "0.80"))
ENV_CONFIRM_N = int(os.environ.get("CONFIRM_N", "2"))
# CHG v0.2.9: при низком FPS воркера (0.5-1.0) окно 2 секунды легко превращается в "вечное ожидание".
# По умолчанию расширяем окно до 6 секунд (можно вернуть 2.0, если RTSP_FPS>=2).
ENV_CONFIRM_WINDOW_SEC = float(os.environ.get("CONFIRM_WINDOW_SEC", "6.0"))
ENV_COOLDOWN_SEC = float(os.environ.get("COOLDOWN_SEC", "15.0"))
ENV_WHITELIST_PATH = os.environ.get("WHITELIST_PATH", "/config/whitelist.json")

ENV_REGION_CHECK = os.environ.get("REGION_CHECK", "1").strip().lower() not in ("0", "false", "no", "off", "")
ENV_REGION_STAB = os.environ.get("REGION_STAB", "1").strip().lower() not in ("0", "false", "no", "off", "")
ENV_REGION_STAB_WINDOW_SEC = float(os.environ.get("REGION_STAB_WINDOW_SEC", "2.5"))
ENV_REGION_STAB_MIN_HITS = int(os.environ.get("REGION_STAB_MIN_HITS", "3"))
ENV_REGION_STAB_MIN_RATIO = float(os.environ.get("REGION_STAB_MIN_RATIO", "0.60"))

# OCR orientation / warp toggles (для прямых запросов в gatebox)
ENV_OCR_ORIENT_TRY = os.environ.get("OCR_ORIENT_TRY", "1").strip().lower() not in ("0", "false", "no", "off", "")
ENV_OCR_WARP_TRY = os.environ.get("OCR_WARP_TRY", "1").strip().lower() not in ("0", "false", "no", "off", "")
# CHG v0.2.9: меньший размер warp заметно экономит CPU на мини-ПК.
ENV_OCR_WARP_W = int(os.environ.get("OCR_WARP_W", "320"))
ENV_OCR_WARP_H = int(os.environ.get("OCR_WARP_H", "96"))

# NEW v0.2.9: критерии "плохого OCR" для adaptive pipeline
ENV_OCR_BAD_MIN_LEN = int(os.environ.get("OCR_BAD_MIN_LEN", "6"))
ENV_OCR_BAD_MIN_CONF = float(os.environ.get("OCR_BAD_MIN_CONF", "0.70"))

# NEW: продуктовые флаги логирования
DEBUG_LOG = os.environ.get("DEBUG_LOG", "0").strip().lower() in ("1", "true", "yes", "y", "on")
PRINT_EVERY_RESPONSE = os.environ.get("PRINT_EVERY_RESPONSE", "0").strip().lower() in ("1", "true", "yes", "y", "on")

RECENT_PLATES_DIR = Path(os.environ.get("RECENT_PLATES_DIR", "/config/live/recent_plates"))
RECENT_PLATES_INDEX = RECENT_PLATES_DIR / "index.json"
RECENT_PLATES_MAX = int(os.environ.get("RECENT_PLATES_MAX", "5") or "5")
_recent_lock = Lock()


def _save_recent_plate_crop(img_bgr: np.ndarray, payload: Dict[str, Any]) -> None:
    """Сохраняем последние распознанные номера (ring buffer) для UI."""
    try:
        plate = str(payload.get("plate") or "").strip()
        if not plate:
            return

        RECENT_PLATES_DIR.mkdir(parents=True, exist_ok=True)

        ts_ms = int(float(payload.get("ts") or time.time()) * 1000.0)
        safe_plate = "".join(ch for ch in plate if ch.isalnum()) or "PLATE"
        fname = f"{ts_ms}_{safe_plate}.jpg"
        fpath = RECENT_PLATES_DIR / fname

        ok_jpg, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok_jpg:
            return
        fpath.write_bytes(bytes(buf))

        item = {
            "ts": float(payload.get("ts") or time.time()),
            "plate": plate,
            "conf": float(payload.get("conf") or 0.0),
            "reason": str(payload.get("reason") or ""),
            "ok": bool(payload.get("ok")),
            "file": fname,
        }

        with _recent_lock:
            items = []
            try:
                if RECENT_PLATES_INDEX.exists():
                    data = json.loads(RECENT_PLATES_INDEX.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and isinstance(data.get("items"), list):
                        items = [x for x in data.get("items", []) if isinstance(x, dict)]
            except Exception:
                items = []

            items.insert(0, item)
            max_n = max(1, int(RECENT_PLATES_MAX))
            extra = items[max_n:]
            items = items[:max_n]

            RECENT_PLATES_INDEX.write_text(
                json.dumps({"ok": True, "items": items}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            for ex in extra:
                try:
                    old = RECENT_PLATES_DIR / str(ex.get("file") or "")
                    if old.exists() and old.is_file():
                        old.unlink()
                except Exception:
                    pass
    except Exception:
        pass

DEFAULT_SETTINGS: Dict[str, Any] = default_settings_v2()
DEFAULT_SETTINGS["system"]["mqtt"] = {
    "enabled": ENV_MQTT_ENABLED,
    "host": ENV_MQTT_HOST,
    "port": ENV_MQTT_PORT,
    "user": ENV_MQTT_USER,
    "pass": ENV_MQTT_PASS,
    "topic": ENV_MQTT_TOPIC,
}
DEFAULT_SETTINGS["system"]["paths"] = {
    "model_path": MODEL_PATH,
    "settings_path": SETTINGS_PATH,
    "infer_url": os.environ.get("INFER_URL", "http://gatebox:8080/infer"),
    "capture_backend": os.environ.get("CAPTURE_BACKEND", "auto"),
    "save_dir": os.environ.get("SAVE_DIR", "/debug"),
}
DEFAULT_SETTINGS["profiles"]["day"]["gate"].update({
    "min_conf": ENV_MIN_CONF,
    "confirm_n": ENV_CONFIRM_N,
    "confirm_window_sec": ENV_CONFIRM_WINDOW_SEC,
    "cooldown_sec": ENV_COOLDOWN_SEC,
    "whitelist_path": ENV_WHITELIST_PATH,
    "region_check": ENV_REGION_CHECK,
    "region_stab": ENV_REGION_STAB,
    "region_stab_window_sec": ENV_REGION_STAB_WINDOW_SEC,
    "region_stab_min_hits": ENV_REGION_STAB_MIN_HITS,
    "region_stab_min_ratio": ENV_REGION_STAB_MIN_RATIO,
})



# =========================
# APP
# =========================
app = FastAPI(title="LPR GateBox Core")

# legacy (старый UI не ломаем)
app.include_router(ui_router, prefix="/api")

# v1 (новый стабильный контракт)
app.include_router(ui_router, prefix="/api/v1")

app.include_router(telegram_router)

# =========================
# CORE OBJECTS
# =========================
ocr = OnnxOcr(MODEL_PATH)
decider = GateDecider()

from app.api.ui_api import set_whitelist_reload_callback
set_whitelist_reload_callback(decider.reload_whitelist)

# =========================
# MQTT (runtime config)
# =========================
_mqtt: mqtt.Client | None = None
_mqtt_cfg: Dict[str, Any] = {
    "enabled": ENV_MQTT_ENABLED,
    "host": ENV_MQTT_HOST,
    "port": ENV_MQTT_PORT,
    "user": ENV_MQTT_USER,
    "pass": ENV_MQTT_PASS,
    "topic": ENV_MQTT_TOPIC,
}


def _mqtt_disconnect() -> None:
    """Отключаемся от MQTT аккуратно, без исключений."""
    global _mqtt
    if _mqtt is None:
        return
    try:
        # FIX: останавливаем loop, иначе возможны фоновые ошибки/потоки
        _mqtt.loop_stop()
    except Exception:
        pass
    try:
        _mqtt.disconnect()
    except Exception:
        pass
    _mqtt = None


def mqtt_client() -> mqtt.Client:
    """Lazy MQTT client. Переподключается после apply_settings(), если конфиг менялся."""
    global _mqtt
    if _mqtt is not None:
        return _mqtt

    c = mqtt.Client()
    if _mqtt_cfg.get("user"):
        c.username_pw_set(str(_mqtt_cfg.get("user")), str(_mqtt_cfg.get("pass") or ""))

    c.connect(str(_mqtt_cfg.get("host")), int(_mqtt_cfg.get("port")), 60)

    # FIX: запускаем сетевой цикл, чтобы соединение было устойчивым в долгую
    try:
        c.loop_start()
    except Exception:
        # Если loop_start не стартанул — не валим приложение, просто будем пытаться publish как есть
        pass

    _mqtt = c
    return _mqtt


def mqtt_publish(payload: Dict[str, Any]) -> bool:
    """MQTT publish не должен валить /infer."""
    if not bool(_mqtt_cfg.get("enabled")):
        return False
    try:
        c = mqtt_client()
        topic = str(_mqtt_cfg.get("topic") or "gate/open")
        c.publish(topic, json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        _mqtt_disconnect()
        return False


class MqttTestPublishIn(BaseModel):
    topic: str | None = None
    payload: Dict[str, Any] | None = None


@app.post("/api/v1/mqtt/check")
def api_mqtt_check():
    if not bool(_mqtt_cfg.get("enabled")):
        return {"ok": False, "error": "mqtt_disabled"}
    try:
        c = mqtt_client()
        is_connected = bool(c.is_connected()) if hasattr(c, "is_connected") else True
        return {
            "ok": bool(is_connected),
            "connected": bool(is_connected),
            "host": str(_mqtt_cfg.get("host") or ""),
            "port": int(_mqtt_cfg.get("port") or 1883),
            "topic": str(_mqtt_cfg.get("topic") or "gate/open"),
        }
    except Exception as e:
        _mqtt_disconnect()
        return {"ok": False, "error": "connect_failed", "detail": str(e)}


@app.post("/api/v1/mqtt/test_publish")
def api_mqtt_test_publish(req: MqttTestPublishIn):
    if not bool(_mqtt_cfg.get("enabled")):
        return {"ok": False, "error": "mqtt_disabled"}
    try:
        c = mqtt_client()
        topic = str(req.topic or _mqtt_cfg.get("topic") or "gate/open")
        payload = req.payload if isinstance(req.payload, dict) else {"kind": "ui_test", "ts": time.time()}
        payload.setdefault("kind", "ui_test")
        payload.setdefault("ts", time.time())

        info = c.publish(topic, json.dumps(payload, ensure_ascii=False))
        rc = int(getattr(info, "rc", 0))
        ok = rc == 0
        if not ok:
            _mqtt_disconnect()
        return {"ok": ok, "topic": topic, "rc": rc}
    except Exception as e:
        _mqtt_disconnect()
        return {"ok": False, "error": "publish_failed", "detail": str(e)}


# =========================
# SETTINGS APPLY (callback)
# =========================
def apply_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Применить настройки в рантайме. Возвращает что применили (для UI).

    CHG: единое правило приоритетов runtime-конфига:
      settings.json (cfg) -> ENV fallback -> default.
    """
    applied: Dict[str, Any] = {}
    cfg = settings if isinstance(settings, dict) else {}
    eff_bundle = effective_config(cfg)
    eff = eff_bundle.get("effective") if isinstance(eff_bundle, dict) else {}
    eff = eff if isinstance(eff, dict) else {}
    gate_cfg = eff.get("gate") if isinstance(eff.get("gate"), dict) else {}
    system_cfg = eff.get("system") if isinstance(eff.get("system"), dict) else {}
    mqtt_cfg = system_cfg.get("mqtt") if isinstance(system_cfg.get("mqtt"), dict) else {}

    # --- GATE (runtime tuning: settings.json only, ENV тюнинг игнорируется) ---
    decider.min_conf = float(gate_cfg.get("min_conf", 0.80))
    decider.confirm_n = int(gate_cfg.get("confirm_n", 2))
    decider.window_sec = float(gate_cfg.get("confirm_window_sec", 6.0))
    decider.cooldown_sec = float(gate_cfg.get("cooldown_sec", 15.0))

    new_whitelist = str(gate_cfg.get("whitelist_path") or os.environ.get("WHITELIST_PATH", "/config/whitelist.json"))
    if new_whitelist != getattr(decider, "whitelist_path", ""):
        decider.whitelist_path = new_whitelist
        decider.reload_whitelist()

    if hasattr(decider, "region_check"):
        decider.region_check = bool(gate_cfg.get("region_check", True))
    if hasattr(decider, "region_stab"):
        decider.region_stab = bool(gate_cfg.get("region_stab", True))
    if hasattr(decider, "region_stab_window_sec"):
        decider.region_stab_window_sec = float(gate_cfg.get("region_stab_window_sec", 2.5))
    if hasattr(decider, "region_stab_min_hits"):
        decider.region_stab_min_hits = int(gate_cfg.get("region_stab_min_hits", 3))
    if hasattr(decider, "region_stab_min_ratio"):
        decider.region_stab_min_ratio = float(gate_cfg.get("region_stab_min_ratio", 0.60))

    applied["gate"] = {
        "min_conf": decider.min_conf,
        "confirm_n": decider.confirm_n,
        "confirm_window_sec": decider.window_sec,
        "cooldown_sec": decider.cooldown_sec,
        "whitelist_path": decider.whitelist_path,
        "region_check": getattr(decider, "region_check", False),
        "region_stab": getattr(decider, "region_stab", False),
        "region_stab_window_sec": getattr(decider, "region_stab_window_sec", 0.0),
        "region_stab_min_hits": getattr(decider, "region_stab_min_hits", 0),
        "region_stab_min_ratio": getattr(decider, "region_stab_min_ratio", 0.0),
    }

    # --- MQTT (cfg -> env -> default) ---
    new_cfg = {
        "enabled": bool(mqtt_cfg.get("enabled", True)),
        "host": str(mqtt_cfg.get("host") or "192.168.1.10"),
        "port": int(mqtt_cfg.get("port") or 1883),
        "user": str(mqtt_cfg.get("user") or ""),
        "pass": str(mqtt_cfg.get("pass") or ""),
        "topic": str(mqtt_cfg.get("topic") or "gate/open"),
    }

    changed = new_cfg != _mqtt_cfg
    if changed:
        _mqtt_disconnect()
        _mqtt_cfg.update(new_cfg)

    applied["mqtt"] = {
        "enabled": _mqtt_cfg.get("enabled"),
        "host": _mqtt_cfg.get("host"),
        "port": _mqtt_cfg.get("port"),
        "user": _mqtt_cfg.get("user"),
        "pass": "***" if (_mqtt_cfg.get("pass") or "") else "",
        "topic": _mqtt_cfg.get("topic"),
        "reconnected": changed,
    }

    return applied


# =========================
# INIT SETTINGS STORE + APPLY
# =========================
ui_api.init_settings(SETTINGS_PATH, DEFAULT_SETTINGS)
ui_api.set_apply_callback(apply_settings)

# применяем настройки при старте (best-effort)
try:
    apply_settings(ui_api._require_store().get())  # type: ignore[attr-defined]
except Exception:
    pass

# =========================
# TELEGRAM (optional)
# =========================

_tg_notifier: TelegramNotifier | None = None
_tg_poller: TelegramPoller | None = None


def _tg_log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _tg_get_cfg() -> Dict[str, Any]:
    try:
        return get_settings_store().get()
    except Exception:
        return {}


def _tg_save_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    return get_settings_store().update(patch)


def _tg_pick_token_from_cfg(cfg: Dict[str, Any]) -> str:
    """
    Берём токен из settings.json:
      settings.telegram.enabled == True
      settings.telegram.bot_token не пустой
    """
    try:
        tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        tg = tg if isinstance(tg, dict) else {}
        if not bool(tg.get("enabled")):
            return ""
        return str(tg.get("bot_token") or "").strip()
    except Exception:
        return ""


def _tg_pick_photo_path(cfg: Dict[str, Any]) -> str | None:
    tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    tg = tg if isinstance(tg, dict) else {}
    kind = str(tg.get("photo_kind") or "frame").strip().lower()

    # 1) frame — всегда есть (rtsp_worker пишет live)
    if kind == "frame":
        p = "/config/live/frame.jpg"
        return p if os.path.exists(p) else None

    # 2) plate — best-effort из /debug (если воркер сохраняет)
    if kind == "plate":
        try:
            import glob
            cand = sorted(glob.glob("/debug/*_crop.jpg"), reverse=True)
            if cand:
                return cand[0]
        except Exception:
            pass

    # fallback
    p = "/config/live/frame.jpg"
    return p if os.path.exists(p) else None


def _tg_enqueue_text(text: str, photo_path: str | None):
    # используем тот же notifier, что и для ok=True
    if _tg_notifier is None:
        return
    try:
        from app.integrations.telegram.notifier import TgTask
        _tg_notifier.q.put_nowait(TgTask(text=text, photo_path=photo_path))
    except Exception:
        pass


# hooks для интеграции (events -> tg)
set_telegram_hooks(_tg_get_cfg, _tg_enqueue_text, _tg_pick_photo_path)

# --- token source: cfg > env > default ---
_cfg0 = _tg_get_cfg()
TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_TOKEN_SRC = get_str_src(_cfg0, "telegram.bot_token", "TELEGRAM_BOT_TOKEN", "")

_mqtt_host_dbg, _mqtt_host_src = get_str_src(_cfg0, "mqtt.host", "MQTT_HOST", ENV_MQTT_HOST)
_tg_log(f"[cfg] mqtt.host source={_mqtt_host_src} value={_mqtt_host_dbg or '—'}")
_tg_log(f"[cfg] telegram.token source={TELEGRAM_BOT_TOKEN_SRC} {describe_secret(TELEGRAM_BOT_TOKEN)}")

if TELEGRAM_BOT_TOKEN:
    try:
        tg_client = TelegramClient(TELEGRAM_BOT_TOKEN)

        _tg_notifier = TelegramNotifier(
            tg_client,
            get_cfg=_tg_get_cfg,
            log=_tg_log,
        )
        _tg_notifier.start()

        _tg_poller = TelegramPoller(
            tg_client,
            get_cfg=_tg_get_cfg,
            save_patch=_tg_save_patch,
            notifier=_tg_notifier,
            log=_tg_log,
        )
        _tg_poller.start()

    except Exception as e:
        _tg_log(f"[tg] ERROR: init failed: {type(e).__name__}: {e}")
else:
    # поясняем, почему выключено
    tg_enabled = False
    try:
        tg0 = _cfg0.get("telegram") if isinstance(_cfg0.get("telegram"), dict) else {}
        tg0 = tg0 if isinstance(tg0, dict) else {}
        tg_enabled = bool(tg0.get("enabled"))
    except Exception:
        tg_enabled = False

    if not tg_enabled:
        _tg_log("[tg] disabled (settings.telegram.enabled is false)")
    else:
        _tg_log("[tg] disabled (no telegram.bot_token in settings.json and no TELEGRAM_BOT_TOKEN in env fallback)")

# =========================
# OCR helpers
# =========================
def _iter_orientation_candidates(img_bgr: np.ndarray) -> Iterable[Tuple[str, np.ndarray]]:
    """Кандидаты ориентации для OCR:
    - всегда 0° и 180°
    - 90°/270° только если кадр вертикальный (h > w)
    """
    yield "rot0", img_bgr
    yield "rot180", cv2.rotate(img_bgr, cv2.ROTATE_180)
    if img_bgr.shape[0] > img_bgr.shape[1]:
        yield "rot90", cv2.rotate(img_bgr, cv2.ROTATE_90_CLOCKWISE)
        yield "rot270", cv2.rotate(img_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)


def _is_bad_ocr(plate_norm: str, conf: float, valid: bool) -> bool:
    """Критерии "плохого OCR" для решения, надо ли запускать тяжёлые ветки (orient/warp).

    Логика deliberately простая и воспроизводимая:
    - invalid РФ формат -> плохой
    - conf ниже порога -> плохой
    - слишком короткая строка -> плохой
    """
    if not plate_norm:
        return True
    if len(plate_norm) < int(ENV_OCR_BAD_MIN_LEN):
        return True
    if not bool(valid):
        return True
    if float(conf) < float(ENV_OCR_BAD_MIN_CONF):
        return True
    return False


def _ocr_try_best(img_bgr: np.ndarray) -> Dict[str, Any]:
    """Адаптивный OCR для CPU-only.

    Принцип:
    1) Быстрый baseline: OCR на исходном кадре (rot0)
    2) Если baseline "плохой" -> пробуем ориентации (если включено)
    3) Если всё ещё "плохо" -> пробуем warp (тяжёлый шаг)

    Важно: warp делаем ТОЛЬКО если реально нужно.
    """
    t_total0 = time.time()
    timing_ms: Dict[str, float] = {"ocr": 0.0, "orient": 0.0, "warp": 0.0}

    def score_for(plate_norm: str, conf: float, valid: bool) -> float:
        # бонус валидности небольшой, но помогает отбрасывать мусор
        return float(conf) + (0.15 if bool(valid) else 0.0)

    # держим текущий "лучший" кадр-кандидат, чтобы warp делать именно на нём
    best_img: np.ndarray = img_bgr

    best: Dict[str, Any] = {
        "raw": "",
        "plate_norm": "",
        "conf": 0.0,
        "variant": "rot0",
        "warped": False,
        "score": -1.0,
        "timing_ms": timing_ms,
    }

    # --- 1) baseline (самый быстрый) ---
    t0 = time.time()
    raw0, conf0 = ocr.infer_bgr(img_bgr)
    timing_ms["ocr"] += (time.time() - t0) * 1000.0

    plate0 = normalize_ru_plate(raw0)
    valid0 = is_valid_ru_plate_strict(plate0, region_check=decider.region_check)
    best.update(
        {
            "raw": str(raw0),
            "plate_norm": str(plate0),
            "conf": float(conf0),
            "variant": "rot0",
            "warped": False,
            "score": score_for(plate0, conf0, valid0),
        }
    )

    # если baseline хороший — сразу выходим (минимальная задержка)
    if not _is_bad_ocr(plate0, float(conf0), bool(valid0)):
        best["timing_ms"]["total"] = (time.time() - t_total0) * 1000.0
        best.pop("score", None)
        return best

    # --- 2) ориентации (средняя цена) ---
    if ENV_OCR_ORIENT_TRY:
        t_or0 = time.time()
        for label, cand in _iter_orientation_candidates(img_bgr):
            if label == "rot0":
                continue
            t1 = time.time()
            raw, conf = ocr.infer_bgr(cand)
            timing_ms["ocr"] += (time.time() - t1) * 1000.0
            plate_norm = normalize_ru_plate(raw)
            valid = is_valid_ru_plate_strict(plate_norm, region_check=decider.region_check)
            sc = score_for(plate_norm, conf, valid)
            if sc > float(best["score"]):
                best.update(
                    {
                        "raw": str(raw),
                        "plate_norm": str(plate_norm),
                        "conf": float(conf),
                        "variant": label,
                        "warped": False,
                        "score": sc,
                    }
                )
                best_img = cand

            # ранний выход: нашли хороший вариант
            bp = str(best.get("plate_norm") or "")
            bc = float(best.get("conf") or 0.0)
            bv = is_valid_ru_plate_strict(bp, region_check=decider.region_check)
            if not _is_bad_ocr(bp, bc, bv):
                break
        timing_ms["orient"] += (time.time() - t_or0) * 1000.0

    # --- 3) warp (дорого) ---
    best_plate = str(best.get("plate_norm") or "")
    best_conf = float(best.get("conf") or 0.0)
    best_valid = is_valid_ru_plate_strict(best_plate, region_check=decider.region_check)

    if ENV_OCR_WARP_TRY and _is_bad_ocr(best_plate, best_conf, best_valid):
        t_w0 = time.time()
        # warping делаем только для лучшего кандидата (а не для всех ориентаций)
        # FIX: rectifier может падать — /infer не должен из-за этого падать
        try:
            warped, _quad = rectify_plate_quad(best_img, out_w=ENV_OCR_WARP_W, out_h=ENV_OCR_WARP_H)
        except Exception:
            warped = None

        if warped is not None and getattr(warped, "size", 0) > 0:
            t2 = time.time()
            raw2, conf2 = ocr.infer_bgr(warped)
            timing_ms["ocr"] += (time.time() - t2) * 1000.0
            plate2 = normalize_ru_plate(raw2)
            valid2 = is_valid_ru_plate_strict(plate2, region_check=decider.region_check)
            sc2 = score_for(plate2, conf2, valid2)
            if sc2 > float(best["score"]):
                best.update(
                    {
                        "raw": str(raw2),
                        "plate_norm": str(plate2),
                        "conf": float(conf2),
                        "variant": "warp",
                        "warped": True,
                        "score": sc2,
                    }
                )

        timing_ms["warp"] += (time.time() - t_w0) * 1000.0

    best["timing_ms"]["total"] = (time.time() - t_total0) * 1000.0
    best.pop("score", None)
    return best


# =========================
# ROUTES
# =========================

START_TS = time.time()


@app.get("/health")
def health():
    """
    Product health endpoint (v0.3.0)
    Используется:
    - UI (вкладка "Система")
    - updater
    - внешняя диагностика
    """

    uptime_sec = int(time.time() - START_TS)

    return {
        "ok": True,

        # -------- Версия / сборка --------
        "version": os.getenv("APP_VERSION", "dev"),
        "git": os.getenv("GIT_SHA", ""),
        "build_time": os.getenv("BUILD_TIME", ""),

        # -------- Runtime --------
        "uptime_sec": uptime_sec,

        # -------- Конфигурация --------
        "model": MODEL_PATH,
        "settings_path": SETTINGS_PATH,

        # -------- MQTT --------
        "mqtt": {
            "enabled": bool(_mqtt_cfg.get("enabled")),
            "host": str(_mqtt_cfg.get("host") or ""),
            "port": int(_mqtt_cfg.get("port") or 1883),
            "topic": str(_mqtt_cfg.get("topic") or ""),
        },

        # -------- Последний номер (best-effort) --------
        "last_plate": getattr(app.state, "last_plate", "") if hasattr(app.state, "last_plate") else "",
    }


# =========================================================
# NEW: alias for UI routes (v0.3.1)
# UI всегда ходит в /api/v1/... (через Vite proxy /api -> backend)
# =========================================================
@app.get("/api/v1/health")
def health_v1():
    return health()


@app.post("/reload")
def reload_whitelist():
    """Перечитать whitelist.json без перезапуска контейнера."""
    decider.reload_whitelist()
    return {"ok": True, "whitelist_size": len(decider.whitelist)}


@app.post("/infer")
async def infer(
    file: UploadFile = File(...),
    pre_variant: str | None = Form(None),
    pre_warped: str | None = Form(None),
    pre_timing_ms: str | None = Form(None),
):
    """Принимает JPEG/PNG → OCR → gate-логика → MQTT → событие для UI."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="upload an image")

    data = await file.read()

    # decode один раз
    t_decode0 = time.time()

    # NEW: дефолт best, чтобы ниже код (variant/timing) всегда был устойчивым
    best: Dict[str, Any] = {
        "raw": "",
        "plate_norm": "",
        "conf": 0.0,
        "variant": "error",
        "warped": False,
        "timing_ms": {},
    }

    raw = ""
    plate_norm = ""
    conf = 0.0
    decode_ms = 0.0

    # NEW: diagnose without 400
    ocr_error: str | None = None
    infer_ok: bool = True

    # -----------------------------
    # 1) Decode (это реально "плохой запрос/данные" => 400)
    # -----------------------------
    try:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("cannot decode image")
        decode_ms = (time.time() - t_decode0) * 1000.0
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"cannot decode image: {e}")

    # -----------------------------
    # 2) OCR pipeline (это НЕ ошибка запроса => soft reject, всегда 200)
    # -----------------------------
    try:
        best = _ocr_try_best(img)
        raw = str(best.get("raw") or "")
        plate_norm = str(best.get("plate_norm") or normalize_ru_plate(raw))
        conf = float(best.get("conf") or 0.0)
    except Exception as e:
        # FIX: раньше тут было HTTP 400. Теперь это штатный отказ.
        infer_ok = False
        ocr_error = f"{type(e).__name__}: {e}"
        best = {
            "raw": "",
            "plate_norm": "",
            "conf": 0.0,
            "variant": "error",
            "warped": False,
            "timing_ms": {},
        }
        raw = ""
        plate_norm = ""
        conf = 0.0

    # -----------------------------
    # meta от rtsp_worker (best-effort)
    # -----------------------------
    # NEW: rtsp_worker может присылать поля через multipart/form-data:
    # - pre_variant: "crop" / "rectify" (или другое)
    # - pre_warped: "1"/"0"/"true"/"false"
    # - pre_timing_ms: JSON-строка с таймингами до /infer (например http_ms/rectify_ms)
    pre_warped_bool: bool | None = None
    if pre_warped is not None:
        v = str(pre_warped).strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            pre_warped_bool = True
        elif v in ("0", "false", "no", "n", "off"):
            pre_warped_bool = False

    pre_timing: Dict[str, Any] | None = None
    if pre_timing_ms:
        try:
            x = json.loads(str(pre_timing_ms))
            if isinstance(x, dict):
                pre_timing = x
        except Exception:
            pre_timing = None

    # -----------------------------
    # Решение gate-логики (soft reject для пустого/упавшего OCR)
    # -----------------------------
    # FIX: если OCR упал или вернул пусто — НЕ вызываем decider.decide(), возвращаем штатный отказ
    if not plate_norm:
        decision: Dict[str, Any] = {
            "plate": "",
            "valid": False,
            "allowed": False,
            "ok": False,
            "reason": "ocr_failed" if (not infer_ok) else "empty_ocr",
            "stabilized": False,
            "stab_reason": "empty",
            "hits": 0,
            "hits_window_sec": float(decider.window_sec),
        }
        valid_norm = False
        noise = True  # пустой OCR считаем "мусором" для UI по умолчанию
    else:
        # -----------------------------
        # Фильтрация OCR мусора (продуктовый режим)
        # -----------------------------
        # Мусор скрываем из UI и не печатаем в лог по умолчанию.
        # Но в debug-режиме он сохраняется в событиях для диагностики.
        valid_norm = is_valid_ru_plate_strict(plate_norm, region_check=decider.region_check)
        noise = bool(is_noise_ocr(raw) and not valid_norm)

        # FIX: GateDecider получает уже нормализованный номер
        decision = decider.decide(plate_norm, conf)

        # Если распознали откровенный мусор — помечаем reason (не ломая контракт)
        if noise:
            decision = {
                **decision,
                "ok": False,
                "allowed": False,
                "valid": False,
                "reason": "noise_ocr",
                "hits": 0,
                "hits_window_sec": float(decider.window_sec),
            }

    # variant/warped для диагностики
    variant = (pre_variant or best.get("variant") or best.get("ocr_variant") or "crop")
    warped = pre_warped_bool if pre_warped_bool is not None else bool(best.get("warped", False))

    timing_ms: Dict[str, Any] = {
        "decode": round(float(decode_ms), 2),
        **{k: round(float(v), 2) for k, v in dict(best.get("timing_ms") or {}).items()},
    }
    if isinstance(pre_timing, dict):
        timing_ms["pre"] = pre_timing

    payload: Dict[str, Any] = {
        "ts": time.time(),

        # NEW: health of infer (штатный отказ != 400)
        "infer_ok": bool(infer_ok),

        # NEW: diagnose OCR failures without breaking contract
        "ocr_error": ocr_error,

        "raw": raw,
        "plate_norm": plate_norm,
        "conf": round(float(conf), 4),

        # backward compat (старые ключи)
        "ocr_variant": best.get("variant", "rot0"),
        "ocr_warped": bool(best.get("warped", False)),

        # новые унифицированные ключи
        "variant": variant,
        "warped": bool(warped),
        "timing_ms": timing_ms,
        "noise": bool(noise),
        "log_level": "debug" if noise else "info",
        "meta": {"variant": variant, "warped": bool(warped), "timing_ms": timing_ms},

        # decision включает: plate/valid/allowed/ok/reason/stabilized/...
        **decision,
    }

    # MQTT: публикуем только при ok (никогда не публикуем мусор)
    published = False
    if payload.get("infer_ok") and decision.get("ok") and not noise:
        published = mqtt_publish(payload)
    payload["mqtt_published"] = bool(published)

    # Last recognized crops for UI (ring buffer, best-effort)
    if payload.get("infer_ok") and payload.get("plate") and payload.get("valid") and not noise:
        _save_recent_plate_crop(img, payload)

    # Telegram notify: только при ok и не мусор (не блокируем infer)
    try:
        if payload.get("infer_ok") and decision.get("ok") and not noise and _tg_notifier is not None:
            _tg_notifier.set_last_ok(payload)
            cfg = _tg_get_cfg()
            photo_path = _tg_pick_photo_path(cfg)
            _tg_notifier.enqueue_ok(payload, photo_path=photo_path)
    except Exception:
        pass

    # UI events (не ломаем infer)
    try:
        push_event_from_infer(payload)
    except Exception:
        pass

    # Логирование: по умолчанию печатаем только полезное
    if PRINT_EVERY_RESPONSE or (DEBUG_LOG and noise) or (not noise and DEBUG_LOG):
        try:
            print(
                f"[infer] infer_ok={payload.get('infer_ok')} plate={payload.get('plate')} "
                f"raw={payload.get('raw')} conf={payload.get('conf')} reason={payload.get('reason')} "
                f"level={payload.get('log_level')} ocr_error={payload.get('ocr_error')}"
            )
        except Exception:
            pass

    return payload


# -------------------------
# UI (static build)
# -------------------------
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/app/static")
INDEX_PATH = os.path.join(STATIC_DIR, "index.html")

if os.path.exists(INDEX_PATH):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
