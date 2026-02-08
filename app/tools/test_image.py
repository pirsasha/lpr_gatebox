# =========================================================
# Файл: app/tools/test_image.py
# Проект: LPR GateBox
# Версия: v0.2.7-fix-test5
# Изменено: 2026-02-04 (UTC+3)
# ---------------------------------------------------------
# Оффлайн тест на изображениях:
# 1) YOLO (ultralytics) -> bbox
# 2) refine + warp по белой рамке (quad/minAreaRect) -> rectify image
# 3) OCR через gatebox /infer (HTTP) -> печать результата
# 4) debug-save: frame_vis, crop, rectify, ocr_in, ocr_preproc, ocr_postcrop
#
# NEW:
# - POSTCROP теперь LRBT: OCR_POSTCROP_L/R/T/B (доли)
# - Алиасы OCR_POSTCROP_X/Y всё ещё работают (симметрично по X/Y)
# =========================================================

from __future__ import annotations

import os
import glob
import time
from typing import Optional, Tuple, Any, Dict

import cv2
import numpy as np
import requests
from ultralytics import YOLO


# -----------------------------
# ENV / defaults
# -----------------------------
OUT_DIR = os.environ.get("TEST_OUT", "/work/debug_test")
IMG_GLOB = os.environ.get("TEST_GLOB", "/work/test_images/*.jpg")

DET_MODEL_PATH = os.environ.get("DET_MODEL_PATH", "/models/license-plate-finetune-v1s.pt")
DET_CONF = float(os.environ.get("DET_CONF", "0.35"))
DET_IOU = float(os.environ.get("DET_IOU", "0.45"))
DET_IMG_SIZE = int(os.environ.get("DET_IMG_SIZE", "640"))

PLATE_PAD = float(os.environ.get("PLATE_PAD", "0.00"))

INFER_URL = os.environ.get("INFER_URL", "http://gatebox:8080/infer")
HTTP_TIMEOUT_SEC = float(os.environ.get("HTTP_TIMEOUT_SEC", "3.0"))

RECTIFY = os.environ.get("RECTIFY", "1") == "1"
RECTIFY_W = int(os.environ.get("RECTIFY_W", "320"))
RECTIFY_H = int(os.environ.get("RECTIFY_H", "96"))

# Алиасы под старые подсказки/скрипты:
# OCR_WARP_W/H переопределяют RECTIFY_W/H
_OCR_WARP_W = os.environ.get("OCR_WARP_W")
_OCR_WARP_H = os.environ.get("OCR_WARP_H")
if _OCR_WARP_W:
    RECTIFY_W = int(_OCR_WARP_W)
if _OCR_WARP_H:
    RECTIFY_H = int(_OCR_WARP_H)

OCR_PREPROC = os.environ.get("OCR_PREPROC", "0") == "1"

# POSTCROP (0 = выключено)
POSTCROP = os.environ.get("OCR_POSTCROP", "1") == "1"

# NEW: LRBT
POSTCROP_L = os.environ.get("OCR_POSTCROP_L")
POSTCROP_R = os.environ.get("OCR_POSTCROP_R")
POSTCROP_T = os.environ.get("OCR_POSTCROP_T")
POSTCROP_B = os.environ.get("OCR_POSTCROP_B")

# Алиасы X/Y (симметрично), если LRBT не задан
POSTCROP_X = float(os.environ.get("OCR_POSTCROP_X", "0.04"))  # доля слева/справа
POSTCROP_Y = float(os.environ.get("OCR_POSTCROP_Y", "0.08"))  # доля сверху/снизу

# refine params
REFINE_INNER_PAD = float(os.environ.get("REFINE_INNER_PAD", "0.00"))
REFINE_MIN_AREA_RATIO = float(os.environ.get("REFINE_MIN_AREA_RATIO", "0.10"))


# -----------------------------
# Imports (совместимо с разными структурами)
# -----------------------------
def _import_refiner():
    try:
        from app.core.plate_crop_refiner import refine_and_warp_plate_for_ocr  # type: ignore
        return refine_and_warp_plate_for_ocr
    except Exception:
        from core.plate_crop_refiner import refine_and_warp_plate_for_ocr  # type: ignore
        return refine_and_warp_plate_for_ocr


