# =========================================================
# Файл: app/core/plate_crop_refiner.py
# Проект: LPR GateBox
# Версия: v0.2.8 (quad-fix)
# Изменено: 2026-02-04 (UTC+3)
# Автор: Александр + правки
# ---------------------------------------------------------
# Что исправлено (важное):
# - FIX: на "грязных" номерах/рамках часто побеждал внутренний контур (часть номера),
#        из-за чего warpPerspective вырезал половину номера.
# - NEW: фильтры по покрытию ROI (width/height coverage), чтобы брать именно внешний прямоугольник рамки.
# - NEW: sanity-check для approx4: если площадь approx << площади minAreaRect -> используем boxPoints(rect).
# - CHG: скоринг учитывает ширину покрытия (width_ratio), это повышает стабильность.
# =========================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

import cv2
import numpy as np


@dataclass
class RefineResult:
    warped_bgr: Optional[np.ndarray]
    quad_full: Optional[np.ndarray]  # (4,2) float32 в координатах полного кадра
    crop_dbg: np.ndarray
    reason: str
    meta: Dict[str, Any]


def _order_points(pts: np.ndarray) -> np.ndarray:
    """Упорядочиваем точки: tl, tr, br, bl."""
    pts = pts.astype(np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _safe_clip_xyxy(x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> Tuple[int, int, int, int]:
    x1 = max(0, min(x1, w - 1))
    y1 = max(0, min(y1, h - 1))
    x2 = max(1, min(x2, w))
    y2 = max(1, min(y2, h))
    if x2 <= x1 + 1:
        x2 = min(w, x1 + 2)
    if y2 <= y1 + 1:
        y2 = min(h, y1 + 2)
    return x1, y1, x2, y2


def _poly_area(quad: np.ndarray) -> float:
    """Площадь четырехугольника."""
    try:
        return float(abs(cv2.contourArea(quad.astype(np.float32))))
    except Exception:
        return 0.0


def refine_and_warp_plate_for_ocr(
    frame_bgr: np.ndarray,
    bbox_xyxy: Tuple[float, float, float, float],
    out_w: int = 640,
    out_h: int = 200,
    inner_pad: float = 0.00,
    min_area_ratio: float = 0.06,
    plate_aspect_min: float = 2.0,
    plate_aspect_max: float = 8.5,
    # NEW: чтобы не выбирать "кусок номера"
    min_width_ratio: float = 0.60,   # кандидат должен покрывать >= 60% ширины ROI
    min_height_ratio: float = 0.35,  # и >= 35% высоты ROI (иначе это часто цифры/внутренности)
) -> RefineResult:
    """
    На вход: полный кадр + bbox (обычно от YOLO, уже с pad).
    На выход: warpPerspective на out_w/out_h.
    """
    H, W = frame_bgr.shape[:2]
    x1f, y1f, x2f, y2f = bbox_xyxy

    bw = max(2.0, x2f - x1f)
    bh = max(2.0, y2f - y1f)
    pad_x = int(bw * inner_pad)
    pad_y = int(bh * inner_pad)

    x1 = int(x1f) + pad_x
    y1 = int(y1f) + pad_y
    x2 = int(x2f) - pad_x
    y2 = int(y2f) - pad_y
    x1, y1, x2, y2 = _safe_clip_xyxy(x1, y1, x2, y2, W, H)

    roi = frame_bgr[y1:y2, x1:x2].copy()
    crop_dbg = roi.copy()
    if roi.size == 0:
        return RefineResult(None, None, crop_dbg, "empty_roi", {})

    roi_h, roi_w = roi.shape[:2]
    roi_area = float(roi_w * roi_h)

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # Поднимаем локальный контраст и чуть подавляем шум, сохраняя границы
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    g = clahe.apply(gray)
    g = cv2.bilateralFilter(g, d=7, sigmaColor=50, sigmaSpace=50)

    # Инвертированный adaptive threshold для "черных символов/рамок"
    thr = cv2.adaptiveThreshold(
        g, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31, 7
    )

    # Морфология, чтобы склеить рамку
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
    thr2 = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, kernel, iterations=1)

    # Canny помогает “внешней рамке” на грязных номерах
    edges = cv2.Canny(g, 60, 180)
    mix = cv2.bitwise_or(thr2, edges)
    mix = cv2.morphologyEx(mix, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(mix, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return RefineResult(None, None, crop_dbg, "no_contours", {"roi": (x1, y1, x2, y2), "contours": 0})

    best_score = -1e9
    best_quad_roi: Optional[np.ndarray] = None
    best_meta: Dict[str, Any] = {}

    for cnt in contours:
        rect = cv2.minAreaRect(cnt)
        (_cx, _cy), (rw, rh), _ang = rect
        rw = float(rw)
        rh = float(rh)
        if rw < 2 or rh < 2:
            continue

        # --- базовые признаки кандидата ---
        rect_area = rw * rh
        area_ratio = rect_area / max(1.0, roi_area)

        long_side = max(rw, rh)
        short_side = max(1e-6, min(rw, rh))
        aspect = long_side / short_side

        width_ratio = long_side / max(1.0, float(roi_w))
        height_ratio = short_side / max(1.0, float(roi_h))

        # --- фильтры (критично против “внутренних” контуров) ---
        if area_ratio < min_area_ratio:
            continue
        if not (plate_aspect_min <= aspect <= plate_aspect_max):
            continue
        if width_ratio < min_width_ratio:
            continue
        if height_ratio < min_height_ratio:
            continue

        # Попытаемся взять 4-угольник, но проверим его адекватность
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.03 * peri, True)

        method = "minAreaRect"
        quad = cv2.boxPoints(rect).astype(np.float32)

        if approx is not None and len(approx) == 4 and cv2.isContourConvex(approx):
            quad_a = approx.reshape(4, 2).astype(np.float32)
            # sanity: если approx сильно меньше minAreaRect -> это часто “внутренний прямоугольник”
            a_poly = _poly_area(quad_a)
            if a_poly >= 0.75 * rect_area:
                quad = quad_a
                method = "approx4"

        quad = _order_points(quad)

        # --- скоринг ---
        # хотим:
        #  - большой area_ratio
        #  - большое покрытие ширины (width_ratio)
        #  - aspect ближе к целевому (около 4.4..4.7)
        aspect_target = 4.5
        aspect_penalty = abs(aspect - aspect_target)

        score = (area_ratio * 100.0) + (width_ratio * 30.0) - (aspect_penalty * 3.0)

        if score > best_score:
            best_score = score
            best_quad_roi = quad
            best_meta = {
                "method": method,
                "area": rect_area,
                "area_ratio": area_ratio,
                "aspect": aspect,
                "width_ratio": width_ratio,
                "height_ratio": height_ratio,
                "score": score,
                "roi": (x1, y1, x2, y2),
                "contours": len(contours),
            }

    if best_quad_roi is None:
        return RefineResult(
            None,
            None,
            crop_dbg,
            "no_candidate_passed_filters",
            {"roi": (x1, y1, x2, y2), "contours": len(contours)},
        )

    quad_full = best_quad_roi.copy()
    quad_full[:, 0] += float(x1)
    quad_full[:, 1] += float(y1)

    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32
    )
    M = cv2.getPerspectiveTransform(quad_full.astype(np.float32), dst)
    warped = cv2.warpPerspective(frame_bgr, M, (out_w, out_h), flags=cv2.INTER_CUBIC)

    return RefineResult(warped, quad_full.astype(np.float32), crop_dbg, "ok", best_meta)
