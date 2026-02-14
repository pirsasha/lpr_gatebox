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
from typing import Tuple


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
    """ROI string: 'x1,y1,x2,y2' in full-frame pixels."""
    if not s:
        return (0, 0, w, h)
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        return (0, 0, w, h)
    try:
        x1, y1, x2, y2 = [int(float(p)) for p in parts]
    except Exception:
        return (0, 0, w, h)

    x1 = max(0, min(w - 1, x1))
    x2 = max(1, min(w, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(1, min(h, y2))
    if x2 <= x1:
        x2 = min(w, x1 + 1)
    if y2 <= y1:
        y2 = min(h, y1 + 1)
    return (x1, y1, x2, y2)


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