refine_and_warp_plate_for_ocr = _import_refiner()


# -----------------------------
# helpers
# -----------------------------
def expand_box(x1, y1, x2, y2, pad: float, W: int, H: int) -> Tuple[int, int, int, int]:
    bw = x2 - x1
    bh = y2 - y1
    px = int(bw * pad)
    py = int(bh * pad)
    ex1 = max(0, int(x1 - px))
    ey1 = max(0, int(y1 - py))
    ex2 = min(W, int(x2 + px))
    ey2 = min(H, int(y2 + py))
    return ex1, ey1, ex2, ey2


def save_img(name: str, img: np.ndarray):
    os.makedirs(OUT_DIR, exist_ok=True)
    cv2.imwrite(os.path.join(OUT_DIR, name), img)


def ocr_preprocess(img_bgr: np.ndarray) -> np.ndarray:
    """Лёгкий ч/б препроцесс (опционально)."""
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    g = clahe.apply(g)
    g = cv2.bilateralFilter(g, 7, 50, 50)
    bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 7)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, k, iterations=1)
    return cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)


def post_infer(image_bgr: np.ndarray) -> dict:
    """POST /infer file=UploadFile поле = 'file'."""
    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise RuntimeError("cannot encode jpg")
    files = {"file": ("frame.jpg", buf.tobytes(), "image/jpeg")}
    r = requests.post(INFER_URL, files=files, timeout=HTTP_TIMEOUT_SEC)
    r.raise_for_status()
    return r.json()


def yolo_best_plate_bbox(model: YOLO, frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int, float]]:
    """Возвращает лучший bbox (x1,y1,x2,y2,conf)."""
    res = model.predict(source=frame_bgr, imgsz=DET_IMG_SIZE, conf=DET_CONF, iou=DET_IOU, verbose=False)
    if not res:
        return None
    r0 = res[0]
    if r0.boxes is None or len(r0.boxes) == 0:
        return None

    best = None
    best_conf = -1.0
    xyxy = r0.boxes.xyxy.cpu().numpy()
    confs = r0.boxes.conf.cpu().numpy()
    for (x1, y1, x2, y2), c in zip(xyxy, confs):
        c = float(c)
        if c > best_conf:
            best_conf = c
            best = (int(x1), int(y1), int(x2), int(y2), c)
    return best


def _unpack_refine_result(rr: Any):
    """
    Поддержка контрактов refiner-а:
    - старый: (warped, crop_dbg, quad_full)
    - новый: RefineResult(warped_bgr, quad_full, crop_dbg, reason, meta)
    """
    if rr is None:
        return None, None, None, "none", {}

    if isinstance(rr, tuple) and len(rr) == 3:
        warped, crop_dbg, quad_full = rr
        return warped, crop_dbg, quad_full, "tuple", {}

    warped = getattr(rr, "warped_bgr", None)
    crop_dbg = getattr(rr, "crop_dbg", None)
    quad_full = getattr(rr, "quad_full", None)
    reason = str(getattr(rr, "reason", "")) or "ok"
    meta = getattr(rr, "meta", {}) or {}
    if not isinstance(meta, dict):
        meta = {"meta": str(meta)}
    return warped, crop_dbg, quad_full, reason, meta


def _get_postcrop_lrbt() -> Tuple[float, float, float, float]:
    """
    Возвращает доли (L,R,T,B).
    Приоритет:
      1) OCR_POSTCROP_L/R/T/B
      2) OCR_POSTCROP_X/Y (симметрично)
    """
    if POSTCROP_L is not None or POSTCROP_R is not None or POSTCROP_T is not None or POSTCROP_B is not None:
        l = float(POSTCROP_L or "0")
        r = float(POSTCROP_R or "0")
        t = float(POSTCROP_T or "0")
        b = float(POSTCROP_B or "0")
        return l, r, t, b
    return POSTCROP_X, POSTCROP_X, POSTCROP_Y, POSTCROP_Y


