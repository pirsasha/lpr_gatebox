from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Tuple

TUNING_ENV_KEYS = {
    "MIN_CONF", "CONFIRM_N", "CONFIRM_WINDOW_SEC", "COOLDOWN_SEC", "REGION_CHECK", "REGION_STAB",
    "REGION_STAB_WINDOW_SEC", "REGION_STAB_MIN_HITS", "REGION_STAB_MIN_RATIO",
    "DET_CONF", "DET_IOU", "DET_IMG_SIZE", "PLATE_PAD", "PLATE_PAD_BASE", "PLATE_PAD_SMALL", "PLATE_PAD_MAX",
    "RECTIFY", "RECTIFY_W", "RECTIFY_H", "REFINE_INNER_PAD", "UPSCALE_ENABLE", "UPSCALE_MIN_W", "UPSCALE_MIN_H",
    "MIN_PLATE_W", "MIN_PLATE_H", "OCR_WARP_TRY", "OCR_WARP_W", "OCR_WARP_H", "POSTCROP", "POSTCROP_LRBT",
}


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def default_settings_v2() -> Dict[str, Any]:
    return {
        "version": 2,
        "revision": 1,
        "active_profile": "day",
        "profiles": {
            "day": {
                "gate": {"min_conf": 0.75, "confirm_n": 2, "confirm_window_sec": 2.0, "cooldown_sec": 15.0},
                "ocr": {"orient_try": True, "warp_try": True, "warp_w": 320, "warp_h": 96, "postcrop": True, "postcrop_lrbt": [0.02, 0.02, 0.05, 0.05]},
                "rtsp_worker": {"det_conf": 0.35, "det_iou": 0.45, "det_img_size": 960, "rectify": True, "rectify_w": 384, "rectify_h": 128},
            },
            "night": {},
            "custom": {},
        },
        "camera": {"enabled": True, "rtsp_url": "", "roi": "", "roi_poly": ""},
        "system": {
            "mqtt": {"enabled": True, "host": "", "port": 1883, "user": "", "pass": "", "topic": "gate/open"},
            "telegram": {"enabled": False, "bot_token": "", "chat_id": "", "thread_id": ""},
            "cloudpub": {"enabled": False, "server_ip": "", "access_key": "", "auto_expire_min": 0},
            "paths": {"model_path": "", "settings_path": "", "infer_url": "", "capture_backend": "auto", "save_dir": ""},
        },
        "ui": {"draft": {}, "language": "ru"},
    }


def migrate_to_v2(data: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, list[str]]:
    notes: list[str] = []
    if not isinstance(data, dict):
        return default_settings_v2(), True, ["invalid_source_replaced_with_default"]
    if int(data.get("version") or 1) == 2:
        return data, False, notes

    out = default_settings_v2()
    notes.append("migrate_v1_to_v2")

    gate = data.get("gate") if isinstance(data.get("gate"), dict) else {}
    ocr = data.get("ocr") if isinstance(data.get("ocr"), dict) else {}
    camera = data.get("camera") if isinstance(data.get("camera"), dict) else {}
    rtsp = data.get("rtsp_worker") if isinstance(data.get("rtsp_worker"), dict) else {}
    overrides = rtsp.get("overrides") if isinstance(rtsp.get("overrides"), dict) else {}

    out["profiles"]["day"]["gate"] = {
        "min_conf": float(gate.get("min_conf", 0.75)),
        "confirm_n": int(gate.get("confirm_n", 2)),
        "confirm_window_sec": float(gate.get("confirm_window_sec", 2.0)),
        "cooldown_sec": float(gate.get("cooldown_sec", 15.0)),
    }
    out["profiles"]["day"]["ocr"] = {
        "orient_try": bool(ocr.get("orient_try", True)),
        "warp_try": bool(ocr.get("warp_try", True)),
        "warp_w": int(ocr.get("warp_w", 320)),
        "warp_h": int(ocr.get("warp_h", 96)),
        "postcrop": bool(ocr.get("postcrop", True)),
        "postcrop_lrbt": ocr.get("postcrop_lrbt", [0.02, 0.02, 0.05, 0.05]),
    }
    out["profiles"]["day"]["rtsp_worker"] = {
        "det_conf": float(overrides.get("DET_CONF", 0.35)),
        "det_iou": float(overrides.get("DET_IOU", 0.45)),
        "det_img_size": int(overrides.get("DET_IMG_SIZE", 960)),
        "rectify": str(overrides.get("RECTIFY", "1")).lower() not in ("0", "false", "off"),
        "rectify_w": int(overrides.get("RECTIFY_W", 384)),
        "rectify_h": int(overrides.get("RECTIFY_H", 128)),
    }

    if isinstance(camera, dict):
        _deep_merge(out["camera"], camera)

    mqtt = data.get("mqtt") if isinstance(data.get("mqtt"), dict) else {}
    telegram = data.get("telegram") if isinstance(data.get("telegram"), dict) else {}
    cloudpub = data.get("cloudpub") if isinstance(data.get("cloudpub"), dict) else {}

    _deep_merge(out["system"]["mqtt"], mqtt)
    _deep_merge(out["system"]["telegram"], telegram)
    _deep_merge(out["system"]["cloudpub"], cloudpub)

    return out, True, notes


def effective_config(settings: Dict[str, Any]) -> Dict[str, Any]:
    cfg = settings if isinstance(settings, dict) else {}
    active = str(cfg.get("active_profile") or "day")
    profiles = cfg.get("profiles") if isinstance(cfg.get("profiles"), dict) else {}
    base = copy.deepcopy(default_settings_v2())

    for profile_name in ("day", active):
        profile = profiles.get(profile_name)
        if isinstance(profile, dict):
            _deep_merge(base, {"profiles": {profile_name: profile}})

    # flatten effective runtime sections
    effective = {
        "version": int(cfg.get("version") or 2),
        "revision": int(cfg.get("revision") or 1),
        "active_profile": active,
        "camera": copy.deepcopy(cfg.get("camera") or {}),
        "system": copy.deepcopy(cfg.get("system") or {}),
        "gate": copy.deepcopy((profiles.get(active) or {}).get("gate") if isinstance(profiles.get(active), dict) else (profiles.get("day") or {}).get("gate") or {}),
        "ocr": copy.deepcopy((profiles.get(active) or {}).get("ocr") if isinstance(profiles.get(active), dict) else (profiles.get("day") or {}).get("ocr") or {}),
        "rtsp_worker": copy.deepcopy((profiles.get(active) or {}).get("rtsp_worker") if isinstance(profiles.get(active), dict) else (profiles.get("day") or {}).get("rtsp_worker") or {}),
    }

    ignored_env = [k for k in sorted(TUNING_ENV_KEYS) if os.environ.get(k) not in (None, "")]
    return {
        "effective": effective,
        "source": {
            "runtime": "settings.json(v2)",
            "fallback": "defaults",
            "system_env_lock": [
                k for k in ("MODEL_PATH", "SETTINGS_PATH", "INFER_URL", "CAPTURE_BACKEND", "SAVE_DIR", "WHITELIST_PATH") if os.environ.get(k)
            ],
            "ignored_tuning_env": ignored_env,
        },
    }


def backup_file(path: str) -> str:
    src = Path(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = src.with_suffix(src.suffix + f".v1.bak.{stamp}")
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(dst)


def dump_json(path: str, data: Dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
