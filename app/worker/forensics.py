# =========================================================
# Файл: app/worker/forensics.py
# Проект: LPR GateBox
# Версия: v0.3.7-all-in-one-split
# =========================================================

from __future__ import annotations

import os
import json


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
