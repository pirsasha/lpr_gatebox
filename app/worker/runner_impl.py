# =========================================================
# Файл: app/worker/runner_impl.py
# Проект: LPR GateBox
# Версия: v0.3.9-auto-metrics-roi-deskew (PATCH)
# Обновлено: 2026-02-12 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - FIX: auto day/night метрики считаем по ROI сцены (AUTO_METRICS_SOURCE=roi|crop)
# - NEW: deskew (DESKEW_ENABLE) — лёгкий поворот по горизонту номера для OCR
# - CHG: deskew_* добавлен в pre_timing (meta в gatebox)
# - CHG: добавлена диагностика причин pre-sanity reject (`sanity_fail_reason`)
# - ВАЖНО: остальной пайплайн сохранён (settings poll, tracking, freeze, live, debug)
# =========================================================

from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.worker.settings import env_bool, env_float, env_int, env_str, parse_roi, parse_roi_poly_str, point_in_polygon, expand_box
from app.worker.forensics import ensure_dir, atomic_write_bytes, atomic_write_json
from app.worker.http_client import infer_base_url as _infer_base_url
from app.worker.http_client import post_heartbeat as _post_heartbeat
from app.worker.http_client import fetch_camera_settings, fetch_settings_json
from app.worker.http_client import post_crop
from app.worker.detector import PlateDetector, DetBox
from app.worker.capture import AutoGrabber
from app.worker.tracker import TrackState, iou, smooth_box
from app.worker.policy import PlateEventState
from app.worker.live_preview import write_live_preview

# rectifier
try:
    from app.core.plate_rectifier import rectify_plate_quad  # type: ignore
except ModuleNotFoundError:
    from core.plate_rectifier import rectify_plate_quad  # type: ignore

# AUTO day/night
from app.worker.plate_auto import AutoConfig, AutoState, AutoDecision, decide_auto
from app.worker.plate_preproc import apply_profile


# -----------------------------
# ENV
# -----------------------------
RTSP_URL_DEFAULT = env_str("RTSP_URL", "")
INFER_URL = env_str("INFER_URL", "http://gatebox:8080/infer")

RTSP_FPS = env_float("RTSP_FPS", 1.0)
WORKER_DEBUG = env_bool("WORKER_DEBUG", False)

READ_FPS = env_float("READ_FPS", max(4.0, RTSP_FPS))
DET_FPS = env_float("DET_FPS", 2.0)
SEND_FPS = env_float("SEND_FPS", 2.0)

CAPTURE_BACKEND = env_str("CAPTURE_BACKEND", "auto").strip().lower()
FFMPEG_PROBE = env_bool("FFMPEG_PROBE", False)

AUTO_SWITCH_CHECK_SEC = env_float("AUTO_SWITCH_CHECK_SEC", 1.0)
AUTO_SWITCH_AGE_MS = env_int("AUTO_SWITCH_AGE_MS", 300)
AUTO_SWITCH_STREAK = env_int("AUTO_SWITCH_STREAK", 5)
AUTO_SWITCH_COOLDOWN_SEC = env_float("AUTO_SWITCH_COOLDOWN_SEC", 15.0)

FFMPEG_THREADS = env_int("FFMPEG_THREADS", 1)
FFMPEG_READ_TIMEOUT_SEC = env_float("FFMPEG_READ_TIMEOUT_SEC", 2.0)

# Heartbeat
CAMERA_ID = env_str("CAMERA_ID", "cam1")
HB_EVERY_SEC = env_float("HB_EVERY_SEC", 1.0)

HEARTBEAT_URL = env_str("HEARTBEAT_URL", "")
base = _infer_base_url(INFER_URL)
if not HEARTBEAT_URL:
    HEARTBEAT_URL = (base + "/api/rtsp/heartbeat") if base else ""

# settings poll
SETTINGS_POLL_SEC = env_float("SETTINGS_POLL_SEC", 1.5)
SETTINGS_BASE_URL = env_str("SETTINGS_BASE_URL", base)

# =========================================================
# Runtime overrides from UI (settings.json)
# ---------------------------------------------------------
# The UI edits /config/settings.json via gatebox API.
# rtsp_worker periodically fetches that JSON and can override
# selected knobs without a container restart.
#
# settings.json schema (minimal):
# {
#   "camera": {"enabled": true, "rtsp_url": "..."},
#   "rtsp_worker": {
#     "overrides": {
#       "DET_CONF": 0.4,
#       "RECTIFY": 1,
#       "PLATE_PAD_BASE": 0.07
#     }
#   }
# }

_OVERRIDABLE: dict[str, tuple[str, callable]] = {
    # name: (type_name, caster)
    "READ_FPS": ("float", float),
    "DET_FPS": ("float", float),
    "SEND_FPS": ("float", float),
    "DET_CONF": ("float", float),
    "DET_IOU": ("float", float),
    "DET_IMG_SIZE": ("int", int),
    "PLATE_PAD": ("float", float),
    "PLATE_PAD_BASE": ("float", float),
    "PLATE_PAD_SMALL": ("float", float),
    "PLATE_PAD_SMALL_W": ("int", int),
    "PLATE_PAD_SMALL_H": ("int", int),
    "PLATE_PAD_MAX": ("float", float),
    "RECTIFY": ("bool", lambda v: bool(int(v)) if isinstance(v, (int, str)) else bool(v)),
    "RECTIFY_W": ("int", int),
    "RECTIFY_H": ("int", int),
    "REFINE_INNER_PAD": ("float", float),
    "REFINE_MIN_AREA_RATIO": ("float", float),
    "DESKEW_ENABLE": ("bool", lambda v: bool(int(v)) if isinstance(v, (int, str)) else bool(v)),
    "DESKEW_MAX_ANGLE_DEG": ("float", float),
    "DESKEW_MIN_ANGLE_DEG": ("float", float),
    "UPSCALE_ENABLE": ("bool", lambda v: bool(int(v)) if isinstance(v, (int, str)) else bool(v)),
    "UPSCALE_MIN_W": ("int", int),
    "UPSCALE_MIN_H": ("int", int),
    "JPEG_QUALITY": ("int", int),

    # --- Debug (форензика/логи) ---
    # Важно: эти параметры управляются из UI через settings.json (rtsp_worker.overrides)
    # и применяются на лету (poll раз в SETTINGS_POLL_SEC).
    "SAVE_DIR": ("str", str),
    "SAVE_EVERY": ("int", int),
    "SAVE_FULL_FRAME": ("bool", lambda v: bool(int(v)) if isinstance(v, (int, str)) else bool(v)),
    "SAVE_WITH_ROI": ("bool", lambda v: bool(int(v)) if isinstance(v, (int, str)) else bool(v)),
    "LOG_EVERY_SEC": ("float", float),

    # Sanity filter knobs (plate shape/size gate)
    "SANITY_ASPECT_MIN_BASE": ("float", float),
    "SANITY_ASPECT_MIN_ADAPTIVE": ("float", float),
    "SANITY_ADAPTIVE_CONF_MIN": ("float", float),
    "SANITY_ADAPTIVE_AREA_MIN": ("float", float),
    "SANITY_MIN_WIDTH_PX": ("int", int),
    "SANITY_MIN_HEIGHT_PX": ("int", int),
    "SANITY_DEBUG_REJECT_EVERY_SEC": ("float", float),

    # ROI (scene crop)
    "ROI_STR": ("str", str),
    "ROI_POLY_STR": ("str", str),
}

_REQUIRES_REBUILD_DETECTOR = {"DET_CONF", "DET_IOU", "DET_IMG_SIZE"}


def _get_settings_overrides(settings_json: dict) -> dict:
    try:
        rt = (settings_json or {}).get("rtsp_worker") or {}
        overrides = rt.get("overrides") or {}
        return overrides if isinstance(overrides, dict) else {}
    except Exception:
        return {}

def _apply_runtime_overrides(overrides: dict | None, last: dict) -> tuple[dict, dict]:
    """
    Applies overrides to module globals.

    Returns:
      (new_last, flags)
      flags = {"detector_rebuild": bool, "grabber_restart": bool}
    """
    flags = {"detector_rebuild": False, "grabber_restart": False}

    if not isinstance(overrides, dict) or not overrides:
        return last, flags

    new_last = dict(last)

    # если поменялись такие ключи — grabber лучше перезапустить
    REQUIRES_GRABBER_RESTART = {
        "READ_FPS",
        "CAPTURE_BACKEND",
        "RTSP_TRANSPORT",
        "RTSP_OPEN_TIMEOUT_MS",
        "RTSP_READ_TIMEOUT_MS",
        "RTSP_DRAIN_GRABS",
        "FFMPEG_THREADS",
        "FFMPEG_READ_TIMEOUT_SEC",
        "AUTO_SWITCH_CHECK_SEC",
        "AUTO_SWITCH_AGE_MS",
        "AUTO_SWITCH_STREAK",
        "AUTO_SWITCH_COOLDOWN_SEC",
    }

    for k, v in overrides.items():
        if k not in _OVERRIDABLE:
            continue

        _, caster = _OVERRIDABLE[k]
        try:
            cast_v = caster(v)
        except Exception:
            continue

        # only apply if changed
        if k in new_last and new_last[k] == cast_v:
            continue

        # set module global
        globals()[k] = cast_v
        new_last[k] = cast_v

        if k in _REQUIRES_REBUILD_DETECTOR:
            flags["detector_rebuild"] = True
        if k in REQUIRES_GRABBER_RESTART:
            flags["grabber_restart"] = True

    return new_last, flags

