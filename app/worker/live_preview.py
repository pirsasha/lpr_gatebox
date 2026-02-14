# =========================================================
# Файл: app/worker/live_preview.py
# Проект: LPR GateBox
# Версия: v0.3.7-all-in-one-split
# =========================================================

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.worker.forensics import atomic_write_bytes, atomic_write_json
from app.worker.settings import expand_box

try:
    from app.core.plate_rectifier import rectify_plate_quad  # type: ignore
except ModuleNotFoundError:
    from core.plate_rectifier import rectify_plate_quad  # type: ignore


def write_live_preview(
    live_dir: str,
    frame_bgr: np.ndarray,
    items: List[dict],
    roi_xyxy: Tuple[int, int, int, int],
    camera_id: str,
    ts: float,
    frame_w: int,
    frame_h: int,
    live_jpeg_quality: int,
    live_save_quad: bool,
    rectify_enable: bool,
    rectify_w: int,
    rectify_h: int,
    best_full_xyxy: Optional[Tuple[int, int, int, int]],
    plate_pad_used: float,
) -> None:
    x1, y1, x2, y2 = roi_xyxy
    quad = None

    if live_save_quad and rectify_enable and best_full_xyxy is not None:
        try:
            bx1, by1, bx2, by2 = best_full_xyxy
            ex1, ey1, ex2, ey2 = expand_box(bx1, by1, bx2, by2, plate_pad_used, frame_w, frame_h)
            crop_live = frame_bgr[ey1:ey2, ex1:ex2]
            if crop_live.size > 0:
                _warped_live, quad_crop = rectify_plate_quad(crop_live, out_w=rectify_w, out_h=rectify_h)
                if quad_crop is not None and getattr(quad_crop, "size", 0) >= 8:
                    qc = quad_crop.astype(np.float32)
                    qc[:, 0] += float(ex1)
                    qc[:, 1] += float(ey1)
                    quad = qc.astype(int).tolist()
        except Exception:
            quad = None

    try:
        ok_jpg, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(live_jpeg_quality)])
        if ok_jpg:
            atomic_write_bytes(os.path.join(live_dir, "frame.jpg"), bytes(buf))
        atomic_write_json(os.path.join(live_dir, "meta.json"), {"ts": ts, "w": frame_w, "h": frame_h, "camera_id": camera_id})
        atomic_write_json(
            os.path.join(live_dir, "boxes.json"),
            {"ts": ts, "w": frame_w, "h": frame_h, "items": items, "roi": [x1, y1, x2, y2], "quad": quad},
        )
    except Exception:
        pass
