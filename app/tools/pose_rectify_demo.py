# =========================================================
# Файл: app/tools/pose_rectify_demo.py
# Проект: LPR GateBox
# Версия: v0.3.6-pose-rectify-demo-fix-quad-order-and-try-rot
# Изменено: 2026-02-10 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - FIX: правильный порядок точек TL,TR,BR,BL (в прошлой версии TR/BL были перепутаны)
# - NEW: TRY_ROT=1 — пробуем 0/90/180/270 и выбираем лучший warp
# - NEW: QUAD_PAD_FRAC — расширяем quad наружу (часто помогает не "резать" рамку номера)
# - NEW: *_warp_best.jpg и meta с вариантами
#
# ENV:
#   POSE_MODEL=/path/to/best.pt
#   SRC_GLOB=/work/test_images/*.jpg
#   OUT_DIR=/work/debug_test/pose_warp_demo
#   WARP_W=320
#   WARP_H=96
#   QUAD_PAD_FRAC=0.06
#   TRY_ROT=1
#
#   # если SRC — НЕ кропы, включай детектор:
#   DET_MODEL=/models/license-plate-finetune-v1s.pt
#   DET_CONF=0.20
#   DET_IMG_SIZE=640
#   PLATE_PAD=0.14
#
#   OCR=1
#   INFER_URL=http://gatebox:8080/infer
# =========================================================

from __future__ import annotations

import os
import glob
import time
import json
import math
from typing import Optional, Tuple, Any, Dict, List

import cv2
import numpy as np
import requests
from ultralytics import YOLO


# -----------------------------
# ENV
# -----------------------------
POSE_MODEL = os.environ.get("POSE_MODEL", "")
SRC_GLOB = os.environ.get("SRC_GLOB", "/work/test_images/*.jpg")
OUT_DIR = os.environ.get("OUT_DIR", "/work/debug_test/pose_warp_demo")

WARP_W = int(os.environ.get("WARP_W", "320"))
WARP_H = int(os.environ.get("WARP_H", "96"))

QUAD_PAD_FRAC = float(os.environ.get("QUAD_PAD_FRAC", "0.06") or "0.06")
TRY_ROT = os.environ.get("TRY_ROT", "0") == "1"

# optional DET on full image
DET_MODEL = os.environ.get("DET_MODEL", "")
DET_CONF = float(os.environ.get("DET_CONF", "0.20"))
DET_IMG_SIZE = int(os.environ.get("DET_IMG_SIZE", "640"))
PLATE_PAD = float(os.environ.get("PLATE_PAD", "0.14"))

# OCR via gatebox /infer
OCR = os.environ.get("OCR", "0") == "1"
INFER_URL = os.environ.get("INFER_URL", "http://gatebox:8080/infer")
HTTP_TIMEOUT_SEC = float(os.environ.get("HTTP_TIMEOUT_SEC", "4.0"))


# -----------------------------
# helpers
# -----------------------------
def save_img(path: str, img: np.ndarray):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)


def save_json(path: str, obj: Any):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def post_infer(image_bgr: np.ndarray) -> dict:
    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise RuntimeError("cannot encode jpg")
    files = {"file": ("plate.jpg", buf.tobytes(), "image/jpeg")}
    r = requests.post(INFER_URL, files=files, timeout=HTTP_TIMEOUT_SEC)
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.reason}; body={r.text}")
    return r.json()


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


def yolo_best_plate_bbox(model: YOLO, frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int, float]]:
    res = model.predict(source=frame_bgr, imgsz=DET_IMG_SIZE, conf=DET_CONF, iou=0.45, verbose=False)
    if not res:
        return None
    r0 = res[0]
    if r0.boxes is None or len(r0.boxes) == 0:
        return None

    xyxy = r0.boxes.xyxy.cpu().numpy()
    confs = r0.boxes.conf.cpu().numpy()

    best = None
    best_conf = -1.0
    for (x1, y1, x2, y2), c in zip(xyxy, confs):
        c = float(c)
        if c > best_conf:
            best_conf = c
            best = (int(x1), int(y1), int(x2), int(y2), c)
    return best


def rotate_img(img: np.ndarray, angle: int) -> np.ndarray:
    if angle == 0:
        return img
    if angle == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError("angle must be 0/90/180/270")


