# =========================================================
# Файл: app/store.py
# Проект: LPR GateBox
# Версия: v0.3.1
# Изменено: 2026-02-06 20:30 (UTC+3)
# Автор: Александр
# Что сделано:
# - NEW: EventItem.level ("info"/"debug") + meta (diagnostics: timing_ms/variant/warped)
# - NEW: EventStore.latest(..., include_debug) — по умолчанию скрывает debug-события (мусор OCR)
# - CHG: to_dict() сохраняет обратную совместимость полей (ts/plate/raw/conf/status/message)
# =========================================================
from __future__ import annotations

from dataclasses import dataclass, asdict
from collections import deque
from threading import Lock
from typing import Any, Deque, Dict, List, Optional
import json
import os
import tempfile


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _to_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _to_str(x: Any, default: str = "") -> str:
    if x is None:
        return default
    try:
        return str(x)
    except Exception:
        return default


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """Рекурсивный merge словарей: src поверх dst."""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)  # type: ignore[index]
        else:
            dst[k] = v
    return dst


@dataclass
class EventItem:
    ts: float
    plate: str
    raw: Optional[str] = None
    conf: Optional[float] = None
    status: str = "info"
    message: str = ""
    level: str = "info"
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # гарантируем только примитивы (FastAPI/JSON)
        d["ts"] = float(d.get("ts") or 0.0)
        if d.get("conf") is not None:
            d["conf"] = float(d["conf"])
        if d.get("raw") is not None:
            d["raw"] = str(d["raw"])
        d["plate"] = str(d.get("plate") or "")
        d["status"] = str(d.get("status") or "info")
        d["message"] = str(d.get("message") or "")
        d["level"] = str(d.get("level") or "info")
        d["meta"] = d.get("meta") if isinstance(d.get("meta"), dict) else None
        return d


class EventStore:
    def __init__(self, maxlen: int = 200):
        self._lock = Lock()
        self._items: Deque[EventItem] = deque(maxlen=maxlen)

    def add(self, item: EventItem) -> None:
        with self._lock:
            self._items.appendleft(item)

    def latest(self, limit: int = 50, after_ts: Optional[float] = None, include_debug: bool = False) -> List[Dict[str, Any]]:
        limit = max(1, min(500, _to_int(limit, 50)))
        after = _to_float(after_ts)

        with self._lock:
            items = list(self._items)

        out: List[Dict[str, Any]] = []
        for it in items:
            if after is not None and float(it.ts) <= after:
                continue
            # CHG: "мусор" держим в debug, по умолчанию скрываем из UI
            if not include_debug and str(getattr(it, "level", "info")) == "debug":
                continue
            out.append(it.to_dict())
            if len(out) >= limit:
                break
        return out

    def count(self) -> int:
        with self._lock:
            return len(self._items)


class SettingsStore:
    """Хранилище настроек: settings.json.

    - При старте: если файла нет → создаём из defaults
    - update(patch): merge + save
    - reload(): перечитать с диска
    - get(): копия настроек
    """

    def __init__(self, path: str, defaults: Dict[str, Any]):
        self.path = path
        self._lock = Lock()
        self._defaults = defaults or {}
        self._data: Dict[str, Any] = {}
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if not os.path.exists(self.path):
            self._data = json.loads(json.dumps(self._defaults))
            self._atomic_write(self._data)
            return
        self._data = self._read_file() or json.loads(json.dumps(self._defaults))

    def _read_file(self) -> Optional[Dict[str, Any]]:
        try:
            raw = open(self.path, "r", encoding="utf-8").read()
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        # атомарно: пишем во временный файл и заменяем
        tmp_dir = os.path.dirname(self.path) or "."
        fd, tmp_path = tempfile.mkstemp(prefix="settings_", suffix=".json", dir=tmp_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def reload(self) -> Dict[str, Any]:
        with self._lock:
            data = self._read_file()
            if isinstance(data, dict):
                self._data = data
            else:
                # если файл битый — не роняем сервис, держим предыдущую копию
                pass
            return json.loads(json.dumps(self._data))

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            patch = {}
        with self._lock:
            base = json.loads(json.dumps(self._data))
            _deep_merge(base, patch)
            self._data = base
            self._atomic_write(self._data)
            return json.loads(json.dumps(self._data))

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self._data = json.loads(json.dumps(self._defaults))
            self._atomic_write(self._data)
            return json.loads(json.dumps(self._data))
