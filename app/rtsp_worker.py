# =========================================================
# Файл: app/rtsp_worker.py
# Проект: LPR GateBox
# Версия: v0.3.4
# Изменено: 2026-02-07 20:10 (UTC+3)
# Автор: Александр
# Что сделано:
# - FIX: добавлена переменная FFMPEG_PROBE (иначе NameError при ffmpeg backend)
# - FIX: основной цикл обрабатывает только НОВЫЙ кадр (иначе 800% CPU на одном и том же кадре)
# - CHG: idle-sleep при отсутствии новых кадров для снижения нагрузки на мини-ПК
# - CHG: при отправке crop в /infer добавляем meta: pre_variant/pre_warped/pre_timing_ms (JSON)
# - CHG: suppress stdout для debug-ответов (мусор OCR) если WORKER_DEBUG=0
# - CHG: rectify_ms добавлен в pre_timing_ms при включённом RECTIFY
# - NEW: CAPTURE_BACKEND=auto|opencv|ffmpeg (автовыбор и фолбэки без участия пользователя)
# - NEW: FFmpegPipeGrabber (ffmpeg-pipe) с авто-restart по таймауту/EOF и игнорированием аудио (-an)
# - NEW: AutoGrabber: переключает backend при деградации grab_age_ms (по умолчанию >300мс)
# - CHG: main() использует AutoGrabber вместо прямого FrameGrabber
# =========================================================

# ИЗМЕНЕНО v0.3.0:
# - Разделены READ_FPS / DET_FPS / SEND_FPS.
# - Добавлен FrameGrabber (RTSP читается в отдельном потоке, старые кадры выбрасываем).
# - Основной цикл стал лёгким: YOLO строго по таймеру, отправка crop строго по таймеру.
# - В heartbeat добавлены diag поля для поиска задержек.

# app/rtsp_worker.py
# LPR_GATEBOX PATCH v0.2.1 | updated 2026-02-01
#
# FIX v0.2.0 (Heartbeat -> gatebox):
# 1) Добавлен heartbeat в gatebox /api/rtsp/heartbeat:
#    - воркер раз в HB_EVERY_SEC отправляет состояние (alive/frozen/fps/errors/sent/frame/roi)
#    - gatebox по этому heartbeat рисует UI "RTSP: OK" и age_ms
# 2) Настроено без хардкода:
#    - HEARTBEAT_URL можно задать явно
#    - иначе берём базу из INFER_URL (http://gatebox:8080) и добавляем /api/rtsp/heartbeat
#
#
# FIX v0.2.1 (UI: корректный frozen):
# 1) frozen=true теперь выставляется ТОЛЬКО если поток подозрительно "замёрз" дольше FREEZE_MAX_SEC.
#    Раньше frozen становился true сразу при низком diff (ложные срабатывания на статичной сцене).
# 2) note="freeze_suspect" выдаётся только при подтверждённом freeze; иначе note="ok".
#
# FIX v0.1.9 (оставлено):
# 1) Freeze watchdog:
#    - detects "same frame repeating" (VideoCapture stuck / RTSP stalled)
#    - auto reopens capture if frame doesn't change for FREEZE_MAX_SEC
# 2) Event-mode logic fixed:
#    - in on_plate_change / on_plate_confirmed we DO NOT spam infer every tick
#    - we probe OCR only when needed: new track OR resend window OR unknown plate
# 3) Debug saving remains fully switchable (SAVE_EVERY=0 by default)

from __future__ import annotations

import os
import time
import json
import subprocess
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import requests

# NEW: rectifier (quad/warp)
# FIX: rtsp_worker часто запускается как скрипт (python /work/app/rtsp_worker.py) с WORKDIR=/work/app.
#      В этом режиме пакет "app" не виден в sys.path, поэтому делаем безопасный fallback-импорт.
try:
    from app.core.plate_rectifier import rectify_plate_quad  # type: ignore
except ModuleNotFoundError:
    from core.plate_rectifier import rectify_plate_quad  # type: ignore

try:
    from ultralytics import YOLO  # type: ignore
except Exception:
    YOLO = None

try:
    import onnxruntime as ort  # type: ignore
except Exception:
    ort = None


# -----------------------------
# helpers / env
# -----------------------------
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


def _infer_base_url(infer_url: str) -> str:
    """Достаём базовый URL gatebox из INFER_URL.

    Примеры:
      http://gatebox:8080/infer -> http://gatebox:8080
      http://127.0.0.1:8080/infer -> http://127.0.0.1:8080
    """
    u = (infer_url or "").strip()
    if not u:
        return ""
    if u.endswith("/infer"):
        return u[: -len("/infer")]
    return u.rstrip("/")


def _post_heartbeat(url: str, payload: dict, timeout_sec: float = 1.0) -> None:
    """Отправка heartbeat в gatebox. Ошибки не роняют воркер."""
    if not url:
        return
    try:
        requests.post(url, json=payload, timeout=timeout_sec)
    except Exception:
        # intentionally silent (не спамим лог)
        return


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


