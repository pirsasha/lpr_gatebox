# =========================================================
# Файл: app/integrations/telegram/notifier.py
# Проект: LPR GateBox
# Версия: v0.3.4
# Изменено: 2026-02-08
# Что сделано:
# - NEW: очередь отправки уведомлений (не блокирует /infer)
# - NEW: rate-limit, retries, fallback на текст
# =========================================================

from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass
from queue import Queue, Empty
from typing import Any, Dict, Optional, Callable

from app.integrations.telegram.client import TelegramClient


@dataclass
class TgTask:
    text: str
    photo_path: Optional[str] = None


class TelegramNotifier:
    def __init__(
        self,
        client: TelegramClient,
        get_cfg: Callable[[], Dict[str, Any]],
        log: Callable[[str], None],
    ):
        self.client = client
        self.get_cfg = get_cfg
        self.log = log

        self.q: "Queue[TgTask]" = Queue(maxsize=200)
        self._stop = False
        self._t = threading.Thread(target=self._worker, daemon=True)
        self._last_send_ts = 0.0

        self.last_ok: Dict[str, Any] = {}  # для /last

    def start(self) -> None:
        self._t.start()
        self.log("[tg] notifier started")

    def stop(self) -> None:
        self._stop = True

    def set_last_ok(self, payload: Dict[str, Any]) -> None:
        try:
            self.last_ok = {
                "ts": float(payload.get("ts") or time.time()),
                "plate": str(payload.get("plate") or payload.get("plate_norm") or ""),
                "conf": float(payload.get("conf") or 0.0),
                "reason": str(payload.get("reason") or ""),
            }
        except Exception:
            pass

    def enqueue_ok(self, payload: Dict[str, Any], photo_path: Optional[str]) -> None:
        cfg = self.get_cfg()
        tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        if not isinstance(tg, dict) or not tg.get("enabled"):
            return

        chat_id = str(tg.get("chat_id") or "").strip()
        if not chat_id:
            return

        plate = str(payload.get("plate") or payload.get("plate_norm") or "—")
        conf = payload.get("conf")
        msg = f"✅ GateBox: {plate}"
        if tg.get("include_conf") and conf is not None:
            try:
                msg += f" (conf={float(conf):.2f})"
            except Exception:
                pass

        send_photo = bool(tg.get("send_photo", True))
        task = TgTask(text=msg, photo_path=(photo_path if send_photo else None))

        try:
            self.q.put_nowait(task)
        except Exception:
            self.log("[tg] WARN: queue is full, drop notify")

    def _rate_limit_sleep(self) -> None:
        cfg = self.get_cfg()
        tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        rate = 2.0
        try:
            rate = float(tg.get("rate_limit_sec", 2.0)) if isinstance(tg, dict) else 2.0
        except Exception:
            rate = 2.0

        now = time.time()
        dt = now - self._last_send_ts
        if dt < rate:
            time.sleep(max(0.0, rate - dt))

    def _worker(self) -> None:
        while not self._stop:
            try:
                task = self.q.get(timeout=0.4)
            except Empty:
                continue

            cfg = self.get_cfg()
            tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
            chat_id = str((tg or {}).get("chat_id") or "").strip()
            if not chat_id:
                continue

            # rate limit
            self._rate_limit_sleep()

            # retries
            ok = False
            for attempt in (1, 2, 3):
                try:
                    if task.photo_path and os.path.exists(task.photo_path):
                        self.client.send_photo(chat_id, task.photo_path, caption=task.text)
                    else:
                        self.client.send_message(chat_id, task.text)
                    ok = True
                    break
                except Exception as e:
                    self.log(f"[tg] WARN: send failed attempt={attempt}: {type(e).__name__}: {e}")
                    time.sleep(0.7 * attempt)

            if ok:
                self._last_send_ts = time.time()