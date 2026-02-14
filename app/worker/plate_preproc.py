# =========================================================
# Файл: app/core/plate_preproc.py
# Проект: LPR GateBox
# Версия: v0.3.7-auto-preproc
# Изменено: 2026-02-11 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - NEW: профили предобработки изображения номера ДО OCR:
#   day_v1 / night_v1 / glare_v1
# - NEW: apply_profile(profile, img_bgr) — единый вход
# =========================================================

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np


def _lut_gamma(gamma: float) -> np.ndarray:
    g = max(0.05, float(gamma))
    inv = 1.0 / g
    table = np.array([((i / 255.0) ** inv) * 255.0 for i in range(256)], dtype=np.uint8)
    return table


def _clahe_luma(img_bgr: np.ndarray, clip_limit: float, tile_grid: int) -> np.ndarray:
    """CLAHE по яркости (Y) — обычно безопаснее, чем по всем каналам."""
    ycrcb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = cv2.split(ycrcb)

    clahe = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=(int(tile_grid), int(tile_grid)))
    y2 = clahe.apply(y)

    out = cv2.merge([y2, cr, cb])
    return cv2.cvtColor(out, cv2.COLOR_YCrCb2BGR)


def _unsharp(img_bgr: np.ndarray, amount: float = 0.6, radius: int = 1) -> np.ndarray:
    """Лёгкая резкость: unsharp mask."""
    r = max(1, int(radius))
    blur = cv2.GaussianBlur(img_bgr, (2 * r + 1, 2 * r + 1), 0)
    out = cv2.addWeighted(img_bgr, 1.0 + float(amount), blur, -float(amount), 0)
    return out


def preproc_day_v1(img_bgr: np.ndarray) -> np.ndarray:
    """
    День: лёгкий контраст + чуть резкости.
    """
    x = _clahe_luma(img_bgr, clip_limit=1.6, tile_grid=8)
    x = _unsharp(x, amount=0.5, radius=1)
    return x


def preproc_night_v1(img_bgr: np.ndarray) -> np.ndarray:
    """
    Ночь: denoise + подъем теней (gamma) + лёгкий CLAHE.
    Важно: не делать "мыло", поэтому резкость аккуратно.
    """
    # denoise (быстрый режим)
    x = cv2.fastNlMeansDenoisingColored(img_bgr, None, 6, 6, 7, 21)

    # gamma > 1.0 -> поднимаем тени
    lut = _lut_gamma(1.35)
    x = cv2.LUT(x, lut)

    # чуть контраста
    x = _clahe_luma(x, clip_limit=1.8, tile_grid=8)

    # чуть резкости
    x = _unsharp(x, amount=0.45, radius=1)
    return x


def preproc_glare_v1(img_bgr: np.ndarray) -> np.ndarray:
    """
    Блики: пытаемся приглушить хайлайты.
    Делается аккуратно, чтобы не "убить" цифры.
    """
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    # Сжимаем верхний диапазон яркости (soft-knee)
    v_f = v.astype(np.float32)
    knee = 200.0
    # всё выше knee слегка "прижимаем"
    over = np.maximum(0.0, v_f - knee)
    v2 = np.clip(v_f - over * 0.45, 0.0, 255.0).astype(np.uint8)

    hsv2 = cv2.merge([h, s, v2])
    x = cv2.cvtColor(hsv2, cv2.COLOR_HSV2BGR)

    # после подавления блика лёгкий контраст
    x = _clahe_luma(x, clip_limit=1.5, tile_grid=8)
    return x


def apply_profile(profile: Optional[str], img_bgr: np.ndarray) -> np.ndarray:
    """
    Единая точка входа.
    profile: None/"off" -> без изменений
    """
    p = (profile or "").strip().lower()
    if not p or p in ("off", "none", "0"):
        return img_bgr

    try:
        if p in ("day", "day_v1"):
            return preproc_day_v1(img_bgr)
        if p in ("night", "night_v1"):
            return preproc_night_v1(img_bgr)
        if p in ("glare", "glare_v1"):
            return preproc_glare_v1(img_bgr)
    except Exception:
        return img_bgr

    # неизвестный профиль -> no-op
    return img_bgr