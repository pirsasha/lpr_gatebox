# =========================================================
# Файл: app/tools/visualize_pose_warp.py
# Проект: LPR GateBox
# Версия: v0.3.6-pose-warp-visualize
# Изменено: 2026-02-10 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - NEW: визуализация работы YOLO-Pose для выравнивания номера:
#   - читает изображения (кропы) по GLOB
#   - прогоняет pose-модель (Ultralytics YOLOv8 Pose)
#   - рисует 4 keypoints + quad на исходнике
#   - делает warpPerspective -> "ровный" номер (RECTIFY_W x RECTIFY_H)
#   - сохраняет коллаж: input | overlay | rectified
#
# Как использовать (в контейнере rtsp_worker):
#   export POSE_MODEL=/models/plate4_pose_best.pt
#   export IMG_GLOB="/work/debug_test/dataset_plate4/_ds_pose/images/train/*.jpg"
#   export OUT_DIR="/work/debug_test/dataset_plate4/_pose_vis"
#   export RECTIFY_W=320
#   export RECTIFY_H=96
#   PYTHONPATH=/work python /work/app/tools/visualize_pose_warp.py
#
# ENV:
#   POSE_MODEL       путь к .pt (pose)
#   IMG_GLOB         glob на изображения
#   OUT_DIR          куда писать результаты
#   MAX_IMAGES       0=все, иначе лимит
#   CONF_TH          порог уверенности детекции (box conf)
#   KPT_CONF_TH      порог видимости точки (kpt conf)
#   RECTIFY_W/H      размер "ровного" номера
#   PAD_OUT          padding по краям выходного rectified (пиксели)
#   SAVE_SINGLE      1=сохранять отдельно overlay/rectified, 0=только коллаж
# =========================================================

from __future__ import annotations

import os
import glob
import math
from typing import List, Tuple, Optional

import cv2
import numpy as np
from ultralytics import YOLO


POSE_MODEL = os.environ.get("POSE_MODEL", "")
IMG_GLOB = os.environ.get("IMG_GLOB", "/work/debug_test/dataset_plate4/_ds_pose/images/train/*.jpg")
OUT_DIR = os.environ.get("OUT_DIR", "/work/debug_test/_pose_vis")

MAX_IMAGES = int(os.environ.get("MAX_IMAGES", "0") or "0")
CONF_TH = float(os.environ.get("CONF_TH", "0.10"))
KPT_CONF_TH = float(os.environ.get("KPT_CONF_TH", "0.20"))

RECTIFY_W = int(os.environ.get("RECTIFY_W", "320"))
RECTIFY_H = int(os.environ.get("RECTIFY_H", "96"))
PAD_OUT = int(os.environ.get("PAD_OUT", "0") or "0")

SAVE_SINGLE = os.environ.get("SAVE_SINGLE", "1") != "0"


def _mkdir(p: str):
    os.makedirs(p, exist_ok=True)


