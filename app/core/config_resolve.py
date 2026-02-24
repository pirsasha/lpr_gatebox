# =========================================================
# Файл: app/core/config_resolve.py
# Проект: LPR GateBox
# Версия: v0.1
# Изменено: 2026-02-18 (UTC+3)
# Автор: Codex
# ---------------------------------------------------------
# Что сделано:
# - NEW: Единый резолвер runtime-конфига с приоритетом cfg -> env -> default.
# - NEW: Источник значения (CFG|ENV|DEFAULT) + безопасное описание secret-значений.
# - FIX: Пустые строки трактуются как отсутствие значения.
# =========================================================

from __future__ import annotations

import os
from typing import Any, Iterable, Tuple


def _path_get(cfg: dict[str, Any] | None, path: str) -> Any:
    cur: Any = cfg if isinstance(cfg, dict) else {}
    for part in str(path or "").split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _is_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _to_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or "").strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off", ""):
        return False
    return default


def _pick(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: Any) -> Tuple[Any, str]:
    cfg_v = _path_get(cfg, path)
    if not _is_empty(cfg_v):
        return cfg_v, "CFG"

    if env_key:
        env_v = os.environ.get(env_key)
        if not _is_empty(env_v):
            return env_v, "ENV"

    return default, "DEFAULT"


def get_str(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: str = "") -> str:
    v, _ = _pick(cfg, path, env_key, default)
    return str(v or "").strip()


def get_str_src(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: str = "") -> Tuple[str, str]:
    v, src = _pick(cfg, path, env_key, default)
    return str(v or "").strip(), src


def get_int(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: int = 0) -> int:
    v, _ = _pick(cfg, path, env_key, default)
    try:
        return int(float(v))
    except Exception:
        return int(default)


def get_int_src(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: int = 0) -> Tuple[int, str]:
    v, src = _pick(cfg, path, env_key, default)
    try:
        return int(float(v)), src
    except Exception:
        return int(default), "DEFAULT"


def get_float(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: float = 0.0) -> float:
    v, _ = _pick(cfg, path, env_key, default)
    try:
        return float(v)
    except Exception:
        return float(default)


def get_float_src(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: float = 0.0) -> Tuple[float, str]:
    v, src = _pick(cfg, path, env_key, default)
    try:
        return float(v), src
    except Exception:
        return float(default), "DEFAULT"


def get_bool(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: bool = False) -> bool:
    v, _ = _pick(cfg, path, env_key, default)
    return _to_bool(v, default=default)


def get_bool_src(cfg: dict[str, Any] | None, path: str, env_key: str | None, default: bool = False) -> Tuple[bool, str]:
    v, src = _pick(cfg, path, env_key, default)
    return _to_bool(v, default=default), src


def describe_secret(v: str) -> str:
    s = str(v or "")
    if not s:
        return "len=0 tail4=----"
    return f"len={len(s)} tail4={s[-4:]}"
