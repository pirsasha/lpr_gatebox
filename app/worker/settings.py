# =========================================================
# Файл: app/worker/settings.py
# Проект: LPR GateBox
# Версия: v0.3.7-all-in-one-split
# Обновлено: 2026-02-11 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - Вынесены env_* хелперы и геометрические утилиты (ROI, pad)
# =========================================================

from __future__ import annotations

import os
from typing import List, Tuple


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except Exception:
        return default


def env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


def parse_roi(s: str, w: int, h: int) -> Tuple[int, int, int, int]:
    """ROI string: 'x1,y1,x2,y2' in full-frame pixels. Empty/zero -> full frame."""
    if w <= 0 or h <= 0:
        return (0, 0, 0, 0)

    if not s:
        return (0, 0, w, h)

    parts = [p.strip() for p in str(s).split(",")]
    if len(parts) != 4:
        return (0, 0, w, h)

    try:
        x1, y1, x2, y2 = [int(float(p)) for p in parts]
    except Exception:
        return (0, 0, w, h)

    # treat "zero roi" as full-frame (common reset value)
    if x1 == 0 and y1 == 0 and x2 == 0 and y2 == 0:
        return (0, 0, w, h)

    # clamp
    x1 = max(0, min(w - 1, x1))
    x2 = max(1, min(w, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(1, min(h, y2))

    # if invalid after clamp -> full-frame (not 1x1)
    if x2 <= x1 or y2 <= y1:
        return (0, 0, w, h)

    return (x1, y1, x2, y2)

def parse_roi_poly_str(s: str, w: int, h: int) -> List[Tuple[int, int]]:
    """ROI polygon string: 'x1,y1;x2,y2;...'. Returns clipped frame points."""
    if not s:
        return []
    pts: List[Tuple[int, int]] = []
    for raw in str(s).split(";"):
        p = raw.strip()
        if not p:
            continue
        xy = [x.strip() for x in p.split(",")]
        if len(xy) != 2:
            continue
        try:
            x = int(float(xy[0]))
            y = int(float(xy[1]))
        except Exception:
            continue
        x = max(0, min(w - 1, x))
        y = max(0, min(h - 1, y))
        pts.append((x, y))

    # минимум 3 точки для полигона
    if len(pts) < 3:
        return []
    return pts


def point_in_polygon(x: float, y: float, poly: List[Tuple[int, int]]) -> bool:
    """Ray casting point-in-polygon. poly in frame px."""
    n = len(poly)
    if n < 3:
        return True

    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (float(yj - yi) + 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def parse_roi_poly_str(s: str, w: int, h: int) -> List[Tuple[int, int]]:
    """ROI polygon string: 'x1,y1;x2,y2;...'. Returns clipped frame points."""
    if not s:
        return []
    pts: List[Tuple[int, int]] = []
    for raw in str(s).split(";"):
        p = raw.strip()
        if not p:
            continue
        xy = [x.strip() for x in p.split(",")]
        if len(xy) != 2:
            continue
        try:
            x = int(float(xy[0]))
            y = int(float(xy[1]))
        except Exception:
            continue
        x = max(0, min(w - 1, x))
        y = max(0, min(h - 1, y))
        pts.append((x, y))

    # минимум 3 точки для полигона
    if len(pts) < 3:
        return []
    return pts


def point_in_polygon(x: float, y: float, poly: List[Tuple[int, int]]) -> bool:
    """Ray casting point-in-polygon. poly in frame px."""
    n = len(poly)
    if n < 3:
        return True

    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (float(yj - yi) + 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def parse_roi_poly_str(s: str, w: int, h: int) -> List[Tuple[int, int]]:
    """ROI polygon string: 'x1,y1;x2,y2;...'. Returns clipped frame points."""
    if not s:
        return []
    pts: List[Tuple[int, int]] = []
    for raw in str(s).split(";"):
        p = raw.strip()
        if not p:
            continue
        xy = [x.strip() for x in p.split(",")]
        if len(xy) != 2:
            continue
        try:
            x = int(float(xy[0]))
            y = int(float(xy[1]))
        except Exception:
            continue
        x = max(0, min(w - 1, x))
        y = max(0, min(h - 1, y))
        pts.append((x, y))

    # минимум 3 точки для полигона
    if len(pts) < 3:
        return []
    return pts


def point_in_polygon(x: float, y: float, poly: List[Tuple[int, int]]) -> bool:
    """Ray casting point-in-polygon. poly in frame px."""
    n = len(poly)
    if n < 3:
        return True

    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (float(yj - yi) + 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def expand_box(x1: int, y1: int, x2: int, y2: int, pad: float, w: int, h: int) -> Tuple[int, int, int, int]:
    bw = max(1, x2 - x1)
    bh = max(1, y2 - y1)
    px = int(round(bw * pad))
    py = int(round(bh * pad))
    nx1 = max(0, x1 - px)
    ny1 = max(0, y1 - py)
    nx2 = min(w, x2 + px)
    ny2 = min(h, y2 + py)
    if nx2 <= nx1:
        nx2 = min(w, nx1 + 1)
    if ny2 <= ny1:
        ny2 = min(h, ny1 + 1)
    return nx1, ny1, nx2, ny2
