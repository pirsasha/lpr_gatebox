# =========================================================
# Файл: app/core/plate_rectifier.py
# Проект: LPR GateBox
# Версия: v0.2.4
# Изменено: 2026-02-04 21:10 (UTC+3)
# Автор: Александр
# Что сделано:
# - Выделен rectifier в отдельный модуль (поиск quad + warpPerspective)
# - API: rectify_plate_quad(crop_bgr, out_w, out_h) -> (warped, quad)
# =========================================================

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Упорядочиваем 4 точки: tl, tr, br, bl.

    Важно для cv2.getPerspectiveTransform.
    """
    pts = pts.astype(np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def rectify_plate_quad(
    crop_bgr: np.ndarray,
    out_w: int = 320,
    out_h: int = 96,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Пробуем найти контур номера (quad) и выпрямить его warpPerspective.

    Возвращает:
      warped_bgr или None,
      quad_pts (4x2 float32) или None

    Примечание:
    - это best-effort: если не нашли подходящий quad, возвращаем (None, None)
    - рассчитано на crop номера, а не полный кадр
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None, None

    h, w = crop_bgr.shape[:2]
    # слишком маленькие кропы дают шумные контуры
    if w < 60 or h < 20:
        return None, None

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)

    # На RTSP-сжатии и грязных номерах Canny часто рвёт контуры.
    # Делаем лёгкий bilateral, а затем Canny + close.
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    edges = cv2.Canny(gray, 60, 160)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k, iterations=2)

    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None, None

    best_quad: Optional[np.ndarray] = None
    best_area = 0.0
    crop_area = float(w * h)

    for c in cnts:
        area = float(cv2.contourArea(c))
        # отсеиваем слишком мелкое (случайные контуры текста/шума)
        if area < 0.08 * crop_area:
            continue

        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4:
            continue

        pts = approx.reshape(4, 2).astype(np.float32)
        rect = cv2.minAreaRect(pts)
        rw, rh = rect[1]
        rw = float(rw)
        rh = float(rh)
        if rw <= 1 or rh <= 1:
            continue

        # Номер РФ — почти всегда вытянутый прямоугольник.
        ar = max(rw, rh) / max(1.0, min(rw, rh))
        if not (2.0 <= ar <= 7.5):
            continue

        if area > best_area:
            best_area = area
            best_quad = pts

    if best_quad is None:
        return None, None

    src = _order_quad(best_quad)
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )

    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(crop_bgr, M, (out_w, out_h), flags=cv2.INTER_LINEAR)
    return warped, best_quad