def _draw_point(img: np.ndarray, p: Tuple[int, int], label: str, color: Tuple[int, int, int]):
    x, y = p
    cv2.circle(img, (x, y), 5, color, -1)
    cv2.putText(img, label, (x + 6, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


def _safe_int(x: float) -> int:
    return int(round(float(x)))


def _order_quad_tl_tr_br_bl(pts: np.ndarray) -> np.ndarray:
    """
    На случай если модель/данные где-то "перекинули" порядок.
    Если ты обучал с фиксированным порядком tl,tr,br,bl — можно НЕ переупорядочивать.
    Но это даёт устойчивость на ранних тестах.
    """
    # pts: (4,2)
    s = pts.sum(axis=1)          # x+y
    d = (pts[:, 0] - pts[:, 1])  # x-y

    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmax(d)]
    bl = pts[np.argmin(d)]

    return np.stack([tl, tr, br, bl], axis=0)


def _warp_by_quad(img: np.ndarray, quad: np.ndarray, out_w: int, out_h: int, pad: int = 0) -> np.ndarray:
    """
    quad: (4,2) tl,tr,br,bl в координатах img
    """
    W = out_w + pad * 2
    H = out_h + pad * 2

    src = quad.astype(np.float32)
    dst = np.array(
        [
            [pad, pad],
            [pad + out_w - 1, pad],
            [pad + out_w - 1, pad + out_h - 1],
            [pad, pad + out_h - 1],
        ],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _make_collage(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """
    a|b|c по горизонтали, с приведением высоты.
    """
    h = max(a.shape[0], b.shape[0], c.shape[0])

    def resize_to_h(x: np.ndarray, hh: int) -> np.ndarray:
        if x.shape[0] == hh:
            return x
        scale = hh / x.shape[0]
        ww = max(1, int(round(x.shape[1] * scale)))
        return cv2.resize(x, (ww, hh), interpolation=cv2.INTER_AREA)

    a2 = resize_to_h(a, h)
    b2 = resize_to_h(b, h)
    c2 = resize_to_h(c, h)
    return np.concatenate([a2, b2, c2], axis=1)


def main():
    _mkdir(OUT_DIR)

    if not POSE_MODEL:
        raise SystemExit("POSE_MODEL is empty. Example: export POSE_MODEL=/models/plate4_pose_best.pt")

    paths = sorted(glob.glob(IMG_GLOB))
    if not paths:
        raise SystemExit(f"no images for IMG_GLOB: {IMG_GLOB}")

    if MAX_IMAGES > 0:
        paths = paths[:MAX_IMAGES]

    print(f"[vis] model={POSE_MODEL}")
    print(f"[vis] images={len(paths)} glob={IMG_GLOB}")
    print(f"[vis] out={OUT_DIR} rectify={RECTIFY_W}x{RECTIFY_H} pad_out={PAD_OUT}")
    print(f"[vis] conf_th={CONF_TH} kpt_conf_th={KPT_CONF_TH}")

    model = YOLO(POSE_MODEL)

    ok_n = 0
    fail_n = 0

    for i, p in enumerate(paths, 1):
        img = cv2.imread(p)
        if img is None:
            continue

        base = os.path.splitext(os.path.basename(p))[0]
        overlay = img.copy()

        # predict
        res = model.predict(source=img, conf=CONF_TH, verbose=False)
        if not res or res[0].boxes is None or len(res[0].boxes) == 0 or res[0].keypoints is None:
            fail_n += 1
            cv2.putText(overlay, "NO DET/KEYPOINTS", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
            col = _make_collage(img, overlay, np.zeros((RECTIFY_H, RECTIFY_W, 3), dtype=np.uint8))
            cv2.imwrite(os.path.join(OUT_DIR, f"{i:04d}_{base}__FAIL.jpg"), col)
            continue

        r0 = res[0]

        # берём лучший bbox по conf
        confs = r0.boxes.conf.cpu().numpy().astype(float)
        bi = int(np.argmax(confs))
        bconf = float(confs[bi])

        # keypoints: (n, k, 2) and conf: (n, k) in ultralytics
        kxy = r0.keypoints.xy.cpu().numpy()  # (n,k,2)
        kcf = None
        try:
            kcf = r0.keypoints.conf.cpu().numpy()
        except Exception:
            kcf = None

        pts = kxy[bi]  # (k,2)
        if pts.shape[0] < 4:
            fail_n += 1
            cv2.putText(overlay, "KPTS<4", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
            col = _make_collage(img, overlay, np.zeros((RECTIFY_H, RECTIFY_W, 3), dtype=np.uint8))
            cv2.imwrite(os.path.join(OUT_DIR, f"{i:04d}_{base}__FAIL.jpg"), col)
            continue

        pts4 = pts[:4].astype(np.float32)  # (4,2)
        # проверим видимость (если есть conf)
        if kcf is not None:
            vis = kcf[bi][:4].astype(float)
            if any(v < KPT_CONF_TH for v in vis):
                # всё равно покажем, но отметим как weak
                cv2.putText(overlay, f"WEAK_KPTS conf={bconf:.2f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 165, 255), 3)

        # упорядочим (устойчивость)
        quad = _order_quad_tl_tr_br_bl(pts4)

        # draw
        tl, tr, br, bl = quad
        tl_i = (_safe_int(tl[0]), _safe_int(tl[1]))
        tr_i = (_safe_int(tr[0]), _safe_int(tr[1]))
        br_i = (_safe_int(br[0]), _safe_int(br[1]))
        bl_i = (_safe_int(bl[0]), _safe_int(bl[1]))

        _draw_point(overlay, tl_i, "tl", (0, 0, 255))
        _draw_point(overlay, tr_i, "tr", (0, 255, 0))
        _draw_point(overlay, br_i, "br", (255, 0, 0))
        _draw_point(overlay, bl_i, "bl", (0, 255, 255))

        poly = np.array([tl_i, tr_i, br_i, bl_i], dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(overlay, [poly], True, (0, 255, 255), 2)
        cv2.putText(
            overlay,
            f"box_conf={bconf:.2f}",
            (10, max(30, img.shape[0] - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
        )

        # warp
        try:
            rect = _warp_by_quad(img, quad, RECTIFY_W, RECTIFY_H, pad=PAD_OUT)
            ok_n += 1
        except Exception:
            fail_n += 1
            rect = np.zeros((RECTIFY_H, RECTIFY_W, 3), dtype=np.uint8)
            cv2.putText(overlay, "WARP_FAIL", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)

        collage = _make_collage(img, overlay, rect)
        out_path = os.path.join(OUT_DIR, f"{i:04d}_{base}__posewarp.jpg")
        cv2.imwrite(out_path, collage)

        if SAVE_SINGLE:
            cv2.imwrite(os.path.join(OUT_DIR, f"{i:04d}_{base}__overlay.jpg"), overlay)
            cv2.imwrite(os.path.join(OUT_DIR, f"{i:04d}_{base}__rect.jpg"), rect)

        if i % 25 == 0:
            print(f"[vis] {i}/{len(paths)} ok={ok_n} fail={fail_n}", flush=True)

    print(f"[vis] DONE. ok={ok_n} fail={fail_n} out={OUT_DIR}")


if __name__ == "__main__":
    main()