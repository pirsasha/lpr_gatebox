# =========================================================
# Файл: app/tools/via_to_yolo_pose.py
# Проект: LPR GateBox
# Версия: v0.3.7-via-reorder-flipidx
# Изменено: 2026-02-10 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - FIX: import numpy (order_quad использует np)
# - NEW: AUTO_REORDER=1 -> приводим 4 точки к TL,TR,BR,BL (иначе pose учится хаосу)
# - NEW: data.yaml добавляет flip_idx для fliplr аугментации
#
# Запуск:
#   export VIA_JSON=/work/dataset_pose_via/via.json
#   export IMG_DIR=/work/dataset_pose_via/images
#   export OUT_DIR=/work/dataset_pose_via/yolo_pose
#   export VAL_EVERY_N=8
#   export AUTO_REORDER=1
#   PYTHONPATH=/work python /work/app/tools/via_to_yolo_pose.py
# =========================================================

from __future__ import annotations

import os
import json
import shutil
from typing import Dict, Any, List, Tuple, Optional

import cv2
import numpy as np

VIA_JSON = os.environ.get("VIA_JSON", "")
IMG_DIR = os.environ.get("IMG_DIR", "")
OUT_DIR = os.environ.get("OUT_DIR", "")
VAL_EVERY_N = int(os.environ.get("VAL_EVERY_N", "8") or "8")
REQUIRE_4PTS = os.environ.get("REQUIRE_4PTS", "1") != "0"
AUTO_REORDER = os.environ.get("AUTO_REORDER", "1") != "0"  # NEW: TL,TR,BR,BL по геометрии

# VIA attribute name containing points. If empty -> auto-detect.
VIA_ATTR = os.environ.get("VIA_ATTR", "")  # e.g. "plate4"


def die(msg: str):
    print(msg)
    raise SystemExit(1)


def load_via_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)


