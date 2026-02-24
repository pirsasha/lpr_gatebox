# =========================================================
# Файл: app/integrations/telegram/client.py
# Проект: LPR GateBox
# Версия: v0.3.5.2-tg-timeouts-tuple-sendphoto (PATCH)
# Обновлено: 2026-02-19 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - NEW: requests timeout теперь tuple (connect, read) по умолчанию
# - NEW: sendPhoto использует увеличенный таймаут (чтобы не падало на upload/write timeout)
# - KEEP: более понятная ошибка Telegram (печатаем description)
# - KEEP: reply_markup + message_thread_id
# - KEEP: createForumTopic
# =========================================================

from __future__ import annotations

import os
import time
import json
from typing import Any, Dict, Optional, Tuple, Union

import requests

# requests timeout может быть float или (connect_timeout, read_timeout)
TimeoutT = Union[float, Tuple[float, float]]


class TelegramClient:
    def __init__(self, token: str, timeout_sec: float = 12.0):
        self.token = token.strip()
        # timeout_sec трактуем как "read timeout" по умолчанию (для простых методов)
        self.timeout_sec = float(timeout_sec)
        self.base = f"https://api.telegram.org/bot{self.token}"

        # дефолтные таймауты (пока без настроек из settings):
        # connect обычно быстрый, read может быть длиннее
        self._default_connect_timeout = 6.0
        self._default_read_timeout = float(timeout_sec)

        # отдельный, более жирный таймаут для sendPhoto (upload + обработка)
        self._photo_connect_timeout = 10.0
        self._photo_read_timeout = 60.0

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

    def _resolve_timeout(self, timeout: Optional[TimeoutT]) -> TimeoutT:
        """
        timeout:
          - None -> (default_connect, default_read)
          - float -> float (как раньше)
          - tuple(connect, read) -> используем как есть
        """
        if timeout is None:
            return (float(self._default_connect_timeout), float(self._default_read_timeout))
        if isinstance(timeout, tuple) and len(timeout) == 2:
            return (float(timeout[0]), float(timeout[1]))
        # float / int / другие числовые — оставляем совместимость
        return float(timeout)  # type: ignore[arg-type]

    def _post(
        self,
        method: str,
        data: Dict[str, Any],
        files: Optional[Dict[str, Any]] = None,
        timeout: Optional[TimeoutT] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base}/{method}"
        t = self._resolve_timeout(timeout)

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

        # long-poll: read_timeout должен быть больше timeout, connect небольшой
        return self._post("getUpdates", data=data, timeout=(6.0, float(timeout) + 10.0))

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

        # дефолтный timeout (connect/read)
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

        # sendPhoto часто падает на "write operation timed out" при upload.
        # Поэтому даём больше времени именно тут.
        photo_timeout: TimeoutT = (float(self._photo_connect_timeout), float(self._photo_read_timeout))

        with open(photo_path, "rb") as f:
            files = {"photo": f}
            js = self._post("sendPhoto", data=data, files=files, timeout=photo_timeout)

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
