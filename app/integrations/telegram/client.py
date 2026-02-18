# =========================================================
# Файл: app/integrations/telegram/client.py
# Проект: LPR GateBox
# Версия: v0.3.5.1-tg-style-fix-better-errors
# Обновлено: 2026-02-13 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - FIX: более понятная ошибка Telegram (печатаем description)
# - KEEP: reply_markup + message_thread_id
# - KEEP: createForumTopic (темы в личке / форум-супергруппа)
# =========================================================

from __future__ import annotations

import os
import time
import json
from typing import Any, Dict, Optional

import requests


class TelegramClient:
    def __init__(self, token: str, timeout_sec: float = 12.0):
        self.token = token.strip()
        self.timeout_sec = float(timeout_sec)
        self.base = f"https://api.telegram.org/bot{self.token}"

    @staticmethod
    def _safe_text(s: Optional[str]) -> str:
        if s is None:
            return ""
        if not isinstance(s, str):
            s = str(s)
        s = s.replace("\x00", "")
        try:
            s = s.encode("utf-8", "replace").decode("utf-8", "replace")
        except Exception:
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

        r = requests.post(url, data=data, files=files, timeout=t)
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            # Печатаем реальное описание ошибки TG
            try:
                js = r.json()
                desc = js.get("description")
                raise RuntimeError(f"telegram http {r.status_code}: {desc} (method={method})") from e
            except Exception:
                raise RuntimeError(f"telegram http {r.status_code}: {r.text} (method={method})") from e

        js = r.json()
        if not isinstance(js, dict) or js.get("ok") is not True:
            raise RuntimeError(f"telegram api error: {js}")
        return js

    def get_me(self) -> Dict[str, Any]:
        return self._post("getMe", data={})

    def get_updates(self, offset: Optional[int] = None, timeout: int = 25) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "timeout": int(timeout),
            "allowed_updates": '["message","callback_query"]',
        }
        if offset is not None:
            data["offset"] = int(offset)
        return self._post("getUpdates", data=data, timeout=float(timeout) + 5.0)

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_markup: Optional[Dict[str, Any]] = None,
        message_thread_id: Optional[int] = None,
        disable_web_page_preview: bool = True,
    ) -> Dict[str, Any]:
        text = self._safe_text(text)
        data: Dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": "true" if disable_web_page_preview else "false",
        }
        if message_thread_id is not None:
            data["message_thread_id"] = int(message_thread_id)
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self._post("sendMessage", data=data)

    def send_photo(
        self,
        chat_id: str,
        photo_path: str,
        caption: Optional[str] = None,
        *,
        reply_markup: Optional[Dict[str, Any]] = None,
        message_thread_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not os.path.exists(photo_path):
            raise FileNotFoundError(photo_path)

        data: Dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = self._safe_text(caption)
        if message_thread_id is not None:
            data["message_thread_id"] = int(message_thread_id)
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

        with open(photo_path, "rb") as f:
            files = {"photo": f}
            js = self._post("sendPhoto", data=data, files=files)

        time.sleep(0.05)
        return js

    def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        *,
        reply_markup: Optional[Dict[str, Any]] = None,
        disable_web_page_preview: bool = True,
    ) -> Dict[str, Any]:
        text = self._safe_text(text)
        data: Dict[str, Any] = {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "text": text,
            "disable_web_page_preview": "true" if disable_web_page_preview else "false",
        }
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self._post("editMessageText", data=data)

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False) -> None:
        data: Dict[str, Any] = {"callback_query_id": str(callback_query_id)}
        if text:
            data["text"] = self._safe_text(text)
        if show_alert:
            data["show_alert"] = "true"
        self._post("answerCallbackQuery", data=data)

    def create_forum_topic(
        self,
        chat_id: str,
        name: str,
        *,
        icon_color: Optional[int] = None,
        icon_custom_emoji_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {"chat_id": str(chat_id), "name": self._safe_text(name)}
        if icon_color is not None:
            data["icon_color"] = int(icon_color)
        if icon_custom_emoji_id:
            data["icon_custom_emoji_id"] = str(icon_custom_emoji_id)
        return self._post("createForumTopic", data=data)