def order_quad(pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """
    Приводим 4 точки к TL,TR,BR,BL по геометрии (sum/diff).
    Это критично для pose: индексы keypoints должны быть стабильными.
    """
    if len(pts) != 4:
        return pts
    a = np.array(pts, dtype=np.float32)  # (4,2)
    s = a[:, 0] + a[:, 1]
    d = a[:, 0] - a[:, 1]

    tl = a[int(np.argmin(s))]
    br = a[int(np.argmax(s))]
    tr = a[int(np.argmin(d))]
    bl = a[int(np.argmax(d))]

    out = np.stack([tl, tr, br, bl], axis=0).astype(np.float32)

    # на всякий: если вдруг получились дубликаты (редко, но бывает при мусорной разметке)
    # то fallback сортировкой
    if len({tuple(map(float, p)) for p in out.tolist()}) < 4:
        b = sorted(pts, key=lambda x: (x[1], x[0]))  # by y then x
        top = sorted(b[:2], key=lambda x: x[0])
        bot = sorted(b[2:], key=lambda x: x[0])
        out = np.array([top[0], top[1], bot[1], bot[0]], dtype=np.float32)

    return [(float(x), float(y)) for x, y in out.tolist()]


def bbox_from_pts(pts: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    return cx, cy, bw, bh


def parse_points_from_region(r: Dict[str, Any]) -> Optional[List[Tuple[float, float]]]:
    """
    Поддержка VIA polygon:
      shape_attributes: { name: "polygon", all_points_x: [...], all_points_y: [...] }
    Берём первые 4 точки.
    """
    sa = r.get("shape_attributes") or {}
    if (sa.get("name") or "") != "polygon":
        return None
    xs = sa.get("all_points_x") or []
    ys = sa.get("all_points_y") or []
    if not isinstance(xs, list) or not isinstance(ys, list) or len(xs) != len(ys):
        return None
    pts = list(zip(xs, ys))
    if len(pts) < 4:
        return None
    pts = pts[:4]

    if AUTO_REORDER:
        try:
            pts = order_quad([(float(x), float(y)) for x, y in pts])
        except Exception:
            pts = [(float(x), float(y)) for x, y in pts]
    else:
        pts = [(float(x), float(y)) for x, y in pts]

    return pts


def pick_regions(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    # VIA formats vary: sometimes "regions" is dict; normalize to list
    regs = d.get("regions")
    if regs is None:
        return []
    if isinstance(regs, dict):
        return list(regs.values())
    if isinstance(regs, list):
        return regs
    return []


def region_ok_for_attr(region: Dict[str, Any]) -> bool:
    if not VIA_ATTR:
        return True
    ra = region.get("region_attributes") or {}
    # VIA может класть атрибуты строками/булями; мы просто проверим наличие ключа или совпадение значения
    if VIA_ATTR in ra:
        return True
    for k, v in ra.items():
        if str(v).strip() == VIA_ATTR:
            return True
    return False


def write_label_yolo_pose(label_path: str, cls: int, pts: List[Tuple[float, float]], img_w: int, img_h: int):
    cx, cy, bw, bh = bbox_from_pts(pts)

    # YOLO Pose label format:
    # class cx cy w h x1 y1 v1 x2 y2 v2 x3 y3 v3 x4 y4 v4 (all normalized 0..1)
    # visibility v: 0/1/2. Мы ставим 2 (visible).
    def nxy(p):
        return (max(0.0, min(1.0, p[0] / img_w)), max(0.0, min(1.0, p[1] / img_h)))

    kpts = []
    for p in pts:
        x, y = nxy(p)
        kpts.extend([x, y, 2])

    line = [cls, cx / img_w, cy / img_h, bw / img_w, bh / img_h] + kpts
    ensure_dir(os.path.dirname(label_path))
    with open(label_path, "w", encoding="utf-8") as f:
        f.write(" ".join([f"{v:.6f}" if isinstance(v, float) else str(v) for v in line]) + "\n")


def main():
    if not VIA_JSON or not os.path.exists(VIA_JSON):
        die("VIA_JSON not found. Set env VIA_JSON=/path/to/via.json")
    if not IMG_DIR or not os.path.isdir(IMG_DIR):
        die("IMG_DIR not found. Set env IMG_DIR=/path/to/images_dir")
    if not OUT_DIR:
        die("OUT_DIR empty. Set env OUT_DIR=/path/to/out_dir")

    out_images_train = os.path.join(OUT_DIR, "images", "train")
    out_images_val = os.path.join(OUT_DIR, "images", "val")
    out_labels_train = os.path.join(OUT_DIR, "labels", "train")
    out_labels_val = os.path.join(OUT_DIR, "labels", "val")
    ensure_dir(out_images_train)
    ensure_dir(out_images_val)
    ensure_dir(out_labels_train)
    ensure_dir(out_labels_val)

    via = load_via_json(VIA_JSON)

    # VIA projects typically: _via_img_metadata dict
    meta = via.get("_via_img_metadata")
    if not isinstance(meta, dict):
        die("Bad VIA json: _via_img_metadata not found")

    kept = 0
    skipped = 0

    for i, (img_id, rec) in enumerate(meta.items(), 1):
        filename = rec.get("filename")
        if not filename:
            skipped += 1
            continue

        regions = pick_regions(rec)
        regions = [r for r in regions if region_ok_for_attr(r)]

        pts = None
        for r in regions:
            p = parse_points_from_region(r)
            if p is not None:
                pts = p
                break

        if pts is None:
            if REQUIRE_4PTS:
                skipped += 1
                continue
            else:
                skipped += 1
                continue

        # source image
        src_img = os.path.join(IMG_DIR, filename)
        if not os.path.exists(src_img):
            skipped += 1
            continue

        img = cv2.imread(src_img)
        if img is None:
            skipped += 1
            continue

        h, w = img.shape[:2]

        # split train/val
        is_val = (VAL_EVERY_N > 0 and (kept + 1) % VAL_EVERY_N == 0)
        out_img_dir = out_images_val if is_val else out_images_train
        out_lbl_dir = out_labels_val if is_val else out_labels_train

        dst_img = os.path.join(out_img_dir, filename)
        shutil.copy2(src_img, dst_img)

        stem = os.path.splitext(os.path.basename(filename))[0]
        dst_lbl = os.path.join(out_lbl_dir, f"{stem}.txt")
        write_label_yolo_pose(dst_lbl, cls=0, pts=pts, img_w=w, img_h=h)

        kept += 1

    # write data.yaml
    yaml_path = os.path.join(OUT_DIR, "data.yaml")
    yaml = (
        f"path: {OUT_DIR}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"nc: 1\n"
        f"names: ['plate']\n"
        f"kpt_shape: [4, 3]\n"
        f"flip_idx: [1, 0, 3, 2]\n"
    )
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(yaml)

    print(f"[via2yolo] DONE out={OUT_DIR}")
    print(f"[via2yolo] kept={kept} skipped={skipped}")
    print(f"[via2yolo] yaml={yaml_path}")
    print("[via2yolo] next: yolo pose train model=yolov8n-pose.pt data=data.yaml imgsz=640 epochs=100")


if __name__ == "__main__":
    main()