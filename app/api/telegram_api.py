# =========================================================
# Файл: app/api/telegram_api.py
# Проект: LPR GateBox
# Версия: v0.3.4
# Изменено: 2026-02-08
# Что сделано:
# - NEW: API для теста Telegram уведомлений
# =========================================================

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.integrations.telegram.client import TelegramClient

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


class TgTestReq(BaseModel):
    text: str = "✅ GateBox: тестовое уведомление"
    with_photo: bool = True


# Эти ссылки установим из main.py (без циклических импортов)
_TG: Dict[str, Any] = {"get_cfg": None, "enqueue": None, "pick_photo": None}


def set_telegram_hooks(get_cfg, enqueue, pick_photo):
    _TG["get_cfg"] = get_cfg
    _TG["enqueue"] = enqueue
    _TG["pick_photo"] = pick_photo


@router.post("/test")
def telegram_test(req: TgTestReq):
    if not _TG["get_cfg"] or not _TG["enqueue"]:
        return {"ok": False, "error": "telegram_not_initialized"}

    cfg = _TG["get_cfg"]()
    tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    tg = tg if isinstance(tg, dict) else {}

    if not tg.get("enabled"):
        return {"ok": False, "error": "telegram_disabled"}

    chat_id = str(tg.get("chat_id") or "").strip()
    if not chat_id:
        return {"ok": False, "error": "telegram_not_paired"}

    photo_path: Optional[str] = None
    if req.with_photo and _TG["pick_photo"]:
        photo_path = _TG["pick_photo"](cfg)

    _TG["enqueue"](req.text, photo_path)
    return {"ok": True, "queued": True, "with_photo": bool(photo_path)}

@router.get("/bot_info")
def telegram_bot_info():
    if not _TG["get_cfg"]:
        return {"ok": False, "error": "telegram_not_initialized"}

    cfg = _TG["get_cfg"]()
    tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    tg = tg if isinstance(tg, dict) else {}

    token = str(tg.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "error": "no_token"}

    try:
        cli = TelegramClient(token=token)
        js = cli.get_me()
        res = js.get("result") if isinstance(js, dict) else {}
        username = str((res or {}).get("username") or "").strip()
        return {
            "ok": True,
            "username": username or None,
            "link": f"https://t.me/{username}" if username else None,
            "id": (res or {}).get("id"),
            "first_name": (res or {}).get("first_name"),
        }
    except Exception as e:
        return {"ok": False, "error": "bot_info_failed", "detail": str(e)}