# -----------------------------
# main
# -----------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    imgs = sorted(glob.glob(IMG_GLOB))
    if not imgs:
        print(f"[test] no images for glob: {IMG_GLOB}")
        return

    l, r, t, b = _get_postcrop_lrbt()

    print(f"[test] model={DET_MODEL_PATH} conf={DET_CONF} iou={DET_IOU} imgsz={DET_IMG_SIZE}")
    print(f"[test] infer_url={INFER_URL} rectify={int(RECTIFY)} out={OUT_DIR}")
    print(
        f"[test] rectify_size={RECTIFY_W}x{RECTIFY_H} plate_pad={PLATE_PAD} "
        f"min_area_ratio={REFINE_MIN_AREA_RATIO} ocr_preproc={int(OCR_PREPROC)} "
        f"postcrop={int(POSTCROP)} postcrop_lrbt={l:.3f},{r:.3f},{t:.3f},{b:.3f}"
    )

    model = YOLO(DET_MODEL_PATH)

    for path in imgs:
        frame = cv2.imread(path)
        if frame is None:
            print(f"[test] cannot read: {path}")
            continue

        H, W = frame.shape[:2]
        ts = int(time.time() * 1000)
        base = os.path.splitext(os.path.basename(path))[0]

        vis = frame.copy()

        best = yolo_best_plate_bbox(model, frame)
        if best is None:
            print(f"[test] {base} -> NO DET")
            save_img(f"{ts}_{base}_frame_vis.jpg", vis)
            continue

        x1, y1, x2, y2, conf = best
        ex1, ey1, ex2, ey2 = expand_box(x1, y1, x2, y2, PLATE_PAD, W, H)

        cv2.rectangle(vis, (ex1, ey1), (ex2, ey2), (0, 255, 255), 2)
        cv2.putText(vis, f"{conf:.2f}", (ex1, max(0, ey1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        crop = frame[ey1:ey2, ex1:ex2].copy()
        save_img(f"{ts}_{base}_crop.jpg", crop)

        ocr_in = crop
        quad_full = None
        warp_reason = "disabled"
        warp_meta: Dict[str, Any] = {}

        if RECTIFY:
            rr = refine_and_warp_plate_for_ocr(
                frame_bgr=frame,
                bbox_xyxy=(ex1, ey1, ex2, ey2),
                out_w=RECTIFY_W,
                out_h=RECTIFY_H,
                inner_pad=REFINE_INNER_PAD,
                min_area_ratio=REFINE_MIN_AREA_RATIO,
            )

            warped, crop_pre_dbg, quad_full, warp_reason, warp_meta = _unpack_refine_result(rr)

            if crop_pre_dbg is not None and getattr(crop_pre_dbg, "size", 0) > 0:
                save_img(f"{ts}_{base}_crop_refine.jpg", crop_pre_dbg)

            if warped is not None and getattr(warped, "size", 0) > 0:
                ocr_in = warped
                save_img(f"{ts}_{base}_rectify.jpg", warped)

        if quad_full is not None:
            cv2.polylines(vis, [quad_full.astype(np.int32)], True, (0, 255, 0), 2)

        save_img(f"{ts}_{base}_frame_vis.jpg", vis)
        save_img(f"{ts}_{base}_ocr_in.jpg", ocr_in)

        # препроцесс (опционально)
        if OCR_PREPROC:
            ocr_pp = ocr_preprocess(ocr_in)
            save_img(f"{ts}_{base}_ocr_preproc.jpg", ocr_pp)
            ocr_send = ocr_pp
        else:
            ocr_send = ocr_in

        # post-crop (LRBT)
        if POSTCROP:
            hh, ww = ocr_send.shape[:2]
            ml = int(ww * l)
            mr = int(ww * r)
            mt = int(hh * t)
            mb = int(hh * b)
            if ww > (ml + mr + 2) and hh > (mt + mb + 2):
                ocr_send = ocr_send[mt:hh - mb, ml:ww - mr].copy()
                save_img(f"{ts}_{base}_ocr_postcrop.jpg", ocr_send)

        # OCR через gatebox
        try:
            resp = post_infer(ocr_send)
            resp["warp_reason"] = warp_reason
            resp["warp_meta"] = warp_meta
            print(f"[test] {base} -> {resp}")
        except Exception as e:
            print(f"[test] {base} -> infer error: {e}")

    print(f"[test] done. outputs: {OUT_DIR}")


if __name__ == "__main__":
    main()
