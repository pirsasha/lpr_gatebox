# =========================================================
# Файл: app/worker/tracker.py
# Проект: LPR GateBox
# Версия: v0.3.7-all-in-one-split
# =========================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.worker.detector import DetBox


def iou(a: DetBox, b: DetBox) -> float:
    x1 = max(a.x1, b.x1)
    y1 = max(a.y1, b.y1)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    iw = max(0, x2 - x1)
    ih = max(0, y2 - y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1, a.w() * a.h())
    area_b = max(1, b.w() * b.h())
    return float(inter) / float(area_a + area_b - inter)


def smooth_box(prev: DetBox, cur: DetBox, alpha: float) -> DetBox:
    a = float(alpha)
    x1 = int(round(prev.x1 * a + cur.x1 * (1 - a)))
    y1 = int(round(prev.y1 * a + cur.y1 * (1 - a)))
    x2 = int(round(prev.x2 * a + cur.x2 * (1 - a)))
    y2 = int(round(prev.y2 * a + cur.y2 * (1 - a)))
    conf = max(prev.conf, cur.conf)
    return DetBox(x1=x1, y1=y1, x2=x2, y2=y2, conf=conf)


@dataclass
class TrackState:
    track_id: int = 0
    last_seen_ts: float = 0.0
    box: Optional[DetBox] = None