def order_quad_points(pts_xy: np.ndarray) -> np.ndarray:
    """
    FIX: правильный TL,TR,BR,BL по sum/diff.
    pts_xy: shape (4,2)
    """
    a = np.array(pts_xy, dtype=np.float32)
    s = a[:, 0] + a[:, 1]
    d = a[:, 0] - a[:, 1]

    tl = a[np.argmin(s)]
    br = a[np.argmax(s)]
    tr = a[np.argmin(d)]  # <--- FIX: TR = min(x-y)
    bl = a[np.argmax(d)]  # <--- FIX: BL = max(x-y)

    return np.stack([tl, tr, br, bl], axis=0)


def pad_quad(quad: np.ndarray, pad_frac: float) -> np.ndarray:
    """
    Расширяем quad наружу от центра на pad_frac.
    """
    q = quad.astype(np.float32)
    c = q.mean(axis=0, keepdims=True)
    v = q - c
    return c + v * (1.0 + float(pad_frac))


def warp_by_quad(img_bgr: np.ndarray, kpts_xy: np.ndarray, out_w: int, out_h: int, pad_frac: float) -> np.ndarray:
    if kpts_xy.shape != (4, 2):
        raise ValueError("kpts_xy must be (4,2)")
    quad = order_quad_points(kpts_xy)
    quad = pad_quad(quad, pad_frac)

    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(quad, dst)
    warp = cv2.warpPerspective(img_bgr, M, (out_w, out_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return warp


def pick_pose_instance(r0) -> Optional[Tuple[np.ndarray, float, np.ndarray]]:
    """
    Берём лучший instance по bbox confidence (если несколько plate на кадре).
    Возвращает: (kpts_xy (4,2), inst_score, bbox_xyxy)
    """
    if getattr(r0, "keypoints", None) is None:
        return None
    if getattr(r0, "boxes", None) is None or len(r0.boxes) == 0:
        return None

    kpts = r0.keypoints.xy.cpu().numpy()        # (N, K, 2)
    kconf = r0.keypoints.conf.cpu().numpy()     # (N, K)
    bxyxy = r0.boxes.xyxy.cpu().numpy()         # (N, 4)
    bconf = r0.boxes.conf.cpu().numpy()         # (N,)

    best_i = int(np.argmax(bconf))
    if kpts.shape[1] < 4:
        return None

    # берем первые 4 keypoints
    xy = kpts[best_i, :4, :].astype(np.float32)
    sc = float(np.mean(kconf[best_i, :4]))
    bb = bxyxy[best_i].astype(np.float32)
    return xy, sc, bb


def draw_kpts(img: np.ndarray, pts_xy: np.ndarray):
    q = order_quad_points(pts_xy)
    q_int = q.astype(np.int32)
    cv2.polylines(img, [q_int], True, (0, 255, 0), 2)
    for i, (x, y) in enumerate(q_int.tolist()):
        cv2.circle(img, (x, y), 5, (0, 255, 255), -1)
        cv2.putText(img, str(i), (x + 6, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)


def ocr_score(resp: Optional[dict], err: Optional[str]) -> float:
    if resp is None:
        return -999.0
    ok = bool(resp.get("ok"))
    valid = bool(resp.get("valid"))
    noise = bool(resp.get("noise"))
    conf = float(resp.get("conf") or 0.0)
    plate = (resp.get("plate_norm") or resp.get("plate") or resp.get("raw") or "")
    if not isinstance(plate, str):
        plate = str(plate)

    score = 0.0
    score += 200.0 if ok else 0.0
    score += 80.0 if valid else 0.0
    score += conf * 1.0
    if noise:
        score -= 50.0
    if len(plate) == 0:
        score -= 80.0
    if 0 < len(plate) < 6:
        score -= 40.0
    return score


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    if not POSE_MODEL or not os.path.exists(POSE_MODEL):
        print(f"[demo] ERROR: POSE_MODEL not found: {POSE_MODEL}")
        return

    files = sorted(glob.glob(SRC_GLOB))
    print(f"[demo] model={POSE_MODEL}")
    print(f"[demo] src={SRC_GLOB} ({len(files)} files)")
    print(f"[demo] out={OUT_DIR} warp={WARP_W}x{WARP_H} ocr={int(OCR)} try_rot={int(TRY_ROT)} quad_pad={QUAD_PAD_FRAC:.3f}")

    if DET_MODEL:
        print(f"[demo] det_model={DET_MODEL} conf={DET_CONF} imgsz={DET_IMG_SIZE} pad={PLATE_PAD}")

    pose = YOLO(POSE_MODEL)
    det = YOLO(DET_MODEL) if DET_MODEL else None

    kept = 0
    t0 = time.time()

    rotations = [0, 90, 180, 270] if TRY_ROT else [0]

    for idx, p in enumerate(files, 1):
        img = cv2.imread(p)
        if img is None:
            continue

        base = os.path.splitext(os.path.basename(p))[0]
        vis_full = img.copy()

        # 1) если DET включён: режем bbox номера
        crop = img
        crop_offset = (0, 0)
        det_meta = None

        if det is not None:
            H, W = img.shape[:2]
            bb = yolo_best_plate_bbox(det, img)
            if bb is None:
                continue
            x1, y1, x2, y2, dc = bb
            ex1, ey1, ex2, ey2 = expand_box(x1, y1, x2, y2, PLATE_PAD, W, H)
            crop = img[ey1:ey2, ex1:ex2].copy()
            crop_offset = (ex1, ey1)
            det_meta = {"bbox_xyxy": [ex1, ey1, ex2, ey2], "conf": dc}
            cv2.rectangle(vis_full, (ex1, ey1), (ex2, ey2), (255, 0, 0), 2)
            cv2.putText(vis_full, f"det {dc:.2f}", (ex1, max(0, ey1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)

        # 2) TRY_ROT: крутим CROP, на каждом делаем pose->warp->(ocr) и выбираем лучший
        best = None  # dict
        variants: List[Dict[str, Any]] = []

        for rot in rotations:
            crop_r = rotate_img(crop, rot)

            pres = pose.predict(source=crop_r, imgsz=640, conf=0.25, iou=0.7, verbose=False)
            if not pres:
                continue
            r0 = pres[0]
            picked = pick_pose_instance(r0)
            if picked is None:
                continue

            kpts_xy, pose_sc, _bbox_xyxy = picked

            try:
                warp = warp_by_quad(crop_r, kpts_xy, WARP_W, WARP_H, QUAD_PAD_FRAC)
            except Exception as e:
                variants.append({"rot": rot, "ok": False, "error": f"warp:{e}"})
                continue

            ocr_resp = None
            ocr_err = None
            sc = pose_sc
            if OCR:
                try:
                    ocr_resp = post_infer(warp)
                except Exception as e:
                    ocr_err = str(e)
                sc = ocr_score(ocr_resp, ocr_err)

            v = {
                "rot": rot,
                "ok": True,
                "pose_score": float(pose_sc),
                "score": float(sc),
                "kpts_xy_crop": kpts_xy.tolist(),
                "ocr": ocr_resp,
                "ocr_error": ocr_err,
            }
            variants.append(v)

            if best is None or v["score"] > best["score"]:
                best = {"rot": rot, "warp": warp, "kpts_xy": kpts_xy, "pose_score": pose_sc, "score": sc, "ocr": ocr_resp, "ocr_error": ocr_err}

        if best is None:
            continue

        # 3) визуализация: рисуем точки на FULL (для красоты) — но точки у нас в crop-координатах.
        # Важно: если rot != 0 и DET включен — это чисто DEMO, мы рисуем только bbox, а не повернутые точки на full.
        # Чтобы не путать, точки рисуем на crop-preview тоже.
        crop_vis = crop.copy()
        draw_kpts(crop_vis, best["kpts_xy"])

        save_img(os.path.join(OUT_DIR, f"{base}_vis.jpg"), vis_full)
        save_img(os.path.join(OUT_DIR, f"{base}_crop_vis.jpg"), crop_vis)
        save_img(os.path.join(OUT_DIR, f"{base}_warp_best.jpg"), best["warp"])

        meta: Dict[str, Any] = {
            "src": p,
            "warp_size": [WARP_W, WARP_H],
            "quad_pad_frac": QUAD_PAD_FRAC,
            "try_rot": TRY_ROT,
            "best": {
                "rot": best["rot"],
                "pose_score": float(best["pose_score"]),
                "score": float(best["score"]),
                "kpts_xy_crop": best["kpts_xy"].tolist(),
                "ocr": best["ocr"],
                "ocr_error": best["ocr_error"],
            },
            "variants": variants,
            "det": det_meta,
        }
        save_json(os.path.join(OUT_DIR, f"{base}_meta.json"), meta)

        kept += 1
        if kept % 20 == 0:
            dt = time.time() - t0
            print(f"[demo] kept={kept} / {idx}/{len(files)} dt={dt:.1f}s", flush=True)

    print(f"[demo] DONE kept={kept} -> {OUT_DIR}")
    print("[demo] open *_warp_best.jpg to see rectified plates.")


if __name__ == "__main__":
    main()