"""OCR ONNX helper."""

# =========================================================
# Файл: app/ocr_onnx.py
# Проект: LPR GateBox
# Версия: v0.2.4
# Изменено: 2026-02-04 21:10 (UTC+3)
# Автор: Александр
# Что сделано:
# - NEW: добавлен infer_bgr(img_bgr) чтобы вызывать OCR без повторного decode/encode
# =========================================================

import os
import numpy as np
import cv2
import onnxruntime as ort

ALPHABET = os.environ.get("ALPHABET", "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
I2CH = [""] + list(ALPHABET)  # 0 blank

H = int(os.environ.get("H", "32"))
W = int(os.environ.get("W", "256"))

def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=axis, keepdims=True)

class OnnxOcr:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.in_name = self.sess.get_inputs()[0].name
        self.out_name = self.sess.get_outputs()[0].name

    def _preprocess_bgr(self, img_bgr) -> np.ndarray:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        scale = H / max(h, 1)
        new_w = max(1, int(w * scale))
        resized = cv2.resize(gray, (new_w, H), interpolation=cv2.INTER_CUBIC)

        if new_w < W:
            pad = np.full((H, W - new_w), 255, dtype=np.uint8)
            x = np.concatenate([resized, pad], axis=1)
        else:
            x = resized[:, :W]

        x = x.astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        x = x[None, None, :, :]  # (1,1,H,W)
        return np.ascontiguousarray(x)

    def infer_bytes(self, image_bytes: bytes):
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("cannot decode image")

        return self.infer_bgr(img)

    # NEW: без повторного decode/encode (удобно для multi-try ориентации/warp)
    def infer_bgr(self, img_bgr: np.ndarray):
        x = self._preprocess_bgr(img_bgr)
        logits = self.sess.run([self.out_name], {self.in_name: x})[0]  # (1,T,C)
        logits = logits[0]  # (T,C)
        probs = _softmax(logits, axis=1)
        pred_idx = probs.argmax(axis=1)
        pred_conf = probs.max(axis=1)

        # CTC greedy collapse + confidence per char
        out = []
        confs = []
        prev = -1
        for idx, c in zip(pred_idx.tolist(), pred_conf.tolist()):
            if idx == prev:
                continue
            prev = idx
            if idx == 0:
                continue
            out.append(I2CH[idx])
            confs.append(float(c))

        raw = "".join(out)
        avg_conf = float(sum(confs) / max(len(confs), 1))
        return raw, avg_conf
