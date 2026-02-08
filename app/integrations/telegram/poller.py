# =========================================================
# Файл: app/integrations/telegram/poller.py
# Проект: LPR GateBox
# Версия: v0.3.4
# Изменено: 2026-02-08
# Что сделано:
# - NEW: long polling getUpdates
# - NEW: /start автопривязка chat_id в settings.json
# - NEW: /status /last
# =========================================================

from __future__ import annotations

import time
import threading
from typing import Any, Dict, Callable, Optional

from app.integrations.telegram.client import TelegramClient
from app.integrations.telegram.notifier import TelegramNotifier


class TelegramPoller:
    def __init__(
        self,
        client: TelegramClient,
        get_cfg: Callable[[], Dict[str, Any]],
        save_patch: Callable[[Dict[str, Any]], Dict[str, Any]],
        notifier: TelegramNotifier,
        log: Callable[[str], None],
    ):
        self.client = client
        self.get_cfg = get_cfg
        self.save_patch = save_patch
        self.notifier = notifier
        self.log = log

        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._offset: Optional[int] = None

    def start(self) -> None:
        self._t.start()
        self.log("[tg] poller started")

    def stop(self) -> None:
        self._stop = True

    def _is_enabled(self) -> bool:
        cfg = self.get_cfg()
        tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        return bool(isinstance(tg, dict) and tg.get("enabled"))

    def _pair(self, chat_id: str, code: Optional[str]) -> str:
        cfg = self.get_cfg()
        tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        tg = tg if isinstance(tg, dict) else {}

        pair_code = tg.get("pair_code")
        if pair_code is not None:
            want = str(pair_code).strip()
            got = str(code or "").strip()
            if want and got != want:
                return "❌ Неверный код привязки. Напиши: /start <код>"

        patch = {"telegram": {"chat_id": chat_id}}
        self.save_patch(patch)
        return "✅ Привязано! Теперь GateBox будет присылать уведомления сюда."

    def _status_text(self) -> str:
        cfg = self.get_cfg()
        tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        chat_id = str((tg or {}).get("chat_id") or "").strip()
        en = bool((tg or {}).get("enabled"))
        sp = bool((tg or {}).get("send_photo", True))
        return "ℹ️ GateBox Telegram\n" + f"- enabled: {en}\n- paired: {'yes' if chat_id else 'no'}\n- photo: {sp}"

    def _last_text(self) -> str:
        x = self.notifier.last_ok or {}
        if not x:
            return "Пока нет событий OK."
        ts = float(x.get("ts") or 0.0)
        plate = str(x.get("plate") or "—")
        conf = x.get("conf")
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "—"
        try:
            c = f"{float(conf):.2f}" if conf is not None else "—"
        except Exception:
            c = "—"
        return f"✅ Последний OK:\n- plate: {plate}\n- conf: {c}\n- time: {t}"

    def _handle_message(self, msg: Dict[str, Any]) -> None:
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            return

        chat = msg.get("chat")
        if not isinstance(chat, dict):
            return
        chat_id = chat.get("id")
        if chat_id is None:
            return
        chat_id_s = str(chat_id)

        parts = text.strip().split()
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if cmd == "/start":
            reply = self._pair(chat_id_s, code=arg)
            self.client.send_message(chat_id_s, reply)
            return

        if cmd == "/status":
            self.client.send_message(chat_id_s, self._status_text())
            return

        if cmd == "/last":
            self.client.send_message(chat_id_s, self._last_text())
            return

        if cmd in ("/help",):
            self.client.send_message(chat_id_s, "Команды: /start [код], /status, /last")
            return

    def _run(self) -> None:
        while not self._stop:
            try:
                if not self._is_enabled():
                    time.sleep(1.5)
                    continue

                js = self.client.get_updates(offset=self._offset, timeout=25)
                res = js.get("result")
                if not isinstance(res, list) or not res:
                    continue

                for upd in res:
                    if not isinstance(upd, dict):
                        continue
                    uid = upd.get("update_id")
                    if isinstance(uid, int):
                        self._offset = uid + 1

                    msg = upd.get("message")
                    if isinstance(msg, dict):
                        self._handle_message(msg)

            except Exception as e:
                self.log(f"[tg] WARN: poll failed: {type(e).__name__}: {e}")
                time.sleep(2.0)