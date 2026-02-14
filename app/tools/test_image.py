# =========================================================
# Файл: app/tools/test_image.py
# Проект: LPR GateBox
# Версия: v0.3.6-test-suite-variants-tune-plus-video-prep-prefix
# Изменено: 2026-02-10 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - FIX (prep_video): добавлен уникальный префикс для кадров/кропов/датасета, чтобы
#   прогон нескольких видео в одну папку НЕ перезаписывал файлы.
#   Появился ENV:
#     DATASET_PREFIX=...    # например BaseName видео
#   Если не задан — префикс вычисляется автоматически из имени VIDEO_PATH.
#
# - FIX (prep_video): split_to_dataset() больше не перетирает одинаковые имена файлов:
#   если файл уже существует — добавляем суффикс _2/_3/...
#
# - NEW (prep_video): более стабильная разбивка train/val:
#   val считается по ГЛОБАЛЬНОМУ индексу в датасете (а не заново для каждого видео),
#   чтобы итоговый %val был ближе к ожидаемому.
#
# - НЕ ЛОМАЕМ: основной MODE=test (по умолчанию) остался прежним:
#   тестовые картинки → варианты (rot/postcrop) → gatebox /infer → CSV/summary/_failures
#
# ENV (общие):
#   TEST_OUT=/work/debug_test
#   TEST_GLOB=/work/test_images/*.jpg
#   SETTINGS_JSON=/config/settings.json
#   DET_MODEL_PATH=/models/license-plate-finetune-v1s.pt
#   DET_CONF=0.40
#   DET_IOU=0.45
#   DET_IMG_SIZE=416
#   PLATE_PAD=0.16
#   INFER_URL=http://gatebox:8080/infer
#   RECTIFY=1
#   RECTIFY_W=320
#   RECTIFY_H=96
#   REFINE_INNER_PAD=0.04
#   REFINE_MIN_AREA_RATIO=0.03
#   JPEG_QUALITY=85
#
# ENV (prep_video):
#   MODE=prep_video
#   VIDEO_PATH=/work/videos/input.mp4
#   DATASET_PREFIX=optional   # например имя видео без .mp4 (можно не задавать)
#   EXTRACT_FPS=2.0
#   EXTRACT_START_SEC=0
#   EXTRACT_MAX_SEC=0         # 0=всё видео
#   FRAMES_DIR=/work/debug_video/_frames
#   CROPS_DIR=/work/debug_video/_crops
#   DS_DIR=/work/debug_video/_ds_pose
#   VAL_EVERY_N=10            # 10 => ~10% в val
#   MIN_CROP_W=120
#   MIN_CROP_H=35
#   DEDUP=1
#   DEDUP_HASH_SIZE=8
#   DEDUP_MIN_HAMMING=4
# =========================================================

from __future__ import annotations

import os
import glob
import time
import json
import csv
import re
import shutil
from dataclasses import dataclass
from typing import Optional, Tuple, Any, Dict, List, Iterable

import cv2
import numpy as np
import requests
from ultralytics import YOLO


# -----------------------------
# ENV / defaults
# -----------------------------
OUT_DIR = os.environ.get("TEST_OUT", "/work/debug_test")
IMG_GLOB = os.environ.get("TEST_GLOB", "/work/test_images/*.jpg")
SETTINGS_JSON = os.environ.get("SETTINGS_JSON", "/config/settings.json")

DET_MODEL_PATH = os.environ.get("DET_MODEL_PATH", "/models/license-plate-finetune-v1s.pt")
DET_CONF = float(os.environ.get("DET_CONF", "0.40"))
DET_IOU = float(os.environ.get("DET_IOU", "0.45"))
DET_IMG_SIZE = int(os.environ.get("DET_IMG_SIZE", "416"))

PLATE_PAD = float(os.environ.get("PLATE_PAD", "0.16"))

INFER_URL = os.environ.get("INFER_URL", "http://gatebox:8080/infer")
HTTP_TIMEOUT_SEC = float(os.environ.get("HTTP_TIMEOUT_SEC", "3.0"))

RECTIFY = os.environ.get("RECTIFY", "1") == "1"
RECTIFY_W = int(os.environ.get("RECTIFY_W", "320"))
RECTIFY_H = int(os.environ.get("RECTIFY_H", "96"))

REFINE_INNER_PAD = float(os.environ.get("REFINE_INNER_PAD", "0.04"))
REFINE_MIN_AREA_RATIO = float(os.environ.get("REFINE_MIN_AREA_RATIO", "0.03"))

JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "85"))

# fallback defaults (если settings.json нет/битый)
DEFAULT_POSTCROP = True
DEFAULT_POSTCROP_LRBT = (0.040, 0.040, 0.080, 0.080)  # L,R,T,B

# -----------------------------
# mode / video prep env
# -----------------------------
MODE = os.environ.get("MODE", "test")  # test | prep_video

VIDEO_PATH = os.environ.get("VIDEO_PATH", "")
# NEW: префикс для именования файлов, чтобы прогон нескольких видео не затирал данные
DATASET_PREFIX_ENV = os.environ.get("DATASET_PREFIX", "").strip()

EXTRACT_FPS = float(os.environ.get("EXTRACT_FPS", "2.0"))
EXTRACT_START_SEC = float(os.environ.get("EXTRACT_START_SEC", "0") or "0")
EXTRACT_MAX_SEC = float(os.environ.get("EXTRACT_MAX_SEC", "0") or "0")  # 0=всё видео

FRAMES_DIR = os.environ.get("FRAMES_DIR", os.path.join(OUT_DIR, "_frames"))
CROPS_DIR = os.environ.get("CROPS_DIR", os.path.join(OUT_DIR, "_crops"))
DS_DIR = os.environ.get("DS_DIR", os.path.join(OUT_DIR, "_ds_pose"))