def ensure_dir(p: str) -> None:
    if not p:
        return
    os.makedirs(p, exist_ok=True)


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Атомарная запись байт: пишем во временный файл рядом и rename."""
    d = os.path.dirname(path) or "."
    ensure_dir(d)
    tmp = os.path.join(d, f".{os.path.basename(path)}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: str, obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    atomic_write_bytes(path, raw)


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
    # EMA smoothing of bbox coordinates
    a = float(alpha)
    x1 = int(round(prev.x1 * a + cur.x1 * (1 - a)))
    y1 = int(round(prev.y1 * a + cur.y1 * (1 - a)))
    x2 = int(round(prev.x2 * a + cur.x2 * (1 - a)))
    y2 = int(round(prev.y2 * a + cur.y2 * (1 - a)))
    conf = max(prev.conf, cur.conf)
    return DetBox(x1=x1, y1=y1, x2=x2, y2=y2, conf=conf)


def draw_roi(img: np.ndarray, roi: Tuple[int, int, int, int], color=(0, 255, 0), thickness: int = 2) -> None:
    x1, y1, x2, y2 = roi
    cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness)


def draw_box(img: np.ndarray, b: DetBox, color=(0, 255, 255), thickness: int = 2) -> None:
    cv2.rectangle(img, (b.x1, b.y1), (b.x2, b.y2), color, thickness)
    label = f"{b.conf:.2f}"
    cv2.putText(img, label, (b.x1, max(0, b.y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)


# -----------------------------
# Rectification (optional)
# -----------------------------
# CHG: реализация вынесена в app/core/plate_rectifier.py
def rectify_plate(crop_bgr: np.ndarray, out_w: int, out_h: int) -> Optional[np.ndarray]:
    warped, _quad = rectify_plate_quad(crop_bgr, out_w=out_w, out_h=out_h)
    return warped


# -----------------------------
# Detector
# -----------------------------
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
        self.input_name = None

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

        # ONNX best-effort (kept)
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


# -----------------------------
# HTTP infer
# -----------------------------
def post_crop(
    infer_url: str,
    crop_bgr: np.ndarray,
    timeout_sec: float,
    jpeg_quality: int,
    pre_variant: str = "crop",
    pre_warped: bool = False,
    pre_timing: Optional[dict] = None,
) -> dict:
    """Отправляем crop в gatebox /infer.

    NEW v0.3.1:
    - добавляем meta полями формы: pre_variant/pre_warped/pre_timing_ms (JSON)
    - gatebox использует это для диагностики (variant/warped/timing_ms)
    """
    ok, buf = cv2.imencode(".jpg", crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    if not ok:
        return {"ok": False, "reason": "jpeg_encode_failed"}

    files = {"file": ("crop.jpg", buf.tobytes(), "image/jpeg")}
    data = {
        "pre_variant": str(pre_variant or "crop"),
        "pre_warped": "1" if bool(pre_warped) else "0",
        "pre_timing_ms": json.dumps(pre_timing or {}, ensure_ascii=False),
    }

    r = requests.post(infer_url, files=files, data=data, timeout=timeout_sec)
    r.raise_for_status()
    return r.json()



# -----------------------------
# ENV
# -----------------------------
RTSP_URL = env_str("RTSP_URL", "")
INFER_URL = env_str("INFER_URL", "http://gatebox:8080/infer")
RTSP_FPS = env_float("RTSP_FPS", 1.0)
WORKER_DEBUG = os.getenv("WORKER_DEBUG", "0") == "1"
# NEW v0.3.0: раздельные частоты (продуктовый режим)
# READ_FPS — как часто обновляем "последний кадр" из RTSP (старые кадры выбрасываем).
# DET_FPS  — как часто запускаем YOLO (главный регулятор CPU).
# SEND_FPS — как часто отправляем crop в gatebox (/infer) для быстрой стабилизации.
READ_FPS = env_float("READ_FPS", max(4.0, RTSP_FPS))
DET_FPS  = env_float("DET_FPS", 2.0)
SEND_FPS = env_float("SEND_FPS", 2.0)
# -------- Захват RTSP (backend) --------
# auto  = по умолчанию (opencv, а при деградации можно переключаться на ffmpeg)
# opencv= всегда cv2.VideoCapture
# ffmpeg= всегда ffmpeg-pipe
CAPTURE_BACKEND = env_str("CAPTURE_BACKEND", "opencv").strip().lower()
FFMPEG_PROBE = env_int("FFMPEG_PROBE", 0) == 1  # NEW v0.3.4: печать ffprobe/ffmpeg диагностики
# NEW v0.3.3: частота проверки авто-переключения backend
AUTO_SWITCH_CHECK_SEC = env_float("AUTO_SWITCH_CHECK_SEC", 1.0)
# NEW: параметры авто-переключения (если используешь auto-режим)
AUTO_SWITCH_AGE_MS = env_int("AUTO_SWITCH_AGE_MS", 300)          # порог "кадр старый"
AUTO_SWITCH_STREAK = env_int("AUTO_SWITCH_STREAK", 5)           # сколько раз подряд
AUTO_SWITCH_COOLDOWN_SEC = env_float("AUTO_SWITCH_COOLDOWN_SEC", 15.0)

# NEW: ffmpeg-pipe (если используешь ffmpeg backend)
FFMPEG_THREADS = env_int("FFMPEG_THREADS", 1)
FFMPEG_READ_TIMEOUT_SEC = env_float("FFMPEG_READ_TIMEOUT_SEC", 2.0)
# =====================================================
# Heartbeat -> gatebox (/api/rtsp/heartbeat)
# -----------------------------------------------------
# HEARTBEAT_URL можно задать явно (например: http://gatebox:8080/api/rtsp/heartbeat)
# Если не задан, соберём его из INFER_URL.
# CAMERA_ID — только для удобства в UI/логах.
# =====================================================
CAMERA_ID = env_str("CAMERA_ID", "cam1")
HB_EVERY_SEC = env_float("HB_EVERY_SEC", 1.0)
HEARTBEAT_URL = env_str("HEARTBEAT_URL", "")
if not HEARTBEAT_URL:
    base = _infer_base_url(INFER_URL)
    HEARTBEAT_URL = (base + "/api/rtsp/heartbeat") if base else ""

ROI_STR = env_str("ROI_STR", env_str("ROI", ""))

OCR_CROP_MODE = env_str("OCR_CROP_MODE", "yolo").lower()  # yolo | roi_fallback
SEND_ON_NO_DET = env_bool("SEND_ON_NO_DET", False)

DET_MODEL_PATH = env_str("DET_MODEL_PATH", "/models/plate_det.pt")
DET_CONF = env_float("DET_CONF", 0.35)
DET_IOU = env_float("DET_IOU", 0.45)
DET_IMG_SIZE = env_int("DET_IMG_SIZE", 640)
PLATE_PAD = env_float("PLATE_PAD", 0.08)

MIN_PLATE_W = env_int("MIN_PLATE_W", 80)   # in ROI pixels
MIN_PLATE_H = env_int("MIN_PLATE_H", 20)   # in ROI pixels

JPEG_QUALITY = env_int("JPEG_QUALITY", 90)
HTTP_TIMEOUT_SEC = env_float("HTTP_TIMEOUT_SEC", 2.0)

RECTIFY = env_bool("RECTIFY", False)
RECTIFY_W = env_int("RECTIFY_W", 320)
RECTIFY_H = env_int("RECTIFY_H", 96)

# Tracking
TRACK_ENABLE = env_bool("TRACK_ENABLE", True)
TRACK_HOLD_SEC = env_float("TRACK_HOLD_SEC", 1.0)
TRACK_ALPHA = env_float("TRACK_ALPHA", 0.65)     # EMA smoothing
TRACK_IOU_MIN = env_float("TRACK_IOU_MIN", 0.10) # bbox match threshold

# Event mode
EVENT_MODE = env_str("EVENT_MODE", "on_plate_change").lower()
PLATE_CONFIRM_K = env_int("PLATE_CONFIRM_K", 2)
PLATE_CONFIRM_WINDOW_SEC = env_float("PLATE_CONFIRM_WINDOW_SEC", 1.8)

# Throttling
GLOBAL_SEND_MIN_INTERVAL_SEC = env_float("GLOBAL_SEND_MIN_INTERVAL_SEC", 0.7)
PLATE_RESEND_SEC = env_float("PLATE_RESEND_SEC", 15.0)  # per-plate resend interval (0 disables)

# Debug saving
SAVE_DIR = env_str("SAVE_DIR", "/debug")
SAVE_EVERY = env_int("SAVE_EVERY", 0)            # 0 = disabled
SAVE_FULL_FRAME = env_int("SAVE_FULL_FRAME", 0)
SAVE_WITH_ROI = env_int("SAVE_WITH_ROI", 1)
LOG_EVERY_SEC = env_float("LOG_EVERY_SEC", 5.0)

# LIVE snapshot for UI (в общий volume /config/live)
# Обновляем раз в LIVE_EVERY_SEC (по умолчанию 1 сек) и
# сохраняем:
#   frame.jpg  - кадр (jpg)
#   meta.json  - {ts,w,h}
#   boxes.json - {ts,w,h,items:[{x1,y1,x2,y2,conf}]}
LIVE_DIR = env_str("LIVE_DIR", "/config/live")
LIVE_EVERY_SEC = env_float("LIVE_EVERY_SEC", 1.0)
LIVE_JPEG_QUALITY = env_int("LIVE_JPEG_QUALITY", 80)
# NEW: сохранять quad (поворотный контур номера) для UI-оверлея
LIVE_SAVE_QUAD = env_int("LIVE_SAVE_QUAD", 1) == 1

# RTSP options
RTSP_TRANSPORT = env_str("RTSP_TRANSPORT", "tcp").lower()
RTSP_OPEN_TIMEOUT_MS = env_int("RTSP_OPEN_TIMEOUT_MS", 8000)
RTSP_READ_TIMEOUT_MS = env_int("RTSP_READ_TIMEOUT_MS", 30000)

# NEW v0.3.2: сколько grab() сделать перед retrieve() для сброса буфера
RTSP_DRAIN_GRABS = env_int("RTSP_DRAIN_GRABS", 2)

# FIX v0.1.9: freeze watchdog
FREEZE_ENABLE = env_bool("FREEZE_ENABLE", True)
FREEZE_DIFF_MEAN_THR = env_float("FREEZE_DIFF_MEAN_THR", 0.35)
FREEZE_MAX_SEC = env_float("FREEZE_MAX_SEC", 3.0)
# FIX v0.3.0: переменная используется в логах и в grabber, должна быть определена
FREEZE_EVERY_N = env_int("FREEZE_EVERY_N", 3)

def _apply_opencv_ffmpeg_options() -> None:
    """Настраиваем OpenCV->FFmpeg опции для RTSP.

    Важно: это влияет на буферизацию/latency. Если переменная уже задана снаружи —
    не трогаем (уважаем явную настройку пользователя).
    """
    if os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS"):
        return
    stimeout_us = max(1, RTSP_OPEN_TIMEOUT_MS) * 1000
    rwtimeout_us = max(1, RTSP_READ_TIMEOUT_MS) * 1000
    transport = "tcp" if RTSP_TRANSPORT != "udp" else "udp"

    # CHG v0.3.2: добавляем low-latency флаги.
    # - fflags=nobuffer: минимизируем буфер демультиплексера
    # - flags=low_delay/max_delay=0: пытаемся снизить задержку на декодировании/очередях
    # - reorder_queue_size=0: отключаем reorder (актуально при B-frames)
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        f"rtsp_transport;{transport}|stimeout;{stimeout_us}|rw_timeout;{rwtimeout_us}"
        f"|fflags;nobuffer|flags;low_delay|max_delay;0|reorder_queue_size;0"
    )


def _open_capture(rtsp_url: str) -> cv2.VideoCapture:
    _apply_opencv_ffmpeg_options()
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

    # FIX v0.3.2: уменьшаем внутренний буфер захвата.
    # Не на всех сборках OpenCV это работает, но на многих заметно снижает grab_age_ms.
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    return cap


def _reopen(cap: cv2.VideoCapture) -> cv2.VideoCapture:
    try:
        cap.release()
    except Exception as e:
            # На Windows bind-mount иногда может быть RO/ошибки tmp-файлов.
            # Не спамим: печатаем не чаще раза в 10 секунд.
            now2 = time.time()
            if now2 - last_live_err > 10.0:
                print(f"[rtsp_worker] WARN: live write failed: {type(e).__name__}: {e}")
                last_live_err = now2

    time.sleep(0.4)
    cap2 = _open_capture(RTSP_URL)
    time.sleep(0.2)
    return cap2



# =========================================================
# NEW v0.3.0: FrameGrabber — отдельный поток чтения RTSP
# ---------------------------------------------------------
# Задача: держать последний кадр максимально свежим и НЕ
# блокироваться на YOLO/HTTP. Старые кадры выбрасываем —
# это убирает накопление задержки (2–3 минуты) при нагрузке.
# =========================================================
class FrameGrabber(threading.Thread):
    def __init__(self, rtsp_url: str, read_fps: float):
        super().__init__(daemon=True)
        self.rtsp_url = rtsp_url
        self.read_interval = 1.0 / max(1.0, float(read_fps))

        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._last_frame: Optional[np.ndarray] = None
        self._last_ts: float = 0.0

        # метрики
        self._frames = 0
        self._t0 = time.time()
        self._reopens = 0

        # freeze watchdog внутри grabber (дешевле и ближе к источнику)
        self._prev_small: Optional[np.ndarray] = None
        self._freeze_since: float = 0.0
        self._tick = 0

        self.running = True

    def _open(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = _open_capture(self.rtsp_url)
        self._reopens += 1

    def stop(self) -> None:
        self.running = False

    def get(self) -> Tuple[Optional[np.ndarray], float]:
        # Возвращаем ссылку на numpy array; мы НЕ мутируем старые объекты,
        # поэтому это безопасно (grabber просто подменяет ссылку).
        with self._lock:
            return self._last_frame, float(self._last_ts)

    def stats(self) -> Dict[str, float]:
        dt = max(1e-3, time.time() - self._t0)
        return {
            "read_fps_eff": float(self._frames) / dt,
            "reopens": float(self._reopens),
        }

    def _maybe_freeze_reopen(self, frame_bgr: np.ndarray, now: float) -> bool:
        """Возвращает True, если сделали reopen из-за freeze."""
        if not FREEZE_ENABLE:
            return False
        self._tick += 1
        if FREEZE_EVERY_N > 1 and (self._tick % int(FREEZE_EVERY_N) != 0):
            return False

        try:
            g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (160, 120), interpolation=cv2.INTER_AREA)
        except Exception:
            return False

        if self._prev_small is None or self._prev_small.shape != g.shape:
            self._prev_small = g
            self._freeze_since = 0.0
            return False

        dm = float(np.mean(cv2.absdiff(self._prev_small, g)))
        self._prev_small = g

        if dm <= FREEZE_DIFF_MEAN_THR:
            if self._freeze_since == 0.0:
                self._freeze_since = now
            elif (now - self._freeze_since) >= FREEZE_MAX_SEC:
                print(f"[rtsp_worker] WARN: grabber freeze (diff_mean={dm:.3f}) for {now-self._freeze_since:.1f}s -> reopen")
                self._freeze_since = 0.0
                self._open()
                time.sleep(0.2)
                return True
        else:
            self._freeze_since = 0.0

        return False

    def run(self) -> None:
        self._open()

        while self.running:
            t0 = time.time()
            now = t0

            if self._cap is None or not self._cap.isOpened():
                self._open()
                time.sleep(0.2)
                continue
            # CHG v0.3.2: вместо read() используем grab()+drain+retrieve().
            # Это позволяет выбрасывать старые кадры из очереди и брать максимально свежий.
            try:
                ok = self._cap.grab()
            except Exception:
                ok = False

            if not ok:
                self._open()
                time.sleep(0.2)
                continue

            # "drain" буфера (по умолчанию 2). Это дешево и сильно помогает от задержки.
            drain_n = max(0, int(RTSP_DRAIN_GRABS))
            for _ in range(drain_n):
                try:
                    self._cap.grab()
                except Exception:
                    break

            ok, frame = self._cap.retrieve()
            if not ok or frame is None:
                self._open()
                time.sleep(0.2)
                continue

            # freeze watchdog (по желанию)
            self._maybe_freeze_reopen(frame, now)

            with self._lock:
                self._last_frame = frame
                self._last_ts = now

            self._frames += 1

            dt = time.time() - t0
            if dt < self.read_interval:
                time.sleep(self.read_interval - dt)



# =========================================================
# NEW v0.3.3: FFmpegPipeGrabber — захват кадров через ffmpeg pipe
# ---------------------------------------------------------
# Почему: OpenCV VideoCapture на некоторых RTSP (особенно TCP+шум/снег)
# может накапливать задержку. ffmpeg-pipe с nobuffer/low_delay обычно
# держит latency стабильнее. Это НЕ отдельный сервис (не go2rtc) —
# просто subprocess внутри rtsp_worker.
# =========================================================
class FFmpegPipeGrabber(threading.Thread):
    def __init__(self, rtsp_url: str, transport: str, read_fps: float):
        super().__init__(daemon=True)
        self.rtsp_url = rtsp_url
        self.transport = "udp" if transport == "udp" else "tcp"
        self.read_interval = 1.0 / max(1.0, float(read_fps))

        self._lock = threading.Lock()
        self._last_frame: Optional[np.ndarray] = None
        self._last_ts: float = 0.0

        self._frames = 0
        self._t0 = time.time()
        self._restarts = 0

        self._proc: Optional[subprocess.Popen] = None
        self._w: int = 0
        self._h: int = 0
        self.running = True

    def stop(self) -> None:
        self.running = False
        self._kill_proc()

    def get(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            return self._last_frame, float(self._last_ts)

    def stats(self) -> Dict[str, float]:
        dt = max(1e-3, time.time() - self._t0)
        return {
            "read_fps_eff": float(self._frames) / dt,
            "restarts": float(self._restarts),
        }

    def _kill_proc(self) -> None:
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.kill()
        except Exception:
            pass

    def _probe_size(self) -> Tuple[int, int]:
        # Пытаемся узнать размеры через ffprobe (best-effort).
        if not FFMPEG_PROBE:
            return (0, 0)
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-rtsp_transport", self.transport,
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                self.rtsp_url,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
            if r.returncode != 0:
                return (0, 0)
            data = json.loads(r.stdout or "{}")
            streams = data.get("streams") or []
            if not streams:
                return (0, 0)
            w = int(streams[0].get("width") or 0)
            h = int(streams[0].get("height") or 0)
            return (w, h)
        except Exception:
            return (0, 0)

    def _start_proc(self, w: int, h: int) -> None:
        self._kill_proc()
        self._restarts += 1
        self._w, self._h = int(w), int(h)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", self.transport,
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-an",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-i", self.rtsp_url,
            "-pix_fmt", "bgr24",
            "-f", "rawvideo",
            "pipe:1",
        ]
        # Ограничиваем потоки декодера (mini-PC)
        if int(FFMPEG_THREADS) > 0:
            cmd.insert(1, "-threads")
            cmd.insert(2, str(int(FFMPEG_THREADS)))

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def _read_exact(self, n: int, timeout_sec: float) -> Optional[bytes]:
        p = self._proc
        if p is None or p.stdout is None:
            return None
        fd = p.stdout.fileno()
        buf = bytearray()
        deadline = time.time() + float(timeout_sec)

        while len(buf) < n:
            left = deadline - time.time()
            if left <= 0:
                return None
            try:
                import select
                r, _, _ = select.select([fd], [], [], min(0.2, left))
            except Exception:
                r = [fd]

            if not r:
                continue
            try:
                chunk = os.read(fd, n - len(buf))
            except Exception:
                return None
            if not chunk:
                return None
            buf.extend(chunk)

        return bytes(buf)

    def run(self) -> None:
        w, h = self._probe_size()
        if w <= 0 or h <= 0:
            # Если не смогли узнать размер — просто не стартуем (AutoGrabber откатит на OpenCV)
            self._w, self._h = 0, 0
        else:
            self._start_proc(w, h)

        while self.running:
            t0 = time.time()
            now = t0

            if self._proc is None or self._proc.stdout is None or self._w <= 0 or self._h <= 0:
                time.sleep(0.3)
                continue

            frame_bytes = int(self._w) * int(self._h) * 3
            b = self._read_exact(frame_bytes, float(FFMPEG_READ_TIMEOUT_SEC))
            if b is None:
                # timeout/EOF -> restart
                self._start_proc(self._w, self._h)
                time.sleep(0.2)
                continue

            frame = np.frombuffer(b, dtype=np.uint8).reshape((int(self._h), int(self._w), 3))

            with self._lock:
                self._last_frame = frame
                self._last_ts = now

            self._frames += 1

            dt = time.time() - t0
            if dt < self.read_interval:
                time.sleep(self.read_interval - dt)


# =========================================================
# NEW v0.3.3: AutoGrabber — авто-режим захвата кадров (продуктово)
# ---------------------------------------------------------
# Пользователь НЕ обязан выбирать backend. Мы стартуем на OpenCV,
# и если latency (grab_age_ms) часто высокий — переключаемся на
# ffmpeg-pipe. При деградации ffmpeg — откатываемся на OpenCV.
# =========================================================
class AutoGrabber:
    def __init__(self, rtsp_url: str, read_fps: float):
        self.rtsp_url = rtsp_url
        self.read_fps = float(read_fps)

        self._backend = "opencv"
        self._grabber = None  # type: ignore
        self._lock = threading.Lock()

        self._bad_streak = 0
        self._last_switch = 0.0

        self._mon = threading.Thread(target=self._monitor_loop, daemon=True)

    def start(self) -> None:
        backend = CAPTURE_BACKEND
        if backend not in ("auto", "opencv", "ffmpeg"):
            backend = "auto"

        if backend == "ffmpeg":
            if self._start_ffmpeg():
                self._backend = "ffmpeg"
            else:
                self._start_opencv()
        else:
            # auto/opencv -> сначала OpenCV
            self._start_opencv()

        self._mon.start()

    def stop(self) -> None:
        with self._lock:
            g = self._grabber
        try:
            if g is not None:
                g.stop()
        except Exception:
            pass

    def get(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            g = self._grabber
        if g is None:
            return None, 0.0
        return g.get()

    def stats(self) -> Dict[str, float]:
        with self._lock:
            g = self._grabber
            b = self._backend
        s = g.stats() if g is not None else {}
        s["backend_name"] = 1.0 if b == "ffmpeg" else 0.0
        return s

    def backend_name(self) -> str:
        with self._lock:
            return str(self._backend)

    def _start_opencv(self) -> None:
        g = FrameGrabber(self.rtsp_url, self.read_fps)
        g.start()
        with self._lock:
            old = self._grabber
            self._grabber = g
            self._backend = "opencv"
        try:
            if old is not None:
                old.stop()
        except Exception:
            pass

    def _start_ffmpeg(self) -> bool:
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=1.5)
        except Exception:
            return False

        g = FFmpegPipeGrabber(self.rtsp_url, RTSP_TRANSPORT, self.read_fps)
        g.start()

        # Ждём первый кадр чуть-чуть
        t0 = time.time()
        ok = False
        while time.time() - t0 < 2.0:
            fr, ts = g.get()
            if fr is not None and ts > 0:
                ok = True
                break
            time.sleep(0.05)

        if not ok:
            try:
                g.stop()
            except Exception:
                pass
            return False

        with self._lock:
            old = self._grabber
            self._grabber = g
            self._backend = "ffmpeg"
        try:
            if old is not None:
                old.stop()
        except Exception:
            pass
        return True

    def _monitor_loop(self) -> None:
        while True:
            time.sleep(max(0.2, float(AUTO_SWITCH_CHECK_SEC)))

            if CAPTURE_BACKEND != "auto":
                continue

            fr, ts = self.get()
            if ts <= 0:
                continue
            age_ms = (time.time() - float(ts)) * 1000.0

            with self._lock:
                backend = self._backend

            if backend == "opencv":
                if age_ms >= float(AUTO_SWITCH_AGE_MS):
                    self._bad_streak += 1
                else:
                    self._bad_streak = 0

                if self._bad_streak >= int(AUTO_SWITCH_STREAK):
                    now = time.time()
                    if now - self._last_switch > 10.0:
                        if self._start_ffmpeg():
                            print(f"[rtsp_worker] CHG: capture backend -> ffmpeg (age_ms={age_ms:.1f})")
                        self._last_switch = now
                        self._bad_streak = 0
            else:
                # ffmpeg: если деградировал — откат на OpenCV
                if age_ms >= float(AUTO_SWITCH_AGE_MS) * 2.5:
                    now = time.time()
                    if now - self._last_switch > 10.0:
                        self._start_opencv()
                        self._last_switch = now
                        print(f"[rtsp_worker] CHG: capture backend -> opencv (ffmpeg degraded age_ms={age_ms:.1f})")
def _frame_diff_mean(a: np.ndarray, b: np.ndarray) -> float:
    # cheap diff metric: mean absolute difference on small grayscale
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    ga = cv2.resize(ga, (160, 120), interpolation=cv2.INTER_AREA)
    gb = cv2.resize(gb, (160, 120), interpolation=cv2.INTER_AREA)
    d = cv2.absdiff(ga, gb)
    return float(np.mean(d))


# -----------------------------
# Simple track + event state
# -----------------------------
@dataclass
class TrackState:
    track_id: int = 0
    last_seen_ts: float = 0.0
    box: Optional[DetBox] = None


class PlateEventState:
    def __init__(self) -> None:
        self.last_sent_ts: float = 0.0
        self.last_sent_plate: str = ""
        self.per_plate_last_sent: Dict[str, float] = {}
        self.plate_hits: Dict[str, List[float]] = {}
        # last observed valid plate (even if not sent to mqtt due to cooldown)
        self.last_seen_plate: str = ""
        self.last_seen_ts: float = 0.0

    def _clean_hits(self, now: float) -> None:
        win = PLATE_CONFIRM_WINDOW_SEC
        for p in list(self.plate_hits.keys()):
            self.plate_hits[p] = [t for t in self.plate_hits[p] if now - t <= win]
            if not self.plate_hits[p]:
                del self.plate_hits[p]

    def note_plate(self, now: float, plate: str) -> int:
        self._clean_hits(now)
        self.plate_hits.setdefault(plate, []).append(now)
        return len(self.plate_hits[plate])

    def can_send_global(self, now: float) -> bool:
        return (now - self.last_sent_ts) >= max(0.0, GLOBAL_SEND_MIN_INTERVAL_SEC)

    def can_send_plate(self, now: float, plate: str) -> bool:
        if PLATE_RESEND_SEC <= 0:
            return True
        last = self.per_plate_last_sent.get(plate, 0.0)
        return (now - last) >= PLATE_RESEND_SEC

    def mark_sent(self, now: float, plate: str) -> None:
        self.last_sent_ts = now
        self.last_sent_plate = plate
        self.per_plate_last_sent[plate] = now

    def mark_seen(self, now: float, plate: str) -> None:
        self.last_seen_plate = plate
        self.last_seen_ts = now



def main() -> None:
    if not RTSP_URL:
        raise SystemExit("RTSP_URL is empty")

    print(f"[rtsp_worker] RTSP_URL={RTSP_URL}")
    print(f"[rtsp_worker] INFER_URL={INFER_URL}")
    print(f"[rtsp_worker] RTSP_FPS(legacy)={RTSP_FPS} READ_FPS={READ_FPS} DET_FPS={DET_FPS} SEND_FPS={SEND_FPS}")
    print(f"[rtsp_worker] ROI_STR={ROI_STR!r} OCR_CROP_MODE={OCR_CROP_MODE} SEND_ON_NO_DET={int(SEND_ON_NO_DET)}")
    print(f"[rtsp_worker] DET_MODEL_PATH={DET_MODEL_PATH}")
    print(f"[rtsp_worker] DET_CONF={DET_CONF} DET_IOU={DET_IOU} DET_IMG_SIZE={DET_IMG_SIZE} PAD={PLATE_PAD}")
    print(f"[rtsp_worker] MIN_PLATE_WxH={MIN_PLATE_W}x{MIN_PLATE_H}")
    print(f"[rtsp_worker] TRACK_ENABLE={int(TRACK_ENABLE)} HOLD={TRACK_HOLD_SEC}s ALPHA={TRACK_ALPHA} IOU_MIN={TRACK_IOU_MIN}")
    print(f"[rtsp_worker] EVENT_MODE={EVENT_MODE} CONFIRM_K={PLATE_CONFIRM_K} CONFIRM_WIN={PLATE_CONFIRM_WINDOW_SEC}")
    print(f"[rtsp_worker] THROTTLE global={GLOBAL_SEND_MIN_INTERVAL_SEC}s plate_resend={PLATE_RESEND_SEC}s")
    print(f"[rtsp_worker] RECTIFY={int(RECTIFY)} RECTIFY_WxH={RECTIFY_W}x{RECTIFY_H}")
    print(f"[rtsp_worker] SAVE_EVERY={SAVE_EVERY} SAVE_FULL_FRAME={SAVE_FULL_FRAME} SAVE_WITH_ROI={SAVE_WITH_ROI}")
    print(f"[rtsp_worker] FREEZE_ENABLE={int(FREEZE_ENABLE)} THR={FREEZE_DIFF_MEAN_THR} MAX_SEC={FREEZE_MAX_SEC} EVERY_N={FREEZE_EVERY_N}")

    ensure_dir(SAVE_DIR)
    ensure_dir(LIVE_DIR)

    detector = PlateDetector(DET_MODEL_PATH, conf=DET_CONF, iou_thr=DET_IOU, imgsz=DET_IMG_SIZE)

    # NEW v0.3.0: отдельный поток чтения RTSP
    # CHG v0.3.3: продуктовый захват кадров с авто-режимом (opencv->ffmpeg-pipe)
    grabber = AutoGrabber(RTSP_URL, READ_FPS)
    grabber.start()

    # ждём первый кадр, чтобы корректно вычислить ROI/размеры
    t_wait0 = time.time()
    frame0 = None
    frame0_ts = 0.0
    while frame0 is None:
        frame0, frame0_ts = grabber.get()
        if frame0 is None:
            if time.time() - t_wait0 > 12.0:
                raise SystemExit("cannot read first frame from RTSP (timeout)")
            time.sleep(0.05)

    h, w = frame0.shape[:2]
    roi = parse_roi(ROI_STR, w, h)
    print(f"[rtsp_worker] frame={w}x{h} ROI={roi}")

    track = TrackState(track_id=0, last_seen_ts=0.0, box=None)
    events = PlateEventState()

    # Таймеры (главное — DET_FPS/SEND_FPS)
    det_interval = 1.0 / max(0.1, float(DET_FPS))
    send_interval = 1.0 / max(0.1, float(SEND_FPS))

    next_det_ts = 0.0
    next_send_ts = 0.0

    # Кэш детекций, чтобы трек/оверлей жил между YOLO
    last_dets_roi: List[DetBox] = []
    last_det_frame_ts: float = 0.0
    last_det_ms: float = 0.0
    last_post_ms: float = 0.0

    # Счётчики для eff FPS
    t0_stats = time.time()
    det_count = 0
    send_count = 0

    # Heartbeat stats
    hb_last = 0.0
    hb_window_t0 = time.time()
    hb_frames = 0
    read_errors = 0

    # LIVE snapshot / debug
    last_live_write = 0.0
    last_log = 0.0
    tick = 0
    sent = 0

    while True:
        t_loop = time.time()

        frame, frame_ts = grabber.get()
        # FIX v0.3.4: если grabber ещё не отдал НОВЫЙ кадр, не крутим тяжёлый цикл впустую.
        # Это ключевое для мини-ПК: иначе можно получить 500–800% CPU при READ_FPS=12.
        if not hasattr(main, "_last_frame_ts"):  # type: ignore[attr-defined]
            main._last_frame_ts = 0.0  # type: ignore[attr-defined]
        if frame_ts == getattr(main, "_last_frame_ts"):  # type: ignore[attr-defined]
            time.sleep(0.005)
            continue
        main._last_frame_ts = float(frame_ts)  # type: ignore[attr-defined]

        if frame is None or frame_ts <= 0:
            read_errors += 1
            time.sleep(0.05)
            continue

        now = time.time()
        hb_frames += 1

        grab_age_ms = (now - float(frame_ts)) * 1000.0

        # Если кадр слишком старый — считаем поток "подвисшим" для UI
        frozen_now = bool(grab_age_ms >= (FREEZE_MAX_SEC * 1000.0))
        note = "ok" if not frozen_now else "stale_frame"

        fh, fw = frame.shape[:2]
        if (fw, fh) != (w, h):
            w, h = fw, fh
            roi = parse_roi(ROI_STR, w, h)
            print(f"[rtsp_worker] resized stream => frame={w}x{h} ROI={roi}")

        x1, y1, x2, y2 = roi
        roi_frame = frame[y1:y2, x1:x2]
        if roi_frame.size == 0:
            time.sleep(0.02)
            continue

        # -------------------------------------------------
        # DETECTION (YOLO) по таймеру
        # -------------------------------------------------
        dets_roi: List[DetBox] = []
        if (now >= next_det_ts) or (track.box is None):
            next_det_ts = now + det_interval
            td0 = time.time()
            dets_roi = detector.detect(roi_frame)
            last_det_ms = (time.time() - td0) * 1000.0
            last_dets_roi = dets_roi
            last_det_frame_ts = float(frame_ts)
            det_count += 1
        else:
            # Если детекция "слишком старая" относительно текущего кадра — лучше сбросить
            if last_det_frame_ts > 0 and (float(frame_ts) - last_det_frame_ts) <= max(0.1, TRACK_HOLD_SEC * 2.0):
                dets_roi = last_dets_roi
            else:
                dets_roi = []

        det_cnt = len(dets_roi)

        best_roi: Optional[DetBox] = None
        if dets_roi:
            cand = dets_roi[0]
            if cand.w() >= MIN_PLATE_W and cand.h() >= MIN_PLATE_H:
                best_roi = cand

        # -------------------------------------------------
        # TRACKING (лёгкий) — живёт между YOLO
        # -------------------------------------------------
        track_new = False
        best_full: Optional[DetBox] = None

        if best_roi is not None:
            cur_full = DetBox(
                x1=best_roi.x1 + x1,
                y1=best_roi.y1 + y1,
                x2=best_roi.x2 + x1,
                y2=best_roi.y2 + y1,
                conf=best_roi.conf,
            )

            if TRACK_ENABLE and track.box is not None and (now - track.last_seen_ts) <= TRACK_HOLD_SEC:
                if iou(track.box, cur_full) >= TRACK_IOU_MIN:
                    track.box = smooth_box(track.box, cur_full, TRACK_ALPHA)
                    track.last_seen_ts = now
                    best_full = track.box
                else:
                    track.track_id += 1
                    track_new = True
                    track.box = cur_full
                    track.last_seen_ts = now
                    best_full = track.box
            else:
                track.track_id += 1
                track_new = True
                track.box = cur_full
                track.last_seen_ts = now
                best_full = track.box
        else:
            if track.box is not None and (now - track.last_seen_ts) > TRACK_HOLD_SEC:
                track.box = None

        if best_full is None and track.box is not None and (now - track.last_seen_ts) <= TRACK_HOLD_SEC:
            best_full = track.box

        # -------------------------------------------------
        # LIVE snapshot for UI
        # -------------------------------------------------
        if LIVE_EVERY_SEC > 0 and (now - last_live_write) >= LIVE_EVERY_SEC:
            items = []
            for d in dets_roi:
                items.append(
                    {
                        "x1": int(d.x1 + x1),
                        "y1": int(d.y1 + y1),
                        "x2": int(d.x2 + x1),
                        "y2": int(d.y2 + y1),
                        "conf": float(d.conf),
                    }
                )

            quad = None
            # Важно: вычисление quad/rectify — тяжёлое. Поэтому делаем ТОЛЬКО если явно включили RECTIFY.
            if LIVE_SAVE_QUAD and RECTIFY and best_full is not None:
                try:
                    ex1, ey1, ex2, ey2 = expand_box(best_full.x1, best_full.y1, best_full.x2, best_full.y2, PLATE_PAD, w, h)
                    crop_live = frame[ey1:ey2, ex1:ex2]
                    if crop_live.size > 0:
                        _warped_live, quad_crop = rectify_plate_quad(crop_live, out_w=RECTIFY_W, out_h=RECTIFY_H)
                        if quad_crop is not None and getattr(quad_crop, "size", 0) >= 8:
                            qc = quad_crop.astype(np.float32)
                            qc[:, 0] += float(ex1)
                            qc[:, 1] += float(ey1)
                            quad = qc.astype(int).tolist()
                except Exception:
                    quad = None

            try:
                ok_jpg, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(LIVE_JPEG_QUALITY)])
                if ok_jpg:
                    atomic_write_bytes(os.path.join(LIVE_DIR, "frame.jpg"), bytes(buf))
                atomic_write_json(os.path.join(LIVE_DIR, "meta.json"), {"ts": now, "w": w, "h": h, "camera_id": CAMERA_ID})
                atomic_write_json(
                    os.path.join(LIVE_DIR, "boxes.json"),
                    {"ts": now, "w": w, "h": h, "items": items, "roi": [x1, y1, x2, y2], "quad": quad},
                )
            except Exception:
                pass

            last_live_write = now

        # -------------------------------------------------
        # Выбор crop для OCR (лёгкий)
        # -------------------------------------------------
        crop_to_send: Optional[np.ndarray] = None
        crop_dbg: Optional[np.ndarray] = None
        rect_dbg: Optional[np.ndarray] = None
        rectify_ms: float | None = None

        if best_full is not None:
            ex1, ey1, ex2, ey2 = expand_box(best_full.x1, best_full.y1, best_full.x2, best_full.y2, PLATE_PAD, w, h)
            crop = frame[ey1:ey2, ex1:ex2]
            if crop.size > 0:
                crop_dbg = crop
                # ВАЖНО: rectify по умолчанию выключен и должен жить в gatebox (адаптивно).
                # Здесь — только если вручную включили RECTIFY для экспериментов.
                if RECTIFY:
                    t_rect0 = time.time()
                    rect = rectify_plate(crop, RECTIFY_W, RECTIFY_H)
                    rectify_ms = (time.time() - t_rect0) * 1000.0
                    if rect is not None and rect.size > 0:
                        rect_dbg = rect
                        crop_to_send = rect
                    else:
                        crop_to_send = crop
                else:
                    crop_to_send = crop

        if crop_to_send is None:
            if OCR_CROP_MODE == "roi_fallback":
                crop_to_send = roi_frame
            elif OCR_CROP_MODE == "yolo" and SEND_ON_NO_DET:
                crop_to_send = roi_frame

        # -------------------------------------------------
        # Решение "надо ли слать" + SEND_FPS
        # -------------------------------------------------
        want_send = False
        if crop_to_send is not None and crop_to_send.size > 0:
            if EVENT_MODE == "always":
                want_send = True
            elif EVENT_MODE == "on_new_track":
                want_send = bool(track_new)
            elif EVENT_MODE in ("on_plate_change", "on_plate_confirmed"):
                if track_new:
                    want_send = True
                else:
                    last = events.last_seen_plate
                    if not last:
                        want_send = True
                    else:
                        # если plate уже известен и resend ещё рано — не долбим OCR
                        want_send = bool(events.can_send_plate(now, last))
            else:
                want_send = False

        # глобальный троттлинг (старый, совместимость)
        if want_send and not events.can_send_global(now):
            want_send = False

        # NEW v0.3.0: частотный лимит отправки (чтобы стабилизировать latency и CPU)
        if want_send and now < next_send_ts:
            want_send = False

        resp = None
        if want_send:
            next_send_ts = now + send_interval
            try:
                tp0 = time.time()

                # meta для gatebox (variant/warped/timing)
                pre_variant = "rectify" if (RECTIFY and rect_dbg is not None and crop_to_send is not None and crop_to_send is rect_dbg) else "crop"
                pre_warped = bool(pre_variant == "rectify")
                pre_timing = {}
                if rectify_ms is not None:
                    pre_timing["rectify_ms"] = round(float(rectify_ms), 2)

                resp = post_crop(
                    INFER_URL,
                    crop_to_send,
                    timeout_sec=HTTP_TIMEOUT_SEC,
                    jpeg_quality=JPEG_QUALITY,
                    pre_variant=pre_variant,
                    pre_warped=pre_warped,
                    pre_timing=pre_timing,
                )
                last_post_ms = (time.time() - tp0) * 1000.0
            except Exception as e:
                resp = {"ok": False, "reason": f"http_error: {e}"}

            sent += 1
            send_count += 1

            # В режиме разработки можно включить PRINT_EVERY_RESPONSE в gatebox.
            # Здесь оставляем один принт на ответ, чтобы видеть, что pipeline жив.
            if WORKER_DEBUG or (not (isinstance(resp, dict) and resp.get("log_level") == "debug")):
                print(f"[infer] {resp}")

            plate = ""
            valid = False
            if isinstance(resp, dict):
                plate = str(resp.get("plate", "") or "")
                valid = bool(resp.get("valid", False))

            if plate and valid:
                events.mark_seen(now, plate)

            if EVENT_MODE == "on_plate_change":
                if plate and valid:
                    if plate != events.last_sent_plate:
                        events.mark_sent(now, plate)
                    else:
                        if events.can_send_plate(now, plate):
                            events.mark_sent(now, plate)

            elif EVENT_MODE == "on_plate_confirmed":
                if plate and valid:
                    hits = events.note_plate(now, plate)
                    if hits >= PLATE_CONFIRM_K and events.can_send_plate(now, plate):
                        events.mark_sent(now, plate)

        # -------------------------------------------------
        # Heartbeat -> gatebox (UI)
        # -------------------------------------------------
        if HB_EVERY_SEC > 0 and (now - hb_last) >= HB_EVERY_SEC:
            dt_win = max(0.001, now - hb_window_t0)
            fps_est = float(hb_frames) / float(dt_win)
            hb_window_t0 = now
            hb_frames = 0

            st = grabber.stats()
            dt_stats = max(1e-3, now - t0_stats)
            det_fps_eff = float(det_count) / dt_stats
            send_fps_eff = float(send_count) / dt_stats

            _post_heartbeat(
                HEARTBEAT_URL,
                {
                    "ts": now,
                    "frame_ts": float(frame_ts),
                    "alive": True,
                    "frozen": frozen_now,
                    "note": note,
                    "camera_id": CAMERA_ID,
                    "fps": round(float(fps_est), 3),
                    "errors": int(read_errors),
                    "sent": int(sent),
                    "frame_w": int(w),
                    "frame_h": int(h),
                    "roi": list(roi),
                    # NEW: diag
                    "grab_age_ms": round(float(grab_age_ms), 1),
                    "read_fps_eff": round(float(st.get("read_fps_eff", 0.0)), 2),
                    "det_fps_eff": round(float(det_fps_eff), 2),
                    "send_fps_eff": round(float(send_fps_eff), 2),
                    "last_det_ms": round(float(last_det_ms), 2),
                    "last_post_ms": round(float(last_post_ms), 2),
                },
                timeout_sec=1.0,
            )
            hb_last = now

        # -------------------------------------------------
        # Debug save (только если включено)
        # -------------------------------------------------
        if SAVE_EVERY > 0 and (tick % int(SAVE_EVERY) == 0):
            ts = int(time.time())
            if SAVE_FULL_FRAME:
                cv2.imwrite(os.path.join(SAVE_DIR, f"frame_{ts}_{sent}_{tick}.jpg"), frame)

            if SAVE_WITH_ROI:
                vis = frame.copy()
                draw_roi(vis, roi, color=(0, 255, 0), thickness=2)
                if best_full is not None:
                    draw_box(vis, best_full, color=(0, 255, 255), thickness=2)
                cv2.imwrite(os.path.join(SAVE_DIR, f"frame_roi_{ts}_{sent}_{tick}.jpg"), vis)

            cv2.imwrite(os.path.join(SAVE_DIR, f"roi_{ts}_{sent}_{tick}.jpg"), roi_frame)

            if crop_dbg is not None:
                cv2.imwrite(os.path.join(SAVE_DIR, f"crop_{ts}_{sent}_{tick}.jpg"), crop_dbg)
            if rect_dbg is not None:
                cv2.imwrite(os.path.join(SAVE_DIR, f"rectify_{ts}_{sent}_{tick}.jpg"), rect_dbg)

        # Alive log (раз в LOG_EVERY_SEC)
        if now - last_log >= LOG_EVERY_SEC:
            best_conf = best_roi.conf if best_roi is not None else None
            best_conf_str = "-" if best_conf is None else f"{best_conf:.2f}"
            trk = track.track_id if track.box is not None else 0
            last_seen = events.last_seen_plate or "-"
            last_sent = events.last_sent_plate or "-"
            print(
                f"[rtsp_worker] alive: frame={w}x{h} roi={roi} det={det_cnt} best={best_conf_str} "
                f"track={trk} track_new={int(track_new)} sent={sent} seen={last_seen} sent_plate={last_sent} "
                f"grab_age_ms={grab_age_ms:.1f}"
            )
            last_log = now

        tick += 1
        # лёгкий сон, чтобы не крутить 100% CPU на пустом цикле
        dt = time.time() - t_loop
        sleep_t = 0.01 - dt
        if sleep_t > 0:
            time.sleep(sleep_t)


if __name__ == "__main__":
    main()
