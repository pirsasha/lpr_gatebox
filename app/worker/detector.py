# =========================================================
# Файл: app/worker/detector.py
# Проект: LPR GateBox
# Версия: v0.3.7-all-in-one-split
# =========================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

try:
    from ultralytics import YOLO  # type: ignore
except Exception:
    YOLO = None

try:
    import onnxruntime as ort  # type: ignore
except Exception:
    ort = None


@dataclass
class DetBox:
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float

    def w(self) -> int:
        return max(0, self.x2 - self.x1)

    def h(self) -> int:
        return max(0, self.y2 - self.y1)


class PlateDetector:
    """
    Supports:
      - .pt via ultralytics YOLO
      - .onnx via onnxruntime (best-effort)
    """

    def __init__(self, model_path: str, conf: float, iou_thr: float, imgsz: int):
        self.model_path = model_path
        self.conf = conf
        self.iou_thr = iou_thr
        self.imgsz = imgsz

        self.kind = "pt" if model_path.lower().endswith(".pt") else "onnx"
        self.yolo = None
        self.sess = None
        self.input_name: Optional[str] = None

        if self.kind == "pt":
            if YOLO is None:
                raise RuntimeError("ultralytics not installed, cannot load .pt model")
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"DET model not found: {model_path}")
            self.yolo = YOLO(model_path)
        else:
            if ort is None:
                raise RuntimeError("onnxruntime not installed, cannot load .onnx model")
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"DET model not found: {model_path}")
            self.sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            self.input_name = self.sess.get_inputs()[0].name

    def detect(self, frame_bgr: np.ndarray) -> List[DetBox]:
        h, w = frame_bgr.shape[:2]

        if self.kind == "pt":
            res = self.yolo.predict(
                source=frame_bgr,
                imgsz=self.imgsz,
                conf=self.conf,
                iou=self.iou_thr,
                verbose=False,
                device="cpu",
            )
            out: List[DetBox] = []
            if not res:
                return out
            r0 = res[0]
            if r0.boxes is None:
                return out

            for b in r0.boxes:
                xyxy = b.xyxy[0].cpu().numpy().tolist()
                conf = float(b.conf[0].cpu().numpy().item())
                x1, y1, x2, y2 = [int(round(v)) for v in xyxy]
                x1 = max(0, min(w - 1, x1))
                x2 = max(1, min(w, x2))
                y1 = max(0, min(h - 1, y1))
                y2 = max(1, min(h, y2))
                if x2 > x1 and y2 > y1:
                    out.append(DetBox(x1, y1, x2, y2, conf))

            out.sort(key=lambda bb: bb.conf, reverse=True)
            return out

        # ONNX best-effort
        img = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img_resized = cv2.resize(img, (self.imgsz, self.imgsz), interpolation=cv2.INTER_LINEAR)
        x = img_resized.astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[None, ...]

        outputs = self.sess.run(None, {self.input_name: x})
        arr = np.squeeze(outputs[0])

        out: List[DetBox] = []
        if arr.ndim == 2 and arr.shape[1] >= 5:
            sx = w / float(self.imgsz)
            sy = h / float(self.imgsz)
            for row in arr:
                conf = float(row[4])
                if conf < self.conf:
                    continue
                x1, y1, x2, y2 = row[0:4].tolist()
                ix1 = int(round(x1 * sx))
                iy1 = int(round(y1 * sy))
                ix2 = int(round(x2 * sx))
                iy2 = int(round(y2 * sy))
                ix1 = max(0, min(w - 1, ix1))
                ix2 = max(1, min(w, ix2))
                iy1 = max(0, min(h - 1, iy1))
                iy2 = max(1, min(h, iy2))
                if ix2 > ix1 and iy2 > iy1:
                    out.append(DetBox(ix1, iy1, ix2, iy2, conf))

        out.sort(key=lambda bb: bb.conf, reverse=True)
        return out