# ROI
ROI_STR = env_str("ROI_STR", env_str("ROI", ""))
ROI_POLY_STR = env_str("ROI_POLY_STR", "")

OCR_CROP_MODE = env_str("OCR_CROP_MODE", "yolo").lower()
SEND_ON_NO_DET = env_bool("SEND_ON_NO_DET", False)

DET_MODEL_PATH = env_str("DET_MODEL_PATH", "/models/plate_det.pt")
DET_CONF = env_float("DET_CONF", 0.35)
DET_IOU = env_float("DET_IOU", 0.45)
DET_IMG_SIZE = env_int("DET_IMG_SIZE", 640)

# pads
PLATE_PAD = env_float("PLATE_PAD", 0.08)
PLATE_PAD_BASE = env_float("PLATE_PAD_BASE", PLATE_PAD)
PLATE_PAD_SMALL = env_float("PLATE_PAD_SMALL", 0.12)
PLATE_PAD_SMALL_W = env_int("PLATE_PAD_SMALL_W", 260)
PLATE_PAD_SMALL_H = env_int("PLATE_PAD_SMALL_H", 85)
PLATE_PAD_MAX = env_float("PLATE_PAD_MAX", 0.16)
# минимальный асимметричный запас справа, чтобы реже отрезать регион
PLATE_PAD_RIGHT_EXTRA = env_float("PLATE_PAD_RIGHT_EXTRA", 0.04)

MIN_PLATE_W = env_int("MIN_PLATE_W", 80)
MIN_PLATE_H = env_int("MIN_PLATE_H", 20)

JPEG_QUALITY = env_int("JPEG_QUALITY", 90)
HTTP_TIMEOUT_SEC = env_float("HTTP_TIMEOUT_SEC", 2.0)

RECTIFY = env_bool("RECTIFY", False)
RECTIFY_W = env_int("RECTIFY_W", 320)
RECTIFY_H = env_int("RECTIFY_H", 96)

# Tracking
TRACK_ENABLE = env_bool("TRACK_ENABLE", True)
TRACK_HOLD_SEC = env_float("TRACK_HOLD_SEC", 1.6)
TRACK_ALPHA = env_float("TRACK_ALPHA", 0.75)
TRACK_IOU_MIN = env_float("TRACK_IOU_MIN", 0.18)

# Stabilization strategy
STAB_MODE = env_str("STAB_MODE", "track").strip().lower()
if STAB_MODE not in ("track", "plate", "hybrid"):
    STAB_MODE = "track"

# Best-crop buffering (optional): choose best crop in short window before sending
BEST_CROP_ENABLE = env_bool("BEST_CROP_ENABLE", False)
BEST_CROP_WINDOW_SEC = env_float("BEST_CROP_WINDOW_SEC", 1.5)
BEST_CROP_MAX_SEND = env_int("BEST_CROP_MAX_SEND", 1)

# Decision log rate-limit
DECISION_LOG_EVERY_SEC = env_float("DECISION_LOG_EVERY_SEC", 2.0)
STATE_LOG_EVERY_SEC = env_float("STATE_LOG_EVERY_SEC", 5.0)

# Event mode
EVENT_MODE = env_str("EVENT_MODE", "on_plate_change").lower()
PLATE_CONFIRM_K = env_int("PLATE_CONFIRM_K", 2)
PLATE_CONFIRM_WINDOW_SEC = env_float("PLATE_CONFIRM_WINDOW_SEC", 1.8)

# Throttling
GLOBAL_SEND_MIN_INTERVAL_SEC = env_float("GLOBAL_SEND_MIN_INTERVAL_SEC", 0.7)
PLATE_RESEND_SEC = env_float("PLATE_RESEND_SEC", 15.0)

# Debug saving
SAVE_DIR = env_str("SAVE_DIR", "/debug")

# Дефолты из ENV (чтобы было куда "откатываться" при выключении галочки)
SAVE_EVERY_ENV = env_int("SAVE_EVERY", 0)
SAVE_FULL_FRAME_ENV = env_bool("SAVE_FULL_FRAME", 0)
SAVE_WITH_ROI_ENV = env_bool("SAVE_WITH_ROI", 0)
LOG_EVERY_SEC_ENV = env_int("LOG_EVERY_SEC", 5)

# Текущие runtime-значения (могут быть переопределены настройками из UI)
SAVE_EVERY = SAVE_EVERY_ENV
SAVE_FULL_FRAME = SAVE_FULL_FRAME_ENV
SAVE_WITH_ROI = SAVE_WITH_ROI_ENV
LOG_EVERY_SEC = LOG_EVERY_SEC_ENV

SAVE_SEND_BYTES = env_bool("SAVE_SEND_BYTES", False)
CANDIDATE_DEBUG_ENABLE = env_bool("CANDIDATE_DEBUG_ENABLE", False)
CANDIDATE_DEBUG_EVERY_SEC = env_float("CANDIDATE_DEBUG_EVERY_SEC", 2.0)
CANDIDATE_DEBUG_SAMPLE = env_bool("CANDIDATE_DEBUG_SAMPLE", False)
CANDIDATE_DEBUG_COORDS = env_bool("CANDIDATE_DEBUG_COORDS", False)
CANDIDATE_DEBUG_SAVE = env_bool("CANDIDATE_DEBUG_SAVE", False)
UPSCALE_ENABLE = env_bool("UPSCALE_ENABLE", True)
UPSCALE_MIN_W = env_int("UPSCALE_MIN_W", 320)
UPSCALE_MIN_H = env_int("UPSCALE_MIN_H", 96)

# LIVE snapshot
LIVE_DIR = env_str("LIVE_DIR", "/config/live")
LIVE_EVERY_SEC = env_float("LIVE_EVERY_SEC", 1.0)
LIVE_JPEG_QUALITY = env_int("LIVE_JPEG_QUALITY", 80)
LIVE_SAVE_QUAD = env_bool("LIVE_SAVE_QUAD", True)
SANITY_ASPECT_MIN_BASE = env_float("SANITY_ASPECT_MIN_BASE", 1.80)
SANITY_ASPECT_MIN_ADAPTIVE = env_float("SANITY_ASPECT_MIN_ADAPTIVE", 1.60)
SANITY_ADAPTIVE_CONF_MIN = env_float("SANITY_ADAPTIVE_CONF_MIN", 0.75)
SANITY_ADAPTIVE_AREA_MIN = env_float("SANITY_ADAPTIVE_AREA_MIN", 0.0065)
SANITY_MIN_WIDTH_PX = env_int("SANITY_MIN_WIDTH_PX", 140)
SANITY_MIN_HEIGHT_PX = env_int("SANITY_MIN_HEIGHT_PX", 60)
SANITY_DEBUG_REJECT_EVERY_SEC = env_float("SANITY_DEBUG_REJECT_EVERY_SEC", 3.0)

# RTSP watchdog/freeze
RTSP_TRANSPORT = env_str("RTSP_TRANSPORT", "tcp").lower()
RTSP_OPEN_TIMEOUT_MS = env_int("RTSP_OPEN_TIMEOUT_MS", 8000)
RTSP_READ_TIMEOUT_MS = env_int("RTSP_READ_TIMEOUT_MS", 30000)
RTSP_DRAIN_GRABS = env_int("RTSP_DRAIN_GRABS", 2)

FREEZE_ENABLE = env_bool("FREEZE_ENABLE", True)
FREEZE_DIFF_MEAN_THR = env_float("FREEZE_DIFF_MEAN_THR", 0.35)
FREEZE_MAX_SEC = env_float("FREEZE_MAX_SEC", 3.0)
FREEZE_EVERY_N = env_int("FREEZE_EVERY_N", 3)

# ---------------- AUTO day/night ENV ----------------
AUTO_MODE = env_bool("AUTO_MODE", False)
AUTO_EVERY_N = env_int("AUTO_EVERY_N", 3)
AUTO_HYST_SEC = env_float("AUTO_HYST_SEC", 2.0)

AUTO_LUMA_DAY = env_int("AUTO_LUMA_DAY", 95)
AUTO_LUMA_NIGHT = env_int("AUTO_LUMA_NIGHT", 65)
AUTO_SAT_GLARE = env_float("AUTO_SAT_GLARE", 0.08)

AUTO_DROP_ON_BLUR = env_bool("AUTO_DROP_ON_BLUR", True)
AUTO_BLUR_MIN = env_float("AUTO_BLUR_MIN", 35.0)
AUTO_DROP_ON_GLARE = env_bool("AUTO_DROP_ON_GLARE", False)

AUTO_PAD_ENABLE = env_bool("AUTO_PAD_ENABLE", True)
AUTO_PREPROC_ENABLE = env_bool("AUTO_PREPROC_ENABLE", False)  # env-only (not in AutoConfig)
AUTO_RECTIFY = env_bool("AUTO_RECTIFY", True)