VAL_EVERY_N = int(os.environ.get("VAL_EVERY_N", "10") or "10")
MIN_CROP_W = int(os.environ.get("MIN_CROP_W", "120") or "120")
MIN_CROP_H = int(os.environ.get("MIN_CROP_H", "35") or "35")

DEDUP = os.environ.get("DEDUP", "1") != "0"
DEDUP_HASH_SIZE = int(os.environ.get("DEDUP_HASH_SIZE", "8") or "8")
DEDUP_MIN_HAMMING = int(os.environ.get("DEDUP_MIN_HAMMING", "4") or "4")


# -----------------------------
# TUNE env
# -----------------------------
TUNE_POSTCROP = os.environ.get("TUNE_POSTCROP", "0") == "1"
TUNE_SYMMETRIC = os.environ.get("TUNE_SYMMETRIC", "1") != "0"
TUNE_ROT = os.environ.get("TUNE_ROT", "1") != "0"
TUNE_MAX_IMAGES = int(os.environ.get("TUNE_MAX_IMAGES", "0") or "0")
TUNE_LR = os.environ.get("TUNE_LR", "0.010,0.050,0.005")
TUNE_TB = os.environ.get("TUNE_TB", "0.020,0.090,0.005")

# analyze env
SAVE_FAILURES = os.environ.get("SAVE_FAILURES", "1") != "0"
WORST_TOP_N = int(os.environ.get("WORST_TOP_N", "20") or "20")


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
def _safe_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _safe_bool(x: Any, default: bool) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return bool(x)
    if isinstance(x, str):
        return x.strip().lower() in ("1", "true", "yes", "y", "on")
    return default


