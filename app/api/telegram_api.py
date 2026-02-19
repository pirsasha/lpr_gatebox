# =========================================================
# Файл: app/api/telegram_api.py
# Проект: LPR GateBox
# Версия: v0.3.5
# Изменено: 2026-02-18
# Что сделано:
# - CHG: приоритет telegram token изменён на cfg -> env -> default через единый resolver
# - NEW: API для теста Telegram уведомлений
# =========================================================

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.integrations.telegram.client import TelegramClient
from app.core.config_resolve import get_str

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
    if not _TG["get_cfg"]:
        return {"ok": False, "error": "telegram_not_initialized"}

    cfg = _TG["get_cfg"]()
    tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    tg = tg if isinstance(tg, dict) else {}

    if not tg.get("enabled"):
        return {"ok": False, "error": "telegram_disabled"}

    chat_id = str(tg.get("chat_id") or "").strip()
    if not chat_id:
        return {"ok": False, "error": "telegram_not_paired"}

    token = get_str(cfg, "telegram.bot_token", "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "error": "no_token"}

    photo_path: Optional[str] = None
    if req.with_photo and _TG["pick_photo"]:
        photo_path = _TG["pick_photo"](cfg)

    # 1) Пробуем отправить синхронно, чтобы UI сразу видел реальную ошибку.
    try:
        cli = TelegramClient(token=token)
        thread_id = tg.get("thread_id")
        if isinstance(thread_id, str) and thread_id.strip().isdigit():
            thread_id = int(thread_id.strip())
        if not isinstance(thread_id, int):
            thread_id = None

        if req.with_photo and photo_path:
            js = cli.send_photo(chat_id, photo_path, caption=req.text, message_thread_id=thread_id)
            return {"ok": True, "delivered": True, "with_photo": True, "photo_path": photo_path, "result": js.get("result") if isinstance(js, dict) else js}

        js = cli.send_message(chat_id, req.text, message_thread_id=thread_id)
        return {
            "ok": True,
            "delivered": True,
            "with_photo": False,
            "photo_path": photo_path,
            "warning": "photo_unavailable" if req.with_photo and not photo_path else None,
            "result": js.get("result") if isinstance(js, dict) else js,
        }
    except Exception as e:
        # 2) fallback в очередь (если notifier работает), но отдаём ошибку клиенту для диагностики.
        if _TG.get("enqueue"):
            try:
                _TG["enqueue"](req.text, photo_path)
            except Exception:
                pass
        return {
            "ok": False,
            "error": "telegram_send_failed",
            "detail": str(e),
            "queued_fallback": bool(_TG.get("enqueue")),
            "with_photo": bool(req.with_photo and photo_path),
            "photo_path": photo_path,
        }

@router.get("/bot_info")
def telegram_bot_info():
    if not _TG["get_cfg"]:
        return {"ok": False, "error": "telegram_not_initialized"}

    cfg = _TG["get_cfg"]()
    tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    tg = tg if isinstance(tg, dict) else {}

    token = get_str(cfg, "telegram.bot_token", "TELEGRAM_BOT_TOKEN", "")
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