AUTO_UPSCALE_ENABLE = env_bool("AUTO_UPSCALE_ENABLE", True)
AUTO_UPSCALE_DAY_W = env_int("AUTO_UPSCALE_DAY_W", 480)
AUTO_UPSCALE_DAY_H = env_int("AUTO_UPSCALE_DAY_H", 144)
AUTO_UPSCALE_NIGHT_W = env_int("AUTO_UPSCALE_NIGHT_W", 720)
AUTO_UPSCALE_NIGHT_H = env_int("AUTO_UPSCALE_NIGHT_H", 224)

# ---------------- NEW: auto metrics source ----------------
# roi  -> метрики яркости/засвета по сцене (правильно для day/night)
# crop -> по кропу номера (может "липнуть" к night на тёмных авто/грязи)
AUTO_METRICS_SOURCE = env_str("AUTO_METRICS_SOURCE", "roi").strip().lower()
AUTO_METRICS_DOWNSCALE_W = env_int("AUTO_METRICS_DOWNSCALE_W", 320)

# ---------------- NEW: deskew ----------------
DESKEW_ENABLE = env_bool("DESKEW_ENABLE", True)
DESKEW_MAX_ANGLE_DEG = env_float("DESKEW_MAX_ANGLE_DEG", 12.0)
DESKEW_MIN_ANGLE_DEG = env_float("DESKEW_MIN_ANGLE_DEG", 1.0)


# -----------------------------
# helpers
# -----------------------------
def choose_plate_pad(bbox_w: int, bbox_h: int) -> Tuple[float, str]:
    pad = float(PLATE_PAD_BASE)
    reason = "base"
    try:
        if int(bbox_w) < int(PLATE_PAD_SMALL_W) or int(bbox_h) < int(PLATE_PAD_SMALL_H):
            pad = float(PLATE_PAD_SMALL)
            reason = "small_bbox"
    except Exception:
        pad = float(PLATE_PAD_BASE)
        reason = "base_exc"

    pad = max(0.0, min(float(PLATE_PAD_MAX), float(pad)))
    return float(pad), str(reason)


def _plate_norm(plate: str) -> str:
    # Сохраняем и латиницу, и кириллицу (иначе "У616НН761" превращается в "616761").
    return re.sub(r"[^0-9A-ZА-ЯЁ]", "", str(plate or "").upper())


def _sharpness_score(img: Optional[np.ndarray]) -> float:
    if img is None or img.size <= 0:
        return 0.0
    try:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        return float(cv2.Laplacian(g, cv2.CV_64F).var())
    except Exception:
        return 0.0


def rectify_plate(crop_bgr: np.ndarray, out_w: int, out_h: int) -> Optional[np.ndarray]:
    warped, _quad = rectify_plate_quad(crop_bgr, out_w=out_w, out_h=out_h)
    return warped


def sanity_check_crop(
    img: np.ndarray,
    det_conf: Optional[float] = None,
    bbox_wh: Optional[Tuple[int, int]] = None,
    frame_wh: Optional[Tuple[int, int]] = None,
) -> tuple[bool, str, Dict[str, float | str]]:
    metrics: Dict[str, float | str] = {}
    try:
        hh, ww = img.shape[:2]
    except Exception:
        return False, "invalid_shape", {"rule": "invalid_shape"}

    metrics["crop_w"] = float(ww)
    metrics["crop_h"] = float(hh)
    ar = float(ww) / float(max(1, hh))
    metrics["aspect"] = float(ar)
    metrics["det_conf"] = float(det_conf) if det_conf is not None else -1.0

    if ww < int(SANITY_MIN_WIDTH_PX) or hh < int(SANITY_MIN_HEIGHT_PX):
        metrics["rule"] = "too_small"
        return False, f"too_small:{ww}x{hh}<min{int(SANITY_MIN_WIDTH_PX)}x{int(SANITY_MIN_HEIGHT_PX)}", metrics

    # base threshold keeps strict filtering for low-confidence/small detections
    ar_min = float(SANITY_ASPECT_MIN_BASE)
    rule = "base"
    if det_conf is not None:
        bw, bh = (bbox_wh or (ww, hh))
        frame_w, frame_h = (frame_wh or (0, 0))
        bbox_area_ratio = 0.0
        if frame_w > 0 and frame_h > 0:
            bbox_area_ratio = float(max(1, bw) * max(1, bh)) / float(frame_w * frame_h)
        metrics["bbox_area_ratio"] = float(bbox_area_ratio)

        # Adaptive relax: high-confidence + non-tiny bbox may pass with slightly lower AR
        if float(det_conf) >= float(SANITY_ADAPTIVE_CONF_MIN) and bbox_area_ratio >= float(SANITY_ADAPTIVE_AREA_MIN):
            ar_min = float(SANITY_ASPECT_MIN_ADAPTIVE)
            rule = "adaptive_high_conf"

    metrics["aspect_min"] = float(ar_min)
    metrics["rule"] = rule

    if ar < ar_min:
        return False, f"bad_aspect_low:{ar:.3f}<{ar_min:.2f};rule={rule}", metrics
    if ar > 8.0:
        return False, f"bad_aspect_high:{ar:.3f}>8.0", metrics

    return True, "ok", metrics


def maybe_upscale(img: np.ndarray, min_w: int, min_h: int, enable: bool) -> np.ndarray:
    if not enable:
        return img
    try:
        hh, ww = img.shape[:2]
        if ww <= 0 or hh <= 0:
            return img
        if ww >= int(min_w) and hh >= int(min_h):
            return img
        scale = max(float(min_w) / float(max(1, ww)), float(min_h) / float(max(1, hh)))
        if scale <= 1.0:
            return img
        return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    except Exception:
        return img


def downscale_bgr(img: np.ndarray, target_w: int) -> np.ndarray:
    """Быстрое downscale для авто-метрик (чтобы не жечь CPU на 1080p)."""
    try:
        hh, ww = img.shape[:2]
        tw = int(target_w)
        if tw <= 0 or ww <= 0 or hh <= 0 or ww <= tw:
            return img
        scale = float(tw) / float(ww)
        th = max(1, int(round(float(hh) * scale)))
        return cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
    except Exception:
        return img