def _read_json_allow_line_comments(path: str) -> Any:
    """
    Поддержка "квази-json" с // комментариями по строкам.
    Без фанатизма: просто выкидываем строки, где после strip() начинается с '//' .
    (Важно: inline-комментарии не режем.)
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    cleaned: List[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("//"):
            continue
        cleaned.append(ln)
    txt = "\n".join(cleaned)
    return json.loads(txt)


def load_settings_ocr(path: str) -> Tuple[bool, Tuple[float, float, float, float], str]:
    """
    Читает settings.ocr из settings.json.
    Возвращает: (postcrop_enabled, (L,R,T,B), src_string)
    """
    if not path or not os.path.exists(path):
        return DEFAULT_POSTCROP, DEFAULT_POSTCROP_LRBT, "defaults:no_settings_file"

    try:
        d = _read_json_allow_line_comments(path)
    except Exception as e:
        return DEFAULT_POSTCROP, DEFAULT_POSTCROP_LRBT, f"defaults:bad_json:{type(e).__name__}"

    ocr = (((d.get("settings") or {}).get("ocr")) or {})
    postcrop = _safe_bool(ocr.get("postcrop"), DEFAULT_POSTCROP)

    lrbt = ocr.get("postcrop_lrbt")
    if isinstance(lrbt, (list, tuple)) and len(lrbt) == 4:
        l = _safe_float(lrbt[0], DEFAULT_POSTCROP_LRBT[0])
        r = _safe_float(lrbt[1], DEFAULT_POSTCROP_LRBT[1])
        t = _safe_float(lrbt[2], DEFAULT_POSTCROP_LRBT[2])
        b = _safe_float(lrbt[3], DEFAULT_POSTCROP_LRBT[3])
        return postcrop, (l, r, t, b), f"settings:{path}"

    return postcrop, DEFAULT_POSTCROP_LRBT, f"settings:{path}:missing_postcrop_lrbt"


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


def save_img(path: str, img: np.ndarray):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)


def save_json(path: str, obj: Any):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def copy_if_exists(src: str, dst: str):
    try:
        if src and os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    except Exception:
        pass


def post_infer(image_bgr: np.ndarray) -> dict:
    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError("cannot encode jpg")
    files = {"file": ("frame.jpg", buf.tobytes(), "image/jpeg")}
    r = requests.post(INFER_URL, files=files, timeout=HTTP_TIMEOUT_SEC)
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.reason}; body={r.text}")
    return r.json()


def yolo_best_plate_bbox(model: YOLO, frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int, float]]:
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


def apply_postcrop_lrbt(img: np.ndarray, lrbt: Tuple[float, float, float, float]) -> Tuple[np.ndarray, Dict[str, Any]]:
    l, r, t, b = lrbt
    hh, ww = img.shape[:2]
    ml = int(ww * l)
    mr = int(ww * r)
    mt = int(hh * t)
    mb = int(hh * b)
    meta = {"lrbt": [l, r, t, b], "px": [ml, mr, mt, mb], "src_w_h": [ww, hh]}
    if ww <= (ml + mr + 2) or hh <= (mt + mb + 2):
        meta["applied"] = False
        meta["reason"] = "crop_too_large"
        return img, meta
    out = img[mt:hh - mb, ml:ww - mr].copy()
    meta["applied"] = True
    meta["out_w_h"] = [out.shape[1], out.shape[0]]
    return out, meta


def rotate_variant(img: np.ndarray, angle: int) -> np.ndarray:
    """angle in {0,90,180,270} clockwise"""
    if angle == 0:
        return img
    if angle == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError("angle must be 0/90/180/270")


@dataclass
class Variant:
    name: str
    rot: int
    postcrop: bool


def write_csv(path: str, rows: List[dict]):
    if not rows:
        return
    keys = set()
    for r in rows:
        keys.update(r.keys())
    fieldnames = sorted(keys)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# -----------------------------
# scoring / tune / analyze
# -----------------------------
PLATE_RE = re.compile(r"([АВЕКМНОРСТУХA-Z]\d{3}[АВЕКМНОРСТУХA-Z]{2}\d{2,3})", re.IGNORECASE)


def extract_expected_from_filename(path: str) -> Optional[str]:
    """
    Если в имени файла есть номер — используем как "ожидаемый".
    Пример: 1770_У616НН761_crop.jpg -> У616НН761
    """
    base = os.path.basename(path)
    m = PLATE_RE.search(base.replace("_", " "))
    if not m:
        return None
    return m.group(1).upper()


def score_resp(resp: Optional[dict], err: Optional[str], expected: Optional[str] = None) -> float:
    if resp is None:
        return -800.0

    ok = bool(resp.get("ok"))
    valid = bool(resp.get("valid"))
    noise = bool(resp.get("noise"))
    conf = float(resp.get("conf") or 0.0)

    plate = (resp.get("plate_norm") or resp.get("plate") or resp.get("raw") or "")
    if not isinstance(plate, str):
        plate = str(plate)

    score = 0.0
    score += 1000.0 if ok else 0.0
    score += 120.0 if valid else 0.0
    score += conf

    if noise:
        score -= 80.0

    # мягкий штраф за "обрезало"
    if len(plate) > 0 and len(plate) < 6:
        score -= 120.0
    if len(plate) == 0:
        score -= 200.0

    # если есть ожидаемый номер — бонус/штраф
    if expected:
        exp = expected.upper()
        got = plate.upper()
        if got == exp:
            score += 250.0
        else:
            score -= 20.0 * abs(len(got) - len(exp))

    return score


def classify_failure(det_ok: bool, resp: Optional[dict], err: Optional[str]) -> str:
    """
    Классификация "почему плохо" для _failures.
    """
    if not det_ok:
        return "no_det"
    if resp is None:
        return "infer_error"

    ok = resp.get("ok") is True
    valid = resp.get("valid") is True
    noise = resp.get("noise") is True
    conf = float(resp.get("conf") or 0.0)
    reason = (resp.get("reason") or "") if isinstance(resp.get("reason"), str) else ""

    if ok:
        return "ok"

    if not valid:
        if "invalid" in reason:
            return "invalid_format_or_region"
        return "invalid"

    if reason in ("cooldown", "not_enough_hits"):
        return reason

    if resp.get("allowed") is False:
        return "not_allowed"

    if noise:
        return "noise"

    if conf < 0.80:
        return "low_conf"

    return "other"


def parse_range(s: str) -> Tuple[float, float, float]:
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"bad range: {s}")
    a, b, step = float(parts[0]), float(parts[1]), float(parts[2])
    return a, b, step


def frange(a: float, b: float, step: float) -> Iterable[float]:
    x = a
    n = 0
    while x <= b + 1e-12:
        yield float(f"{x:.3f}")
        n += 1
        x = a + n * step


def tune_postcrop_lrbt(
    model: YOLO,
    images: List[str],
    postcrop_enabled_settings: bool,
    postcrop_src: str,
) -> Dict[str, Any]:
    lr_a, lr_b, lr_step = parse_range(TUNE_LR)
    tb_a, tb_b, tb_step = parse_range(TUNE_TB)

    imgs = images[:]
    if TUNE_MAX_IMAGES > 0:
        imgs = imgs[:TUNE_MAX_IMAGES]

    if not postcrop_enabled_settings:
        print("[tune] WARNING: settings.ocr.postcrop=false — тюнинг всё равно сделаю, но в реальности postcrop не применится.")

    rotations = [0, 90, 180, 270] if TUNE_ROT else [0]

    candidates: List[Tuple[float, float, float, float]] = []
    if TUNE_SYMMETRIC:
        for lr in frange(lr_a, lr_b, lr_step):
            for tb in frange(tb_a, tb_b, tb_step):
                candidates.append((lr, lr, tb, tb))
    else:
        for l in frange(lr_a, lr_b, lr_step):
            for r in frange(lr_a, lr_b, lr_step):
                for t in frange(tb_a, tb_b, tb_step):
                    for b in frange(tb_a, tb_b, tb_step):
                        candidates.append((l, r, t, b))

    print(f"[tune] candidates={len(candidates)} symmetric={int(TUNE_SYMMETRIC)} rot={int(TUNE_ROT)} images={len(imgs)}")

    def preprocess_to_ocr_in(frame_path: str) -> Optional[Dict[str, Any]]:
        frame = cv2.imread(frame_path)
        if frame is None:
            return None

        H, W = frame.shape[:2]
        best = yolo_best_plate_bbox(model, frame)
        if best is None:
            return {"ok": False, "reason": "no_det"}

        x1, y1, x2, y2, det_conf = best
        ex1, ey1, ex2, ey2 = expand_box(x1, y1, x2, y2, PLATE_PAD, W, H)

        ocr_in = frame[ey1:ey2, ex1:ex2].copy()
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
            warped, _crop_pre_dbg, _quad_full, warp_reason, warp_meta = _unpack_refine_result(rr)
            if warped is not None and getattr(warped, "size", 0) > 0:
                ocr_in = warped

        return {
            "ok": True,
            "ocr_in": ocr_in,
            "det_conf": det_conf,
            "warp_reason": warp_reason,
            "warp_meta": warp_meta,
        }

    pre: List[Dict[str, Any]] = []
    for p in imgs:
        x = preprocess_to_ocr_in(p)
        if x is None:
            pre.append({"path": p, "ok": False, "reason": "read_fail"})
        else:
            x["path"] = p
            x["expected"] = extract_expected_from_filename(p)
            pre.append(x)

    def eval_lrbt(lrbt: Tuple[float, float, float, float]) -> Dict[str, Any]:
        total = 0.0
        ok_n = 0
        valid_n = 0
        infer_err_n = 0
        samples: List[Dict[str, Any]] = []

        for item in pre:
            if not item.get("ok"):
                total -= 150.0
                continue

            ocr_in: np.ndarray = item["ocr_in"]
            expected = item.get("expected")

            best_score = -1e9
            best_rot = 0
            best_resp = None
            best_err = None
            best_plate = None

            for rot in rotations:
                img = rotate_variant(ocr_in, rot) if rot else ocr_in
                img2, _meta = apply_postcrop_lrbt(img, lrbt)

                resp = None
                err = None
                try:
                    resp = post_infer(img2)
                except Exception as e:
                    err = str(e)

                sc = score_resp(resp, err, expected=expected)
                if sc > best_score:
                    best_score = sc
                    best_rot = rot
                    best_resp = resp
                    best_err = err
                    if resp is not None:
                        best_plate = resp.get("plate_norm") or resp.get("plate") or resp.get("raw")

            total += best_score
            if best_resp is None:
                infer_err_n += 1
            else:
                if best_resp.get("valid") is True:
                    valid_n += 1
                if best_resp.get("ok") is True:
                    ok_n += 1

            samples.append({
                "path": item["path"],
                "expected": expected,
                "best_rot": best_rot,
                "best_score": float(f"{best_score:.3f}"),
                "best_plate": best_plate,
                "best_ok": (best_resp.get("ok") if best_resp else None),
                "best_valid": (best_resp.get("valid") if best_resp else None),
                "best_conf": (best_resp.get("conf") if best_resp else None),
                "best_error": best_err,
            })

        samples_sorted = sorted(samples, key=lambda x: x.get("best_score", 0.0))[:10]

        return {
            "lrbt": [float(f"{x:.3f}") for x in lrbt],
            "total_score": float(f"{total:.3f}"),
            "ok_count": ok_n,
            "valid_count": valid_n,
            "infer_errors": infer_err_n,
            "worst_samples": samples_sorted,
        }

    best: Optional[Dict[str, Any]] = None
    top: List[Dict[str, Any]] = []

    t0 = time.time()
    for i, lrbt in enumerate(candidates, 1):
        rep = eval_lrbt(lrbt)
        top.append(rep)

        if best is None or rep["total_score"] > best["total_score"]:
            best = rep

        if i % 10 == 0 or i == len(candidates):
            dt = time.time() - t0
            print(f"[tune] {i}/{len(candidates)} best_score={best['total_score'] if best else None} dt={dt:.1f}s", flush=True)

    top_sorted = sorted(top, key=lambda x: x["total_score"], reverse=True)
    top20 = top_sorted[:20]

    rec = best or top_sorted[0]
    rec_lrbt = rec["lrbt"]

    report = {
        "tune": {
            "enabled": True,
            "symmetric": bool(TUNE_SYMMETRIC),
            "rot_try": bool(TUNE_ROT),
            "lr_range": TUNE_LR,
            "tb_range": TUNE_TB,
            "max_images": TUNE_MAX_IMAGES,
            "images_used": len(imgs),
            "settings_postcrop_enabled": bool(postcrop_enabled_settings),
            "settings_src": postcrop_src,
        },
        "recommendation": {
            "postcrop": True,
            "postcrop_lrbt": rec_lrbt,
            "as_settings_json_fragment": {
                "settings": {"ocr": {"postcrop": True, "postcrop_lrbt": rec_lrbt}}
            },
        },
        "best": rec,
        "top20": top20,
    }

    tune_rows = []
    for it in top20:
        l, r, t, b = it["lrbt"]
        tune_rows.append({
            "L": l, "R": r, "T": t, "B": b,
            "total_score": it["total_score"],
            "ok_count": it["ok_count"],
            "valid_count": it["valid_count"],
            "infer_errors": it["infer_errors"],
        })

    tune_out_dir = os.path.join(OUT_DIR, "_tune")
    os.makedirs(tune_out_dir, exist_ok=True)
    save_json(os.path.join(tune_out_dir, "tune_report.json"), report)
    write_csv(os.path.join(tune_out_dir, "tune_top.csv"), tune_rows)

    print("[tune] RECOMMEND:")
    print(f"[tune]   postcrop_lrbt={rec_lrbt}  (L,R,T,B fractions)")
    print(f"[tune]   settings.json -> settings.ocr.postcrop_lrbt = {rec_lrbt}")
    print(f"[tune]   outputs: {tune_out_dir}/tune_report.json and tune_top.csv")

    return report


def print_recommendations(summary_files: List[Dict[str, Any]]):
    by_reason: Dict[str, int] = {}
    no_det = 0
    infer_err = 0
    invalid = 0
    ok_true = 0
    total = len(summary_files)

    for f in summary_files:
        r = str(f.get("best_failure_reason") or "unknown")
        by_reason[r] = by_reason.get(r, 0) + 1
        if r == "no_det":
            no_det += 1
        if r == "infer_error":
            infer_err += 1
        if r in ("invalid", "invalid_format_or_region"):
            invalid += 1
        if r == "ok":
            ok_true += 1

    top = sorted(by_reason.items(), key=lambda x: x[1], reverse=True)[:10]
    print("[analyze] failure reasons (best_variant):")
    for k, v in top:
        print(f"[analyze]   {k}: {v}/{total}")

    print("[analyze] recommendations:")
    if no_det > 0:
        print("[analyze] - Есть NO DET: копать детектор/условия камеры.")
        print("[analyze]   • попробуй DET_IMG_SIZE=640, DET_CONF=0.25..0.35, или обучить детектор на повёрнутых/ночных кадрах.")
        print("[analyze]   • проверь MIN_PLATE_W/H в worker (в реальности номера могут быть меньше).")

    if infer_err > 0:
        print("[analyze] - Есть infer_error: это уже gatebox падает/возвращает 400.")
        print("[analyze]   • смотри тела ошибок в variant_*_resp.json; чаще всего None/не тот тип в settings/whitelist/ALPHABET.")
        print("[analyze]   • проверь, что settings.json валидный (без // inline).")

    if invalid > 0:
        print("[analyze] - Много invalid: это обычно кроп/поворот/обрезка букв.")
        print("[analyze]   • тюнь POSTCROP_LRBT (особенно T/B): если режет сверху/снизу — падает valid.")
        print("[analyze]   • если часто best_variant = rot90/rot270 — нужно учить детектор/пайплайн на повёрнутых номерах (или разворот до OCR).")

    if ok_true == 0 and total > 0:
        print("[analyze] - ok=True почти не встречается: возможно whitelist/allowed/confirm/cooldown мешают.")
        print("[analyze]   • для тестов можно временно поставить CONFIRM_N=1 и COOLDOWN_SEC=0 в gatebox, чтобы увидеть 'сырой' успех OCR.")

    if ok_true > 0:
        print(f"[analyze] - ok=True есть: {ok_true}/{total}. Уже можно отжимать edge-кейсы через _failures и дообучение.")


# -----------------------------
# NEW: prep_video helpers
# -----------------------------
def _slugify(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^0-9A-Za-zА-Яа-я_\-\.]+", "_", s)
    s = s.strip("._-")
    return s[:64] if len(s) > 64 else s


def _auto_dataset_prefix(video_path: str) -> str:
    base = os.path.basename(video_path or "")
    base = os.path.splitext(base)[0]
    return _slugify(base) or "video"


def _resolve_dataset_prefix(video_path: str) -> str:
    # FIX: префикс всегда есть (либо ENV, либо по имени видео)
    if DATASET_PREFIX_ENV:
        return _slugify(DATASET_PREFIX_ENV) or "video"
    return _auto_dataset_prefix(video_path)


def _prefixed_name(prefix: str, name: str) -> str:
    p = _slugify(prefix)
    return f"{p}_{name}" if p else name


def _safe_copy_unique(src: str, dst_dir: str) -> str:
    """
    FIX: если файл уже существует — добавляем _2/_3/... чтобы ничего не перетирать.
    Возвращает реальный путь, куда скопировали.
    """
    os.makedirs(dst_dir, exist_ok=True)
    base = os.path.basename(src)
    dst = os.path.join(dst_dir, base)
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
        return dst

    root, ext = os.path.splitext(base)
    k = 2
    while True:
        cand = os.path.join(dst_dir, f"{root}_{k}{ext}")
        if not os.path.exists(cand):
            shutil.copy2(src, cand)
            return cand
        k += 1


def _count_files(dirpath: str) -> int:
    try:
        return len([x for x in os.listdir(dirpath) if not x.startswith(".")])
    except Exception:
        return 0


def extract_frames_opencv(
    video_path: str,
    out_dir: str,
    fps_out: float,
    start_sec: float = 0.0,
    max_sec: float = 0.0,
    prefix: str = "",
) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(src_fps / max(0.1, fps_out))))

    if start_sec > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, start_sec * 1000.0)

    paths: List[str] = []
    idx = 0
    saved = 0
    t0 = time.time()

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        idx += 1
        if idx % step != 0:
            continue

        if max_sec > 0:
            pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0
            pos_sec = pos_ms / 1000.0
            if pos_sec - start_sec > max_sec:
                break

        fname = _prefixed_name(prefix, f"{saved:06d}.jpg") if prefix else f"{saved:06d}.jpg"
        p = os.path.join(out_dir, fname)
        cv2.imwrite(p, frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        paths.append(p)
        saved += 1

        if saved % 100 == 0:
            dt = time.time() - t0
            print(f"[prep] extracted {saved} frames dt={dt:.1f}s", flush=True)

    cap.release()
    print(f"[prep] frames extracted: {len(paths)} -> {out_dir}")
    return paths


def ahash(img_bgr: np.ndarray, hash_size: int = 8) -> int:
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    avg = float(g.mean())
    bits = (g > avg).astype(np.uint8).flatten()
    h = 0
    for b in bits:
        h = (h << 1) | int(b)
    return h


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def prepare_crops_from_frames(model: YOLO, frame_paths: List[str], crops_dir: str, prefix: str = "") -> List[str]:
    os.makedirs(crops_dir, exist_ok=True)
    crop_paths: List[str] = []
    seen_hashes: List[int] = []

    for i, fp in enumerate(frame_paths, 1):
        frame = cv2.imread(fp)
        if frame is None:
            continue

        H, W = frame.shape[:2]
        best = yolo_best_plate_bbox(model, frame)
        if best is None:
            continue

        x1, y1, x2, y2, det_conf = best
        ex1, ey1, ex2, ey2 = expand_box(x1, y1, x2, y2, PLATE_PAD, W, H)
        crop = frame[ey1:ey2, ex1:ex2].copy()

        if crop.shape[1] < MIN_CROP_W or crop.shape[0] < MIN_CROP_H:
            continue

        if DEDUP:
            h = ahash(crop, DEDUP_HASH_SIZE)
            window = seen_hashes[-300:]
            if any(hamming(h, hh) < DEDUP_MIN_HAMMING for hh in window):
                continue
            seen_hashes.append(h)

        # FIX: имя кропа включает префикс (по видео), чтобы не затирать при множественных видео
        base_name = f"{len(crop_paths):06d}_conf{det_conf:.2f}.jpg"
        fname = _prefixed_name(prefix, base_name) if prefix else base_name
        out = os.path.join(crops_dir, fname)

        cv2.imwrite(out, crop, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        crop_paths.append(out)

        if i % 200 == 0:
            print(f"[prep] frames={i}/{len(frame_paths)} crops={len(crop_paths)}", flush=True)

    print(f"[prep] crops prepared: {len(crop_paths)} -> {crops_dir}")
    return crop_paths


def split_to_dataset(crop_paths: List[str], ds_dir: str, val_every_n: int, global_start_idx: int = 0) -> int:
    """
    Кладём кропы в ds/images/train|val.
    FIX:
      - используем глобальный индекс (global_start_idx), чтобы val распределялся по всему датасету
      - копируем с уникальным именем, чтобы ничего не перетирать
    Возвращает новый global_index после добавления файлов.
    """
    train_dir = os.path.join(ds_dir, "images", "train")
    val_dir = os.path.join(ds_dir, "images", "val")
    os.makedirs(train_dir, exist_ok=True)
    os.makedirs(val_dir, exist_ok=True)

    g = int(global_start_idx)
    for p in crop_paths:
        g += 1
        dst_dir = val_dir if (val_every_n > 0 and g % val_every_n == 0) else train_dir
        _safe_copy_unique(p, dst_dir)

    print(f"[prep] dataset ready: {ds_dir}")
    print(f"[prep]  train: {_count_files(train_dir)}")
    print(f"[prep]  val:   {_count_files(val_dir)}")
    return g


def main_prep_video():
    os.makedirs(OUT_DIR, exist_ok=True)

    if not VIDEO_PATH:
        print("[prep] ERROR: VIDEO_PATH is empty")
        return
    if not os.path.exists(VIDEO_PATH):
        print(f"[prep] ERROR: VIDEO_PATH not found: {VIDEO_PATH}")
        return

    prefix = _resolve_dataset_prefix(VIDEO_PATH)

    print(f"[prep] MODE=prep_video video={VIDEO_PATH}")
    print(f"[prep] prefix={prefix}")
    print(f"[prep] det_model={DET_MODEL_PATH} conf={DET_CONF} iou={DET_IOU} imgsz={DET_IMG_SIZE} plate_pad={PLATE_PAD}")
    print(f"[prep] extract_fps={EXTRACT_FPS} start_sec={EXTRACT_START_SEC} max_sec={EXTRACT_MAX_SEC}")
    print(f"[prep] dedup={int(DEDUP)} hash_size={DEDUP_HASH_SIZE} min_hamming={DEDUP_MIN_HAMMING}")
    print(f"[prep] out: frames={FRAMES_DIR} crops={CROPS_DIR} ds={DS_DIR}")

    model = YOLO(DET_MODEL_PATH)

    frames = extract_frames_opencv(
        video_path=VIDEO_PATH,
        out_dir=FRAMES_DIR,
        fps_out=EXTRACT_FPS,
        start_sec=EXTRACT_START_SEC,
        max_sec=EXTRACT_MAX_SEC,
        prefix=prefix,
    )
    if not frames:
        print("[prep] ERROR: extracted 0 frames")
        return

    crops = prepare_crops_from_frames(model, frames, CROPS_DIR, prefix=prefix)
    if not crops:
        print("[prep] ERROR: prepared 0 crops (no detections?)")
        return

    # FIX: глобальный индекс val по всему датасету
    train_dir = os.path.join(DS_DIR, "images", "train")
    val_dir = os.path.join(DS_DIR, "images", "val")
    global_start = _count_files(train_dir) + _count_files(val_dir)

    _ = split_to_dataset(crops, DS_DIR, VAL_EVERY_N, global_start_idx=global_start)

    print("[prep] DONE.")
    print(f"[prep] DS_DIR: {DS_DIR}")
    print(f"[prep] Next step: разметить 4 угла (tl,tr,br,bl) на ds/images/train|val в Supervisely/CVAT.")


# -----------------------------
# main (test images)
# -----------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    postcrop_enabled, postcrop_lrbt, postcrop_src = load_settings_ocr(SETTINGS_JSON)

    imgs = sorted(glob.glob(IMG_GLOB))
    if not imgs:
        print(f"[test] no images for glob: {IMG_GLOB}")
        return

    print(f"[test] model={DET_MODEL_PATH} conf={DET_CONF} iou={DET_IOU} imgsz={DET_IMG_SIZE}")
    print(f"[test] infer_url={INFER_URL} rectify={int(RECTIFY)} out={OUT_DIR}")
    print(
        f"[test] rectify_size={RECTIFY_W}x{RECTIFY_H} plate_pad={PLATE_PAD} "
        f"min_area_ratio={REFINE_MIN_AREA_RATIO} jpeg_q={JPEG_QUALITY} "
        f"postcrop={int(postcrop_enabled)} postcrop_lrbt={postcrop_lrbt[0]:.3f},{postcrop_lrbt[1]:.3f},{postcrop_lrbt[2]:.3f},{postcrop_lrbt[3]:.3f} "
        f"postcrop_src={postcrop_src}"
    )

    variants: List[Variant] = [
        Variant("base", 0, False),
        Variant("postcrop", 0, True),
        Variant("rot90", 90, False),
        Variant("rot180", 180, False),
        Variant("rot270", 270, False),
        Variant("postcrop_rot90", 90, True),
        Variant("postcrop_rot180", 180, True),
        Variant("postcrop_rot270", 270, True),
    ]

    model = YOLO(DET_MODEL_PATH)

    if TUNE_POSTCROP:
        try:
            tune_postcrop_lrbt(
                model=model,
                images=imgs,
                postcrop_enabled_settings=postcrop_enabled,
                postcrop_src=postcrop_src,
            )
        except Exception as e:
            print(f"[tune] ERROR: {type(e).__name__}: {e}")

    csv_rows: List[dict] = []
    summary: Dict[str, Any] = {"files": [], "totals": {}}
    worst_rows: List[Dict[str, Any]] = []

    failures_root = os.path.join(OUT_DIR, "_failures")
    if SAVE_FAILURES:
        os.makedirs(failures_root, exist_ok=True)

    for path in imgs:
        frame = cv2.imread(path)
        if frame is None:
            print(f"[test] cannot read: {path}")
            continue

        base = os.path.splitext(os.path.basename(path))[0]
        ts = int(time.time() * 1000)

        out_dir = os.path.join(OUT_DIR, f"{ts}_{base}")
        os.makedirs(out_dir, exist_ok=True)

        H, W = frame.shape[:2]
        vis = frame.copy()

        best = yolo_best_plate_bbox(model, frame)
        if best is None:
            print(f"[test] {base} -> NO DET")
            save_img(os.path.join(out_dir, "frame_vis.jpg"), vis)

            per_file = {
                "file": base,
                "path": path,
                "det_ok": False,
                "reason": "no_det",
                "best_failure_reason": "no_det",
            }
            summary["files"].append(per_file)
            csv_rows.append({
                "file": base,
                "path": path,
                "det_ok": False,
                "reason": "no_det",
            })

            if SAVE_FAILURES:
                dst_dir = os.path.join(failures_root, "no_det", f"{ts}_{base}")
                os.makedirs(dst_dir, exist_ok=True)
                copy_if_exists(os.path.join(out_dir, "frame_vis.jpg"), os.path.join(dst_dir, "frame_vis.jpg"))

            continue

        x1, y1, x2, y2, det_conf = best
        ex1, ey1, ex2, ey2 = expand_box(x1, y1, x2, y2, PLATE_PAD, W, H)

        cv2.rectangle(vis, (ex1, ey1), (ex2, ey2), (0, 255, 255), 2)
        cv2.putText(
            vis,
            f"{det_conf:.2f}",
            (ex1, max(0, ey1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
        )

        crop = frame[ey1:ey2, ex1:ex2].copy()
        save_img(os.path.join(out_dir, "crop.jpg"), crop)

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
                save_img(os.path.join(out_dir, "crop_refine.jpg"), crop_pre_dbg)

            if warped is not None and getattr(warped, "size", 0) > 0:
                ocr_in = warped
                save_img(os.path.join(out_dir, "rectify.jpg"), warped)

        if quad_full is not None:
            try:
                cv2.polylines(vis, [quad_full.astype(np.int32)], True, (0, 255, 0), 2)
            except Exception:
                pass

        save_img(os.path.join(out_dir, "frame_vis.jpg"), vis)
        save_img(os.path.join(out_dir, "ocr_in.jpg"), ocr_in)

        per_file: Dict[str, Any] = {
            "file": base,
            "path": path,
            "det_ok": True,
            "det_conf": det_conf,
            "warp_reason": warp_reason,
            "warp_meta": warp_meta,
            "variants": [],
        }

        expected = extract_expected_from_filename(path)

        best_pick: Optional[Tuple[float, str, Optional[dict], Optional[str]]] = None
        best_variant_send_path = None
        best_variant_resp_path = None

        for v in variants:
            img = ocr_in

            if v.rot != 0:
                img = rotate_variant(img, v.rot)

            postcrop_meta: Dict[str, Any] = {"enabled": False}
            if v.postcrop and postcrop_enabled:
                img, postcrop_meta = apply_postcrop_lrbt(img, postcrop_lrbt)
                postcrop_meta["enabled"] = True
                postcrop_meta["src"] = postcrop_src
            elif v.postcrop and not postcrop_enabled:
                postcrop_meta = {"enabled": False, "reason": "postcrop_disabled_in_settings"}

            send_path = os.path.join(out_dir, f"ocr_send_{v.name}.jpg")
            save_img(send_path, img)

            resp = None
            err = None
            try:
                resp = post_infer(img)
            except Exception as e:
                err = str(e)

            resp_obj = {
                "variant": v.name,
                "rot": v.rot,
                "postcrop": v.postcrop,
                "postcrop_meta": postcrop_meta,
                "send_path": send_path,
                "infer_ok": resp is not None,
                "error": err,
                "resp": resp,
            }
            resp_path = os.path.join(out_dir, f"variant_{v.name}_resp.json")
            save_json(resp_path, resp_obj)

            sc = score_resp(resp, err, expected=expected)
            if best_pick is None or sc > best_pick[0]:
                best_pick = (sc, v.name, resp, err)
                best_variant_send_path = send_path
                best_variant_resp_path = resp_path

            row: Dict[str, Any] = {
                "file": base,
                "path": path,
                "expected": expected,
                "variant": v.name,
                "rot": v.rot,
                "postcrop": v.postcrop,
                "postcrop_enabled": postcrop_enabled,
                "postcrop_lrbt": ",".join([f"{x:.3f}" for x in postcrop_lrbt]),
                "postcrop_src": postcrop_src,
                "det_conf": det_conf,
                "warp_reason": warp_reason,
                "warp_method": (warp_meta.get("method") if isinstance(warp_meta, dict) else None),
                "warp_score": (warp_meta.get("score") if isinstance(warp_meta, dict) else None),
                "infer_ok": resp is not None,
                "infer_error": err,
                "score": float(f"{sc:.3f}"),
            }

            if resp is not None:
                row.update(
                    {
                        "plate": resp.get("plate"),
                        "raw": resp.get("raw"),
                        "plate_norm": resp.get("plate_norm"),
                        "conf": resp.get("conf"),
                        "valid": resp.get("valid"),
                        "allowed": resp.get("allowed"),
                        "ok": resp.get("ok"),
                        "reason": resp.get("reason"),
                        "noise": resp.get("noise"),
                        "ocr_variant": resp.get("ocr_variant") or resp.get("variant"),
                        "ocr_warped": resp.get("ocr_warped") if "ocr_warped" in resp else resp.get("warped"),
                        "mqtt_published": resp.get("mqtt_published"),
                    }
                )
                tm = resp.get("timing_ms") or {}
                if isinstance(tm, dict):
                    for k in ("decode", "ocr", "orient", "warp", "total"):
                        if k in tm:
                            row[f"timing_{k}_ms"] = tm[k]

            csv_rows.append(row)
            per_file["variants"].append(
                {
                    "name": v.name,
                    "infer_ok": resp is not None,
                    "ok": (resp.get("ok") if resp else None),
                    "valid": (resp.get("valid") if resp else None),
                    "conf": (resp.get("conf") if resp else None),
                    "plate": (resp.get("plate_norm") if resp else None) or (resp.get("plate") if resp else None),
                    "reason": (resp.get("reason") if resp else None),
                    "error": err,
                    "score": float(f"{sc:.3f}"),
                }
            )

        if best_pick is not None:
            best_sc, best_name, best_resp, best_err = best_pick
            per_file["best_variant"] = best_name
            per_file["best_score"] = float(f"{best_sc:.3f}")
            per_file["best_ok"] = (best_resp.get("ok") if best_resp else None)
            per_file["best_valid"] = (best_resp.get("valid") if best_resp else None)
            per_file["best_conf"] = (best_resp.get("conf") if best_resp else None)
            per_file["best_plate"] = (best_resp.get("plate_norm") if best_resp else None) or (best_resp.get("plate") if best_resp else None)
            per_file["best_error"] = best_err
            per_file["best_failure_reason"] = classify_failure(True, best_resp, best_err)

            worst_rows.append({
                "file": base,
                "path": path,
                "expected": expected,
                "best_variant": best_name,
                "best_score": per_file["best_score"],
                "best_ok": per_file["best_ok"],
                "best_valid": per_file["best_valid"],
                "best_conf": per_file["best_conf"],
                "best_plate": per_file["best_plate"],
                "best_error": best_err,
                "best_failure_reason": per_file["best_failure_reason"],
            })

            if SAVE_FAILURES and per_file["best_failure_reason"] != "ok":
                reason_dir = os.path.join(failures_root, per_file["best_failure_reason"], f"{ts}_{base}")
                os.makedirs(reason_dir, exist_ok=True)

                copy_if_exists(os.path.join(out_dir, "frame_vis.jpg"), os.path.join(reason_dir, "frame_vis.jpg"))
                copy_if_exists(os.path.join(out_dir, "ocr_in.jpg"), os.path.join(reason_dir, "ocr_in.jpg"))
                copy_if_exists(os.path.join(out_dir, "rectify.jpg"), os.path.join(reason_dir, "rectify.jpg"))
                copy_if_exists(os.path.join(out_dir, "crop.jpg"), os.path.join(reason_dir, "crop.jpg"))
                copy_if_exists(os.path.join(out_dir, "crop_refine.jpg"), os.path.join(reason_dir, "crop_refine.jpg"))

                if best_variant_send_path:
                    copy_if_exists(best_variant_send_path, os.path.join(reason_dir, f"ocr_send_{best_name}.jpg"))
                if best_variant_resp_path:
                    copy_if_exists(best_variant_resp_path, os.path.join(reason_dir, f"variant_{best_name}_resp.json"))

        summary["files"].append(per_file)

        print(
            f"[test] {base} -> best={per_file.get('best_variant')} "
            f"score={per_file.get('best_score')} ok={per_file.get('best_ok')} "
            f"valid={per_file.get('best_valid')} conf={per_file.get('best_conf')} plate={per_file.get('best_plate')}"
        )

    total_rows = len(csv_rows)
    ok_rows = sum(1 for r in csv_rows if r.get("infer_ok") and r.get("ok") is True)
    valid_rows = sum(1 for r in csv_rows if r.get("infer_ok") and r.get("valid") is True)
    err_rows = sum(1 for r in csv_rows if r.get("infer_ok") is False)
    summary["totals"] = {
        "rows": total_rows,
        "ok_true": ok_rows,
        "valid_true": valid_rows,
        "infer_errors": err_rows,
        "files": len(summary["files"]),
    }

    csv_path = os.path.join(OUT_DIR, "results.csv")
    write_csv(csv_path, csv_rows)

    summary_path = os.path.join(OUT_DIR, "summary.json")
    save_json(summary_path, summary)

    if worst_rows:
        worst_sorted = sorted(worst_rows, key=lambda x: x.get("best_score", 0.0))
        topn = worst_sorted[: max(1, WORST_TOP_N)]
        worst_path = os.path.join(OUT_DIR, "top_worst.csv")
        write_csv(worst_path, topn)

    print(f"[test] done. outputs: {OUT_DIR}")
    print(f"[test] csv: {csv_path}")
    print(f"[test] summary: {summary_path}")
    if worst_rows:
        print(f"[test] worst: {os.path.join(OUT_DIR, 'top_worst.csv')}")
    if SAVE_FAILURES:
        print(f"[test] failures: {os.path.join(OUT_DIR, '_failures')}")

    try:
        print_recommendations(summary.get("files") or [])
    except Exception:
        pass


if __name__ == "__main__":
    if MODE == "prep_video":
        main_prep_video()
    else:
        main()