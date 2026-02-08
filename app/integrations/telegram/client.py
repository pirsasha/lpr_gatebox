# =========================================================
# Файл: app/integrations/telegram/client.py
# Проект: LPR GateBox
# Версия: v0.3.4
# Изменено: 2026-02-08
# Что сделано:
# - FIX: защита от "кракозябр" — нормализация текста в UTF-8 (replace)
# - FIX: чистка нулевых байтов и невалидных символов в text/caption
# - KEEP: Telegram API client (getUpdates/sendMessage/sendPhoto)
# =========================================================

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import requests


class TelegramClient:
    def __init__(self, token: str, timeout_sec: float = 12.0):
        self.token = token.strip()
        self.timeout_sec = float(timeout_sec)
        self.base = f"https://api.telegram.org/bot{self.token}"

    # -------------------------
    # FIX: UTF-8 safe text
    # -------------------------
    @staticmethod
    def _safe_text(s: Optional[str]) -> str:
        """
        Make text Telegram-safe and encoding-safe.
        Prevents "кракозябры" if upstream text is malformed.

        - Ensures valid UTF-8 (replace invalid sequences)
        - Removes NUL bytes
        """
        if s is None:
            return ""
        if not isinstance(s, str):
            s = str(s)

        # Remove NULs that may break payloads or produce weird output
        s = s.replace("\x00", "")

        # Enforce valid UTF-8
        try:
            s = s.encode("utf-8", "replace").decode("utf-8", "replace")
        except Exception:
            # ultra defensive fallback
            s = str(s)

        return s

    def _post(
        self,
        method: str,
        data: Dict[str, Any],
        files: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        t = float(timeout or self.timeout_sec)
        url = f"{self.base}/{method}"

        # NOTE: Telegram API expects form-data. requests handles UTF-8 fine,
        # but upstream text might be broken -> we sanitize in send_* methods.
        r = requests.post(url, data=data, files=files, timeout=t)
        r.raise_for_status()

        js = r.json()
        if not isinstance(js, dict) or js.get("ok") is not True:
            raise RuntimeError(f"telegram api error: {js}")
        return js

    def get_updates(self, offset: Optional[int] = None, timeout: int = 25) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "timeout": int(timeout),
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            data["offset"] = int(offset)
        return self._post("getUpdates", data=data, timeout=float(timeout) + 5.0)

    def send_message(self, chat_id: str, text: str) -> None:
        text = self._safe_text(text)
        self._post(
            "sendMessage",
            data={
                "chat_id": str(chat_id),
                "text": text,
                "disable_web_page_preview": "true",
            },
        )

    def send_photo(self, chat_id: str, photo_path: str, caption: Optional[str] = None) -> None:
        if not os.path.exists(photo_path):
            raise FileNotFoundError(photo_path)

        data: Dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = self._safe_text(caption)

        with open(photo_path, "rb") as f:
            files = {"photo": f}
            self._post("sendPhoto", data=data, files=files)

        # tiny delay to avoid TG throttling on burst
        time.sleep(0.05)