def deskew_roll(img: np.ndarray, max_angle_deg: float, min_angle_deg: float) -> Tuple[np.ndarray, float]:
    """
    Лёгкая коррекция горизонта номера (roll).
    Возвращает (img_out, angle_deg_applied). angle>0 значит повернули по часовой.
    """
    try:
        hh, ww = img.shape[:2]
        if ww < 30 or hh < 15:
            return img, 0.0

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(gray, 40, 140)

        min_len = max(20, int(0.55 * ww))
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180.0,
            threshold=50,
            minLineLength=min_len,
            maxLineGap=10,
        )
        if lines is None or len(lines) == 0:
            return img, 0.0

        angles = []
        weights = []
        for ln in lines:
            x1, y1, x2, y2 = ln[0]
            dx = float(x2 - x1)
            dy = float(y2 - y1)
            if abs(dx) < 1.0:
                continue
            ang = float(np.degrees(np.arctan2(dy, dx)))
            if ang < -45.0 or ang > 45.0:
                continue
            length = float(np.hypot(dx, dy))
            angles.append(ang)
            weights.append(length)

        if not angles:
            return img, 0.0

        order = np.argsort(np.array(angles))
        angs = np.array(angles)[order]
        wts = np.array(weights)[order]
        cum = np.cumsum(wts)
        mid = cum[-1] * 0.5
        idx = int(np.searchsorted(cum, mid))
        angle = float(angs[min(idx, len(angs) - 1)])

        if abs(angle) < float(min_angle_deg) or abs(angle) > float(max_angle_deg):
            return img, 0.0

        rot = -angle
        M = cv2.getRotationMatrix2D((ww / 2.0, hh / 2.0), rot, 1.0)
        out = cv2.warpAffine(img, M, (ww, hh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return out, float(-rot)
    except Exception:
        return img, 0.0


def main() -> None:
    print(f"[rtsp_worker] INFER_URL={INFER_URL}")
    print(f"[rtsp_worker] SETTINGS_BASE_URL={SETTINGS_BASE_URL} SETTINGS_POLL_SEC={SETTINGS_POLL_SEC}")
    print(f"[rtsp_worker] RTSP_URL_DEFAULT={RTSP_URL_DEFAULT!r} (used only if settings empty)")
    print(f"[rtsp_worker] READ_FPS={READ_FPS} DET_FPS={DET_FPS} SEND_FPS={SEND_FPS}")
    print(f"[rtsp_worker] CAPTURE_BACKEND={CAPTURE_BACKEND}")
    print(f"[rtsp_worker] AUTO_MODE={int(AUTO_MODE)} AUTO_PREPROC_ENABLE={int(AUTO_PREPROC_ENABLE)} "
          f"AUTO_METRICS_SOURCE={AUTO_METRICS_SOURCE}")

    ensure_dir(SAVE_DIR)
    ensure_dir(LIVE_DIR)

    detector = PlateDetector(DET_MODEL_PATH, conf=DET_CONF, iou_thr=DET_IOU, imgsz=DET_IMG_SIZE)

    # =========================================================
    # AUTO config/state (FIXED to match plate_auto.py)
    # =========================================================
    auto_cfg = AutoConfig(
        enable=bool(AUTO_MODE),
        every_n=max(1, int(AUTO_EVERY_N)),
        hyst_sec=float(AUTO_HYST_SEC),
        luma_day=float(AUTO_LUMA_DAY),
        luma_night=float(AUTO_LUMA_NIGHT),
        sat_glare=float(AUTO_SAT_GLARE),
        blur_min=float(AUTO_BLUR_MIN),
        drop_on_blur=bool(AUTO_DROP_ON_BLUR),
        drop_on_glare=bool(AUTO_DROP_ON_GLARE),
        allow_rectify=bool(AUTO_RECTIFY),
        allow_pad=bool(AUTO_PAD_ENABLE),
        allow_upscale=bool(AUTO_UPSCALE_ENABLE),
        upscale_day=(int(AUTO_UPSCALE_DAY_W), int(AUTO_UPSCALE_DAY_H)),
        upscale_night=(int(AUTO_UPSCALE_NIGHT_W), int(AUTO_UPSCALE_NIGHT_H)),
        pad_base=float(PLATE_PAD_BASE),
        pad_small=float(PLATE_PAD_SMALL),
        pad_small_w=int(PLATE_PAD_SMALL_W),
        pad_small_h=int(PLATE_PAD_SMALL_H),
        pad_max=float(PLATE_PAD_MAX),
    )
    auto_state = AutoState()
    last_auto: Optional[AutoDecision] = None

    current_rtsp_url = RTSP_URL_DEFAULT
    current_enabled = True

    s_rtsp, s_en = fetch_camera_settings(SETTINGS_BASE_URL)
    if s_rtsp:
        current_rtsp_url = s_rtsp
    if s_en is not None:
        current_enabled = bool(s_en)

    if not current_rtsp_url:
        raise SystemExit("RTSP_URL is empty (both env and settings)")

    print(f"[rtsp_worker] camera.enabled={int(current_enabled)} camera.rtsp_url={current_rtsp_url}")

    grabber: Optional[AutoGrabber] = None

    def start_grabber(url: str) -> AutoGrabber:
        g = AutoGrabber(
            rtsp_url=url,
            read_fps=READ_FPS,
            capture_backend=CAPTURE_BACKEND,
            rtsp_transport=RTSP_TRANSPORT,
            rtsp_open_timeout_ms=RTSP_OPEN_TIMEOUT_MS,
            rtsp_read_timeout_ms=RTSP_READ_TIMEOUT_MS,
            ffmpeg_probe=bool(FFMPEG_PROBE),
            ffmpeg_threads=int(FFMPEG_THREADS),
            ffmpeg_read_timeout_sec=float(FFMPEG_READ_TIMEOUT_SEC),
            auto_switch_check_sec=float(AUTO_SWITCH_CHECK_SEC),
            auto_switch_age_ms=int(AUTO_SWITCH_AGE_MS),
            auto_switch_streak=int(AUTO_SWITCH_STREAK),
            auto_switch_cooldown_sec=float(AUTO_SWITCH_COOLDOWN_SEC),
            freeze_enable=bool(FREEZE_ENABLE),
            freeze_every_n=int(FREEZE_EVERY_N),
            freeze_diff_mean_thr=float(FREEZE_DIFF_MEAN_THR),
            freeze_max_sec=float(FREEZE_MAX_SEC),
            rtsp_drain_grabs=int(RTSP_DRAIN_GRABS),
        )
        g.start()
        return g

    def stop_grabber(g: Optional[AutoGrabber]) -> None:
        try:
            if g is not None:
                g.stop()
        except Exception:
            pass

    if current_enabled:
        grabber = start_grabber(current_rtsp_url)

    # wait first frame
    frame0 = None
    frame0_ts = 0.0
    if grabber is not None:
        t_wait0 = time.time()
        while frame0 is None:
            frame0, frame0_ts = grabber.get()
            if frame0 is None:
                if time.time() - t_wait0 > 12.0:
                    raise SystemExit("cannot read first frame from RTSP (timeout)")
                time.sleep(0.05)

    w = 0
    h = 0
    roi = (0, 0, 0, 0)
    last_roi_str = str(ROI_STR or "")
    last_roi_poly_str = str(ROI_POLY_STR or "")
    if frame0 is not None:
        h, w = frame0.shape[:2]
        roi = parse_roi(last_roi_str, w, h)  # <- ROI from runtime settings/env
        roi_poly = parse_roi_poly_str(last_roi_poly_str, w, h)
        print(f"[rtsp_worker] first frame={w}x{h} ROI={roi} ROI_POLY_PTS={len(roi_poly)}")

    track = TrackState(track_id=0, last_seen_ts=0.0, box=None)
    events = PlateEventState(
        plate_confirm_window_sec=PLATE_CONFIRM_WINDOW_SEC,
        plate_resend_sec=PLATE_RESEND_SEC,
        global_send_min_interval_sec=GLOBAL_SEND_MIN_INTERVAL_SEC,
        plate_confirm_k=PLATE_CONFIRM_K,
    )
    print(f"[rtsp_worker] state_init tracker_obj={id(track)} events_obj={id(events)}")

    det_interval = 1.0 / max(0.1, float(DET_FPS))
    send_interval = 1.0 / max(0.1, float(SEND_FPS))

    next_det_ts = 0.0
    next_send_ts = 0.0

    last_dets_roi: List[DetBox] = []
    last_det_frame_ts: float = 0.0
    last_det_ms: float = 0.0
    last_post_ms: float = 0.0

    t0_stats = time.time()
    det_count = 0
    send_count = 0

    hb_last = 0.0
    hb_window_t0 = time.time()
    hb_frames = 0
    read_errors = 0

    last_live_write = 0.0
    last_log = 0.0
    tick = 0
    sent = 0

    next_settings_poll = 0.0
    last_frame_ts_seen = -1.0

    # meta/debug
    last_pad_used: float = float(PLATE_PAD_BASE)
    last_pad_reason: str = "init"
    last_bbox_wh: Tuple[int, int] = (0, 0)
    auto_profile: Optional[str] = None
    auto_metrics: Dict[str, float] = {}

    runtime_overrides_last: dict = {}
    last_unsane_dump_ts = 0.0
    last_decision_log_ts = 0.0
    last_state_log_ts = 0.0
    last_cand_dbg_ts_mono = 0.0
    last_filter_thr_log_ts_mono = 0.0
    last_sanity_summary_ts_mono = 0.0
    best_missing_with_det = 0
    sanity_summary = {"ok": 0, "too_small": 0, "no_candidate_crop": 0, "rejected_unsane": 0, "other": 0}
    best_crop_buf: List[Dict[str, object]] = []
    roi_poly: List[Tuple[int, int]] = parse_roi_poly_str(str(ROI_POLY_STR or ""), max(1, w), max(1, h)) if (w > 0 and h > 0) else []

    while True:
        now = time.time()

        # settings poll
        if SETTINGS_POLL_SEC > 0 and now >= next_settings_poll:
            next_settings_poll = now + float(SETTINGS_POLL_SEC)

            s_rtsp, s_en = fetch_camera_settings(SETTINGS_BASE_URL)

            # NEW: also poll full settings.json to receive rtsp_worker overrides from UI
            flags = {"detector_rebuild": False, "grabber_restart": False}
            try:
                s_all = fetch_settings_json(SETTINGS_BASE_URL, timeout_sec=1.5)
                if isinstance(s_all, dict):
                    # ожидаем структуру:
                    # settings.rtsp_worker.overrides = {"DET_CONF": 0.35, "READ_FPS": 15, ...}
                    
                    overrides = _get_settings_overrides(s_all)
                    runtime_overrides_last, flags = _apply_runtime_overrides(overrides, runtime_overrides_last)
            except Exception:
                # settings poll не должен ломать основной цикл
                pass
            new_rtsp = current_rtsp_url
            new_enabled = current_enabled
            if s_rtsp:
                new_rtsp = s_rtsp
            if s_en is not None:
                new_enabled = bool(s_en)

            if new_enabled != current_enabled:
                current_enabled = new_enabled
                print(f"[rtsp_worker] CHG: camera.enabled -> {int(current_enabled)}")
                if not current_enabled:
                    stop_grabber(grabber)
                    grabber = None
                    track.box = None
                    last_frame_ts_seen = -1.0
                else:
                    if not new_rtsp:
                        new_rtsp = RTSP_URL_DEFAULT
                    if not new_rtsp:
                        print("[rtsp_worker] WARN: enabled=1 but rtsp_url empty -> keep disabled until url appears")
                        current_enabled = False
                    else:
                        current_rtsp_url = new_rtsp
                        grabber = start_grabber(current_rtsp_url)
                        last_frame_ts_seen = -1.0

            if current_enabled and new_rtsp and new_rtsp != current_rtsp_url:
                print(f"[rtsp_worker] CHG: camera.rtsp_url -> {new_rtsp}")
                current_rtsp_url = new_rtsp
                stop_grabber(grabber)
                grabber = start_grabber(current_rtsp_url)
                track.box = None
                last_frame_ts_seen = -1.0

            # if UI changed parameters that require restart/rebuild
            if current_enabled and grabber is not None and flags.get("grabber_restart"):
                print("[rtsp_worker] CHG: runtime overrides -> restarting grabber")
                stop_grabber(grabber)
                grabber = start_grabber(current_rtsp_url)
                track.box = None
                last_frame_ts_seen = -1.0

            if flags.get("detector_rebuild"):
                try:
                    print("[rtsp_worker] CHG: runtime overrides -> rebuilding detector")
                    detector = PlateDetector(DET_MODEL_PATH, conf=DET_CONF, iou_thr=DET_IOU, imgsz=DET_IMG_SIZE)
                except Exception as e:
                    print(f"[rtsp_worker] WARN: detector rebuild failed: {e}")

            # ROI can be changed from settings at runtime without restart
            cur_roi_str = str(globals().get("ROI_STR", "") or "")
            if cur_roi_str != last_roi_str:
                last_roi_str = cur_roi_str
                if w > 0 and h > 0:
                    roi = parse_roi(last_roi_str, w, h)
                print(f"[rtsp_worker] CHG: ROI_STR -> {last_roi_str!r} ROI={roi}")

            cur_roi_poly_str = str(globals().get("ROI_POLY_STR", "") or "")
            if cur_roi_poly_str != last_roi_poly_str:
                last_roi_poly_str = cur_roi_poly_str
                if w > 0 and h > 0:
                    roi_poly = parse_roi_poly_str(last_roi_poly_str, w, h)
                print(f"[rtsp_worker] CHG: ROI_POLY_STR -> pts={len(roi_poly)}")


        # disabled -> heartbeat only
        if not current_enabled or grabber is None:
            if HB_EVERY_SEC > 0 and (now - hb_last) >= HB_EVERY_SEC:
                _post_heartbeat(
                    HEARTBEAT_URL,
                    {
                        "ts": now,
                        "alive": True,
                        "disabled": True,
                        "frozen": False,
                        "note": "camera_disabled",
                        "camera_id": CAMERA_ID,
                        "fps": 0.0,
                        "errors": int(read_errors),
                        "sent": int(sent),
                        "frame_w": int(w),
                        "frame_h": int(h),
                        "roi": list(roi) if roi else None,
                        "backend": "none",
                        "grab_age_ms": None,
                        "read_fps_eff": 0.0,
                        "det_fps_eff": 0.0,
                        "send_fps_eff": 0.0,
                        "last_det_ms": round(float(last_det_ms), 2),
                        "last_post_ms": round(float(last_post_ms), 2),
                    },
                    timeout_sec=1.0,
                )
                hb_last = now
            time.sleep(0.15)
            continue

        # read frame
        frame, frame_ts = grabber.get()
        if frame is None or frame_ts <= 0:
            read_errors += 1
            time.sleep(0.03)
            continue

        if float(frame_ts) == float(last_frame_ts_seen):
            time.sleep(0.004)
            continue
        last_frame_ts_seen = float(frame_ts)

        hb_frames += 1

        grab_age_ms = (now - float(frame_ts)) * 1000.0
        frozen_now = bool(grab_age_ms >= (FREEZE_MAX_SEC * 1000.0))
        note = "ok" if not frozen_now else "stale_frame"

        fh, fw = frame.shape[:2]
        if (fw, fh) != (w, h) or w == 0 or h == 0:
            w, h = fw, fh
            roi = parse_roi(last_roi_str, w, h)
            roi_poly = parse_roi_poly_str(str(globals().get("ROI_POLY_STR", "") or ""), w, h)
            print(f"[rtsp_worker] stream size => frame={w}x{h} ROI={roi} ROI_POLY_PTS={len(roi_poly)}")

        x1, y1, x2, y2 = roi
        roi_frame = frame[y1:y2, x1:x2]
        if roi_frame.size == 0:
            time.sleep(0.01)
            continue

        # detect
        dets_roi: List[DetBox] = []
        if (now >= next_det_ts) or (track.box is None):
            next_det_ts = now + det_interval
            td0 = time.time()
            dets_roi = detector.detect(roi_frame)
            last_det_ms = (time.time() - td0) * 1000.0
            last_dets_roi = dets_roi
            last_det_frame_ts = float(frame_ts)
            det_count += 1
        else:
            if last_det_frame_ts > 0 and (float(frame_ts) - last_det_frame_ts) <= max(0.1, TRACK_HOLD_SEC * 2.0):
                dets_roi = last_dets_roi
            else:
                dets_roi = []

        det_cnt = len(dets_roi)

        cand_det_total = int(det_cnt)
        cand_after_filters = 0
        cand_filtered_roi = 0
        cand_filtered_poly = 0
        cand_filtered_min_wh = 0
        cand_filtered_area = 0
        cand_filtered_aspect = 0
        cand_filtered_track = 0
        cand_filtered_other = 0
        cand_best_selected = 0
        cand_sample_reason = ""
        cand_sample_idx = -1

        best_roi: Optional[DetBox] = None
        if dets_roi:
            cand = dets_roi[0]
            if cand.w() >= MIN_PLATE_W and cand.h() >= MIN_PLATE_H:
                best_roi = cand
                cand_after_filters = 1
            else:
                cand_filtered_min_wh += 1
                cand_sample_reason = "min_wh"
                cand_sample_idx = 0

        # tracking
        track_new = False
        best_full: Optional[DetBox] = None

        if best_roi is not None:
            cur_full = DetBox(
                x1=best_roi.x1 + x1,
                y1=best_roi.y1 + y1,
                x2=best_roi.x2 + x1,
                y2=best_roi.y2 + y1,
                conf=best_roi.conf,
            )
            if TRACK_ENABLE and track.box is not None and (now - track.last_seen_ts) <= TRACK_HOLD_SEC:
                if iou(track.box, cur_full) >= TRACK_IOU_MIN:
                    track.box = smooth_box(track.box, cur_full, TRACK_ALPHA)
                    track.last_seen_ts = now
                    best_full = track.box
                else:
                    cand_filtered_track += 1
                    if not cand_sample_reason:
                        cand_sample_reason = "track_iou"
                    track.track_id += 1
                    track_new = True
                    track.box = cur_full
                    track.last_seen_ts = now
                    best_full = track.box
            else:
                track.track_id += 1
                track_new = True
                track.box = cur_full
                track.last_seen_ts = now
                best_full = track.box
        else:
            if track.box is not None and (now - track.last_seen_ts) > TRACK_HOLD_SEC:
                track.box = None

        if best_full is None and track.box is not None and (now - track.last_seen_ts) <= TRACK_HOLD_SEC:
            best_full = track.box

        # polygon ROI gate (optional): detection center must be inside polygon
        if best_full is not None and roi_poly:
            cx = 0.5 * (float(best_full.x1) + float(best_full.x2))
            cy = 0.5 * (float(best_full.y1) + float(best_full.y2))
            inside_pts = 0
            for px, py in roi_poly:
                if int(best_full.x1) <= int(px) <= int(best_full.x2) and int(best_full.y1) <= int(py) <= int(best_full.y2):
                    inside_pts += 1
            if not point_in_polygon(cx, cy, roi_poly):
                cand_filtered_poly += 1
                if not cand_sample_reason:
                    cand_sample_reason = "poly"
                best_full = None
            if CANDIDATE_DEBUG_ENABLE and CANDIDATE_DEBUG_COORDS and cand_det_total > 0:
                print(f"[rtsp_worker] cand_poly method=center_in_polygon pts_inside_bbox={inside_pts} poly_pts={len(roi_poly)}")

        if best_full is not None and (best_full.x2 <= x1 or best_full.x1 >= x2 or best_full.y2 <= y1 or best_full.y1 >= y2):
            cand_filtered_roi += 1
            if not cand_sample_reason:
                cand_sample_reason = "roi_mismatch_warn"
            if CANDIDATE_DEBUG_ENABLE:
                print(f"[rtsp_worker] WARN cand_roi_mismatch bbox_full=({best_full.x1},{best_full.y1},{best_full.x2},{best_full.y2}) roi=({x1},{y1},{x2},{y2})")

        if CANDIDATE_DEBUG_ENABLE and CANDIDATE_DEBUG_COORDS and best_full is not None:
            bwf = int(best_full.x2 - best_full.x1)
            bhf = int(best_full.y2 - best_full.y1)
            broi_x1 = int(best_full.x1 - x1)
            broi_y1 = int(best_full.y1 - y1)
            broi_x2 = int(best_full.x2 - x1)
            broi_y2 = int(best_full.y2 - y1)
            roi_w = max(1, int(x2 - x1))
            roi_h = max(1, int(y2 - y1))
            if broi_x1 < -2 or broi_y1 < -2 or broi_x2 > (roi_w + 2) or broi_y2 > (roi_h + 2):
                print(f"[rtsp_worker] WARN cand_coords_mismatch bbox_full=({best_full.x1},{best_full.y1},{best_full.x2},{best_full.y2}) bbox_roi=({broi_x1},{broi_y1},{broi_x2},{broi_y2}) roi_wh={roi_w}x{roi_h}")
            print(f"[rtsp_worker] cand_coords detector_space=roi bbox_full=({best_full.x1},{best_full.y1},{best_full.x2},{best_full.y2}) bbox_roi=({broi_x1},{broi_y1},{broi_x2},{broi_y2}) wh_full={bwf}x{bhf}")

        # live preview
        if LIVE_EVERY_SEC > 0 and (now - last_live_write) >= LIVE_EVERY_SEC:
            items = []
            for d in dets_roi:
                items.append(
                    {"x1": int(d.x1 + x1), "y1": int(d.y1 + y1), "x2": int(d.x2 + x1), "y2": int(d.y2 + y1), "conf": float(d.conf)}
                )

            best_xyxy = None
            if best_full is not None:
                best_xyxy = (int(best_full.x1), int(best_full.y1), int(best_full.x2), int(best_full.y2))

            write_live_preview(
                live_dir=LIVE_DIR,
                frame_bgr=frame,
                items=items,
                roi_xyxy=(x1, y1, x2, y2),
                camera_id=CAMERA_ID,
                ts=now,
                frame_w=int(w),
                frame_h=int(h),
                live_jpeg_quality=int(LIVE_JPEG_QUALITY),
                live_save_quad=bool(LIVE_SAVE_QUAD),
                rectify_enable=bool(RECTIFY),
                rectify_w=int(RECTIFY_W),
                rectify_h=int(RECTIFY_H),
                best_full_xyxy=best_xyxy,
                plate_pad_used=float(last_pad_used),
            )
            last_live_write = now

        # choose crop
        crop_to_send: Optional[np.ndarray] = None
        crop_dbg: Optional[np.ndarray] = None
        rect_dbg: Optional[np.ndarray] = None
        rectify_ms: Optional[float] = None

        pre_variant = "none"
        pre_warped = False
        sanity_fail_reason = "not_applicable"
        sanity_metrics: Dict[str, float | str] = {}

        pad_used_tick = float(PLATE_PAD_BASE)
        pad_reason_tick = "n/a"
        bbox_wh_tick = (0, 0)

        # per-tick controls
        rect_enable_tick = bool(RECTIFY)
        upscale_enable_tick = bool(UPSCALE_ENABLE)
        upscale_min_w_tick = int(UPSCALE_MIN_W)
        upscale_min_h_tick = int(UPSCALE_MIN_H)

        auto_profile = None
        auto_metrics = {}

        deskew_ms: Optional[float] = None
        deskew_deg: float = 0.0

        if best_full is not None:
            bw = int(best_full.x2 - best_full.x1)
            bh = int(best_full.y2 - best_full.y1)
            bbox_wh_tick = (bw, bh)

            # crop for metrics (exact bbox, no pad)
            bx1, by1, bx2, by2 = expand_box(best_full.x1, best_full.y1, best_full.x2, best_full.y2, 0.0, w, h)
            crop_metrics = frame[by1:by2, bx1:bx2]
            if crop_metrics.size == 0:
                crop_metrics = None

            # =========================================================
            # AUTO decision (FIX): metrics source roi|crop
            # =========================================================
            if auto_cfg.enable:
                auto_img = None
                if AUTO_METRICS_SOURCE == "crop":
                    if crop_metrics is not None:
                        auto_img = crop_metrics
                else:
                    auto_img = roi_frame  # default: roi

                if auto_img is not None and auto_img.size > 0:
                    auto_img = downscale_bgr(auto_img, int(AUTO_METRICS_DOWNSCALE_W))
                    dec = decide_auto(now_ts=now, img_bgr=auto_img, cfg=auto_cfg, st=auto_state, bbox_w=bw, bbox_h=bh)
                    if dec is not None:
                        last_auto = dec

                if last_auto is not None:
                    auto_profile = last_auto.profile
                    try:
                        auto_metrics = {k: float(v) for k, v in (last_auto.metrics.__dict__ or {}).items()}
                    except Exception:
                        auto_metrics = {}

                    # pad
                    if last_auto.pad_used is not None:
                        pad_used_tick = float(last_auto.pad_used)
                        pad_reason_tick = "auto"
                    else:
                        pad_used_tick, pad_reason_tick = choose_plate_pad(bw, bh)

                    # rectify
                    if last_auto.rectify_on is not None:
                        rect_enable_tick = bool(last_auto.rectify_on)

                    # upscale
                    if last_auto.upscale_min is not None:
                        try:
                            uw, uh = last_auto.upscale_min
                            upscale_min_w_tick = int(uw)
                            upscale_min_h_tick = int(uh)
                            upscale_enable_tick = True
                        except Exception:
                            pass
            else:
                pad_used_tick, pad_reason_tick = choose_plate_pad(bw, bh)

            # store meta
            last_pad_used = float(pad_used_tick)
            last_pad_reason = str(pad_reason_tick)
            last_bbox_wh = bbox_wh_tick

            ex1, ey1, ex2, ey2 = expand_box(best_full.x1, best_full.y1, best_full.x2, best_full.y2, pad_used_tick, w, h)
            if PLATE_PAD_RIGHT_EXTRA > 0:
                extra_right = int(round(float(best_full.x2 - best_full.x1) * float(PLATE_PAD_RIGHT_EXTRA)))
                if extra_right > 0:
                    ex2 = min(int(w), int(ex2 + extra_right))
            crop = frame[ey1:ey2, ex1:ex2]
            if crop.size > 0:
                crop_dbg = crop

                if rect_enable_tick:
                    t_rect0 = time.time()
                    rect = rectify_plate(crop, RECTIFY_W, RECTIFY_H)
                    rectify_ms = (time.time() - t_rect0) * 1000.0
                    if rect is not None and rect.size > 0:
                        rect_dbg = rect
                        crop_to_send = rect
                        pre_variant = "rectify"
                        pre_warped = True
                    else:
                        crop_to_send = crop
                        pre_variant = "crop"
                        pre_warped = False
                else:
                    crop_to_send = crop
                    pre_variant = "crop"
                    pre_warped = False

        if crop_to_send is None:
            if OCR_CROP_MODE == "roi_fallback":
                crop_to_send = roi_frame
                pre_variant = "roi_fallback"
                pre_warped = False
            elif OCR_CROP_MODE == "yolo" and SEND_ON_NO_DET:
                crop_to_send = roi_frame
                pre_variant = "roi_send_on_no_det"
                pre_warped = False

        if crop_to_send is not None and crop_to_send.size > 0:
            sanity_ok_tick, sanity_fail_reason, sanity_metrics = sanity_check_crop(
                crop_to_send,
                det_conf=(best_full.conf if best_full is not None else None),
                bbox_wh=bbox_wh_tick,
                frame_wh=(w, h),
            )
            if not sanity_ok_tick:
                rejected_crop = crop_to_send
                crop_to_send = None
                pre_variant = "rejected_unsane"
                pre_warped = False
                if str(sanity_fail_reason).startswith("too_small"):
                    cand_filtered_min_wh += 1
                elif str(sanity_fail_reason).startswith("aspect"):
                    cand_filtered_aspect += 1
                elif str(sanity_fail_reason).startswith("ar"):
                    cand_filtered_aspect += 1
                elif str(sanity_fail_reason).startswith("too_narrow"):
                    cand_filtered_aspect += 1
                elif str(sanity_fail_reason).startswith("too_low"):
                    cand_filtered_area += 1
                else:
                    cand_filtered_other += 1

                if (now - float(last_unsane_dump_ts)) >= float(SANITY_DEBUG_REJECT_EVERY_SEC):
                    try:
                        stamp = int(now * 1000)
                        vis = frame.copy()
                        if best_full is not None:
                            cv2.rectangle(vis, (best_full.x1, best_full.y1), (best_full.x2, best_full.y2), (0, 140, 255), 2)
                        txt = f"{sanity_fail_reason} conf={float(best_full.conf) if best_full is not None else -1:.2f}"
                        cv2.putText(vis, txt[:180], (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 140, 255), 2, cv2.LINE_AA)
                        cv2.imwrite(os.path.join(SAVE_DIR, f"unsane_frame_vis_{stamp}.jpg"), vis)
                        if rejected_crop is not None and rejected_crop.size > 0:
                            cv2.imwrite(os.path.join(SAVE_DIR, f"unsane_crop_{stamp}.jpg"), rejected_crop)
                        last_unsane_dump_ts = now
                    except Exception:
                        pass
        else:
            sanity_fail_reason = "no_candidate_crop"

        cand_after_filters = int(best_full is not None)
        cand_best_selected = int(crop_to_send is not None and crop_to_send.size > 0)
        if cand_det_total > 0 and cand_best_selected == 0:
            best_missing_with_det += 1

        if cand_best_selected:
            sanity_summary["ok"] += 1
        else:
            if str(sanity_fail_reason).startswith("too_small"):
                sanity_summary["too_small"] += 1
            elif str(sanity_fail_reason) == "no_candidate_crop":
                sanity_summary["no_candidate_crop"] += 1
            elif str(pre_variant) == "rejected_unsane":
                sanity_summary["rejected_unsane"] += 1
            else:
                sanity_summary["other"] += 1

        if CANDIDATE_DEBUG_ENABLE and cand_det_total > 0:
            tmono = time.monotonic()
            dbg_every = max(0.5, float(CANDIDATE_DEBUG_EVERY_SEC))
            if (tmono - float(last_cand_dbg_ts_mono)) >= dbg_every:
                if cand_det_total > 0:
                    top = sorted(dets_roi, key=lambda d: float(d.conf), reverse=True)[:3]
                    snap_parts = []
                    for d in top:
                        dw, dh = max(1, d.w()), max(1, d.h())
                        ar = float(dw) / float(max(1, dh))
                        area = float(dw * dh) / float(max(1, roi_frame.shape[0] * roi_frame.shape[1]))
                        snap_parts.append(f"c={float(d.conf):.2f} wh={dw}x{dh} ar={ar:.2f} area={area:.4f}")
                    print(f"[rtsp_worker] det_snapshot top3={' | '.join(snap_parts) if snap_parts else '-'}")

                print(
                    f"[rtsp_worker] cand_dbg det_total={cand_det_total} after={cand_after_filters} "
                    f"roi={cand_filtered_roi} poly={cand_filtered_poly} min_wh={cand_filtered_min_wh} "
                    f"area={cand_filtered_area} aspect={cand_filtered_aspect} track={cand_filtered_track} other={cand_filtered_other} "
                    f"best={cand_best_selected} sanity={sanity_fail_reason} roi=({x1},{y1},{x2},{y2}) roi_poly_pts={len(roi_poly)}"
                )

                if CANDIDATE_DEBUG_SAMPLE and cand_det_total > 0 and cand_after_filters == 0 and dets_roi:
                    src = sorted(dets_roi, key=lambda d: float(d.conf), reverse=True)[0]
                    print(
                        f"[rtsp_worker] cand_sample reason={cand_sample_reason or 'unknown'} "
                        f"bbox_roi=({src.x1},{src.y1},{src.w()},{src.h()}) conf={float(src.conf):.3f}"
                    )

                if CANDIDATE_DEBUG_SAVE and cand_det_total > 0 and cand_after_filters == 0:
                    try:
                        stamp = int(now * 1000)
                        vis = frame.copy()
                        for d in dets_roi:
                            fx1, fy1, fx2, fy2 = int(d.x1 + x1), int(d.y1 + y1), int(d.x2 + x1), int(d.y2 + y1)
                            cv2.rectangle(vis, (fx1, fy1), (fx2, fy2), (0, 255, 255), 2)
                            cv2.putText(vis, f"{float(d.conf):.2f}", (fx1, max(14, fy1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
                        cv2.putText(vis, f"cand_after=0 reason={cand_sample_reason or 'unknown'}", (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 140, 255), 2, cv2.LINE_AA)
                        cv2.imwrite(os.path.join(SAVE_DIR, f"cand_dbg_{stamp}.jpg"), vis)
                    except Exception:
                        pass

                last_cand_dbg_ts_mono = tmono

            if (tmono - float(last_filter_thr_log_ts_mono)) >= 10.0:
                print(
                    f"[rtsp_worker] filter_thresholds min_wh={int(MIN_PLATE_W)}x{int(MIN_PLATE_H)} "
                    f"area_min={float(SANITY_ADAPTIVE_AREA_MIN):.4f} aspect_min={float(SANITY_ASPECT_MIN_BASE):.2f}/{float(SANITY_ASPECT_MIN_ADAPTIVE):.2f} "
                    f"roi_rect=1 roi_poly={int(bool(roi_poly))} poly_method=center_in_polygon"
                )
                print(f"[rtsp_worker] cand_dbg best_missing_with_det_10s={int(best_missing_with_det)}")
                best_missing_with_det = 0
                last_filter_thr_log_ts_mono = tmono

            if (tmono - float(last_sanity_summary_ts_mono)) >= 5.0:
                print(
                    f"[rtsp_worker] sanity_summary_5s ok={int(sanity_summary['ok'])} too_small={int(sanity_summary['too_small'])} "
                    f"no_candidate_crop={int(sanity_summary['no_candidate_crop'])} rejected_unsane={int(sanity_summary['rejected_unsane'])} other={int(sanity_summary['other'])}"
                )
                sanity_summary = {"ok": 0, "too_small": 0, "no_candidate_crop": 0, "rejected_unsane": 0, "other": 0}
                last_sanity_summary_ts_mono = tmono

        # APPLY PREPROC
        if crop_to_send is not None and crop_to_send.size > 0:
            if bool(AUTO_PREPROC_ENABLE) and auto_cfg.enable and auto_profile:
                crop_to_send = apply_profile(auto_profile, crop_to_send)

        # UPSCALE
        if crop_to_send is not None and crop_to_send.size > 0:
            allow_upscale = bool(upscale_enable_tick)
            crop_to_send = maybe_upscale(
                crop_to_send,
                min_w=int(upscale_min_w_tick),
                min_h=int(upscale_min_h_tick),
                enable=bool(allow_upscale),
            )

        # NEW: DESKEW
        if crop_to_send is not None and crop_to_send.size > 0 and DESKEW_ENABLE:
            t_ds0 = time.time()
            crop_to_send, deskew_deg = deskew_roll(
                crop_to_send,
                max_angle_deg=float(DESKEW_MAX_ANGLE_DEG),
                min_angle_deg=float(DESKEW_MIN_ANGLE_DEG),
            )
            deskew_ms = (time.time() - t_ds0) * 1000.0

        # SEND decision
        want_send = False
        send_reason = "no_crop"
        if crop_to_send is not None and crop_to_send.size > 0:
            if EVENT_MODE == "always":
                want_send = True
                send_reason = "event_mode_always"
            elif EVENT_MODE == "on_new_track":
                want_send = bool(track_new)
                send_reason = "new_track" if want_send else "same_track"
            elif EVENT_MODE in ("on_plate_change", "on_plate_confirmed"):
                # Для plate-based режимов опираемся на plate-state, а не на track_new.
                # Иначе при нестабильном tracker можно спамить "first_plate/track_new" на каждом кадре.
                last = events.last_seen_plate
                if not last:
                    want_send = True
                    send_reason = "first_plate"
                else:
                    want_send = bool(events.can_send_plate(now, last))
                    send_reason = "plate_resend_ready" if want_send else "plate_resend_cooldown"

        if want_send and not events.can_send_global(now):
            want_send = False
            send_reason = "global_throttle"
        if want_send and now < next_send_ts:
            want_send = False
            send_reason = "send_fps_throttle"

        best_crop_score = 0.0
        if crop_to_send is not None and crop_to_send.size > 0 and BEST_CROP_ENABLE:
            try:
                area_ratio = float((crop_to_send.shape[0] * crop_to_send.shape[1]) / max(1.0, float(w * h)))
                det_conf = float(best_full.conf) if best_full is not None else 0.0
                sharp = _sharpness_score(crop_to_send)
                best_crop_score = float(det_conf * area_ratio * max(1.0, min(1000.0, sharp)))
                best_crop_buf.append({
                    "ts": float(now),
                    "crop": crop_to_send.copy(),
                    "score": best_crop_score,
                    "pre_variant": str(pre_variant),
                    "pre_warped": bool(pre_warped),
                })
            except Exception:
                pass

            win = max(0.3, float(BEST_CROP_WINDOW_SEC))
            best_crop_buf = [x for x in best_crop_buf if (now - float(x.get("ts", now))) <= win]

            if want_send and best_crop_buf:
                best_crop_buf.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
                chosen = best_crop_buf[: max(1, int(BEST_CROP_MAX_SEND))]
                pick = chosen[0]
                crop_to_send = pick.get("crop") if isinstance(pick.get("crop"), np.ndarray) else crop_to_send
                pre_variant = str(pick.get("pre_variant") or pre_variant)
                pre_warped = bool(pick.get("pre_warped"))
                send_reason = f"best_crop(score={float(pick.get('score', 0.0)):.4f})"
                best_crop_buf = []

        resp = None
        jpeg_bytes_sent: Optional[bytes] = None

        if want_send:
            next_send_ts = now + send_interval
            try:
                tp0 = time.time()
                pre_timing = {}
                if rectify_ms is not None:
                    pre_timing["rectify_ms"] = round(float(rectify_ms), 2)
                if deskew_ms is not None:
                    pre_timing["deskew_ms"] = round(float(deskew_ms), 2)
                    pre_timing["deskew_deg"] = round(float(deskew_deg), 2)

                resp, jpeg_bytes_sent = post_crop(
                    INFER_URL,
                    crop_to_send,
                    timeout_sec=HTTP_TIMEOUT_SEC,
                    jpeg_quality=JPEG_QUALITY,
                    pre_variant=pre_variant,
                    pre_warped=pre_warped,
                    pre_timing=pre_timing,
                )
                last_post_ms = (time.time() - tp0) * 1000.0
            except Exception as e:
                resp = {"ok": False, "reason": f"http_error: {e}"}

            sent += 1
            send_count += 1

            if WORKER_DEBUG or (not (isinstance(resp, dict) and resp.get("log_level") == "debug")):
                print(f"[infer] {resp}")

            plate = ""
            valid = False
            if isinstance(resp, dict):
                plate = str(resp.get("plate", "") or "")
                valid = bool(resp.get("valid", False))

            plate_norm = _plate_norm(plate)
            if plate_norm:
                # state должен жить между кадрами даже при временно invalid ответах,
                # иначе EVENT_MODE=on_plate_change будет видеть "first_plate" на каждом цикле
                events.mark_seen(now, plate_norm)

            if EVENT_MODE == "on_plate_change":
                if plate_norm:
                    if plate_norm != events.last_sent_plate:
                        events.mark_sent(now, plate_norm)
                    else:
                        if events.can_send_plate(now, plate_norm):
                            events.mark_sent(now, plate_norm)
            elif EVENT_MODE == "on_plate_confirmed":
                if plate_norm:
                    hits = events.note_plate(now, plate_norm)
                    if hits >= PLATE_CONFIRM_K and events.can_send_plate(now, plate_norm):
                        events.mark_sent(now, plate_norm)

            if STAB_MODE in ("plate", "hybrid") and plate_norm:
                _ = events.note_plate(now, plate_norm)

        if DECISION_LOG_EVERY_SEC > 0 and (now - last_decision_log_ts) >= DECISION_LOG_EVERY_SEC:
            print(f"[rtsp_worker] decision send={int(want_send)} reason={send_reason} mode={EVENT_MODE}/{STAB_MODE} track_new={int(track_new)} best_score={best_crop_score:.4f}")
            last_decision_log_ts = now

        if STATE_LOG_EVERY_SEC > 0 and (now - last_state_log_ts) >= STATE_LOG_EVERY_SEC:
            lp = events.last_seen_plate or "-"
            lsp = events.last_sent_plate or "-"
            seen_hits = 0
            if lp != "-":
                seen_hits = len([t for t in events.plate_hits.get(lp, []) if (now - t) <= float(events.plate_confirm_window_sec)])
            sent_plate_hits = 0
            if lsp != "-":
                sent_plate_hits = len([t for t in events.plate_hits.get(lsp, []) if (now - t) <= float(events.plate_confirm_window_sec)])
            trk_state = int(track.track_id) if track.box is not None else 0
            hits_keys = len(events.plate_hits)
            print(
                f"[rtsp_worker] state last_plate={lp} last_sent_plate={lsp} seen={seen_hits} sent={sent_plate_hits} "
                f"tracker_id={trk_state} hits_keys={hits_keys} tracker_obj={id(track)} events_obj={id(events)} total_sent={sent}"
            )
            last_state_log_ts = now

        # heartbeat
        if HB_EVERY_SEC > 0 and (now - hb_last) >= HB_EVERY_SEC:
            dt_win = max(0.001, now - hb_window_t0)
            fps_est = float(hb_frames) / float(dt_win)
            hb_window_t0 = now
            hb_frames = 0

            st = grabber.stats()
            backend = grabber.backend_name()

            dt_stats = max(1e-3, now - t0_stats)
            det_fps_eff = float(det_count) / dt_stats
            send_fps_eff = float(send_count) / dt_stats

            _post_heartbeat(
                HEARTBEAT_URL,
                {
                    "ts": now,
                    "frame_ts": float(frame_ts),
                    "alive": True,
                    "disabled": False,
                    "frozen": frozen_now,
                    "note": note,
                    "camera_id": CAMERA_ID,
                    "fps": round(float(fps_est), 3),
                    "errors": int(read_errors),
                    "sent": int(sent),
                    "frame_w": int(w),
                    "frame_h": int(h),
                    "roi": list(roi),
                    "backend": backend,
                    "grab_age_ms": round(float(grab_age_ms), 1),
                    "read_fps_eff": round(float(st.get("read_fps_eff", 0.0)), 2),
                    "det_fps_eff": round(float(det_fps_eff), 2),
                    "send_fps_eff": round(float(send_fps_eff), 2),
                    "last_det_ms": round(float(last_det_ms), 2),
                    "last_post_ms": round(float(last_post_ms), 2),
                    "auto_profile": auto_profile,
                    "auto_enabled": int(auto_cfg.enable),
                    "auto_metrics_source": AUTO_METRICS_SOURCE,
                },
                timeout_sec=1.0,
            )
            hb_last = now

        # debug save
        if SAVE_EVERY > 0 and (tick % int(SAVE_EVERY) == 0):
            ts = int(time.time())
            base_name = f"{ts}_{sent}_{tick}"

            if SAVE_FULL_FRAME:
                cv2.imwrite(os.path.join(SAVE_DIR, f"frame_{base_name}.jpg"), frame)

            if SAVE_WITH_ROI:
                vis = frame.copy()
                cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
                if best_full is not None:
                    cv2.rectangle(vis, (best_full.x1, best_full.y1), (best_full.x2, best_full.y2), (0, 255, 255), 2)
                cv2.imwrite(os.path.join(SAVE_DIR, f"frame_roi_{base_name}.jpg"), vis)

            cv2.imwrite(os.path.join(SAVE_DIR, f"roi_{base_name}.jpg"), roi_frame)

            if crop_dbg is not None and crop_dbg.size > 0:
                cv2.imwrite(os.path.join(SAVE_DIR, f"crop_{base_name}.jpg"), crop_dbg)

            if rect_dbg is not None and rect_dbg.size > 0:
                cv2.imwrite(os.path.join(SAVE_DIR, f"rectify_{base_name}.jpg"), rect_dbg)

            if crop_to_send is not None and crop_to_send.size > 0:
                cv2.imwrite(os.path.join(SAVE_DIR, f"send_{base_name}.jpg"), crop_to_send)

            if SAVE_SEND_BYTES and jpeg_bytes_sent:
                atomic_write_bytes(os.path.join(SAVE_DIR, f"send_{base_name}.jpg.bytes"), jpeg_bytes_sent)

            try:
                meta = {
                    "ts": now,
                    "camera_id": CAMERA_ID,
                    "frame_ts": float(frame_ts),
                    "frame_w": int(w),
                    "frame_h": int(h),
                    "roi": [int(x1), int(y1), int(x2), int(y2)],
                    "best_full": None
                    if best_full is None
                    else [int(best_full.x1), int(best_full.y1), int(best_full.x2), int(best_full.y2), float(best_full.conf)],
                    "plate_pad": float(PLATE_PAD),
                    "plate_pad_used": float(last_pad_used),
                    "plate_pad_reason": str(last_pad_reason),
                    "bbox_wh": [int(last_bbox_wh[0]), int(last_bbox_wh[1])],
                    "rectify": bool(rect_enable_tick),
                    "rectify_w": int(RECTIFY_W),
                    "rectify_h": int(RECTIFY_H),
                    "rectify_ms": None if rectify_ms is None else round(float(rectify_ms), 2),
                    "deskew": {
                        "enable": bool(DESKEW_ENABLE),
                        "deg": round(float(deskew_deg), 2),
                        "ms": None if deskew_ms is None else round(float(deskew_ms), 2),
                    },
                    "pre_variant": str(pre_variant),
                    "pre_warped": bool(pre_warped),
                    "sanity_ok": bool(crop_to_send is not None and crop_to_send.size > 0),
                    "sanity_fail_reason": str(sanity_fail_reason),
                    "auto": {
                        "enabled": bool(auto_cfg.enable),
                        "preproc_enabled": bool(AUTO_PREPROC_ENABLE),
                        "profile": auto_profile,
                        "metrics": auto_metrics,
                        "metrics_source": AUTO_METRICS_SOURCE,
                    },
                    "upscale": {
                        "enable": bool(upscale_enable_tick),
                        "min_w": int(upscale_min_w_tick),
                        "min_h": int(upscale_min_h_tick),
                    },
                }
                atomic_write_json(os.path.join(SAVE_DIR, f"meta_{base_name}.json"), meta)
            except Exception:
                pass

        # alive log
        if now - last_log >= LOG_EVERY_SEC:
            best_conf = best_roi.conf if best_roi is not None else None
            best_conf_str = "-" if best_conf is None else f"{best_conf:.2f}"
            trk = track.track_id if track.box is not None else 0
            last_seen = events.last_seen_plate or "-"
            last_sent = events.last_sent_plate or "-"
            print(
                f"[rtsp_worker] alive: backend={grabber.backend_name()} frame={w}x{h} roi={roi} det={det_cnt} best={best_conf_str} "
                f"track={trk} track_new={int(track_new)} sent={sent} seen={last_seen} sent_plate={last_sent} "
                f"grab_age_ms={grab_age_ms:.1f} url={current_rtsp_url} variant={pre_variant} "
                f"pad_used={last_pad_used:.3f} pad_reason={last_pad_reason} bbox={last_bbox_wh[0]}x{last_bbox_wh[1]} "
                f"sanity={sanity_fail_reason} "
                f"aspect={float(sanity_metrics.get('aspect', -1.0)):.3f} "
                f"thr={float(sanity_metrics.get('aspect_min', -1.0)):.2f} "
                f"area={float(sanity_metrics.get('bbox_area_ratio', -1.0)):.4f} "
                f"w={int(float(sanity_metrics.get('crop_w', -1.0)))} h={int(float(sanity_metrics.get('crop_h', -1.0)))} "
                f"conf={float(sanity_metrics.get('det_conf', -1.0)):.2f} "
                f"rule={str(sanity_metrics.get('rule', '-'))} "
                f"auto={int(auto_cfg.enable)} preproc={int(AUTO_PREPROC_ENABLE)} profile={auto_profile} "
                f"auto_src={AUTO_METRICS_SOURCE} deskew={int(DESKEW_ENABLE)}"
            )
            last_log = now

        tick += 1
        time.sleep(0.005)
