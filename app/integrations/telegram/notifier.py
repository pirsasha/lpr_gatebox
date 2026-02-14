# =========================================================
# Файл: app/integrations/telegram/notifier.py
# Проект: LPR GateBox
# Версия: v0.3.5.2-tg-topics-routing
# Обновлено: 2026-02-13 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - NEW: правильная маршрутизация по темам:
#   * events: короткий текст (без фото)
#   * cam1: фото + подпись (если включено send_photo)
#   * debug: отладка (если включено debug_enabled)
# - KEEP: очередь отправки + rate-limit + retries
# - KEEP: last_ok с photo_path (для /last и /snap)
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
    topic_key: Optional[str] = None  # "events" / "cam1" / "debug"


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

        # для /last и /snap
        self.last_ok: Dict[str, Any] = {}

    def start(self) -> None:
        self._t.start()
        self.log("[tg] notifier started")

    def stop(self) -> None:
        self._stop = True

    # -------------------------
    # Config helpers
    # -------------------------
    def _get_tg_cfg(self) -> Dict[str, Any]:
        cfg = self.get_cfg()
        tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        return tg if isinstance(tg, dict) else {}

    def _topics_cfg(self) -> Dict[str, Any]:
        tg = self._get_tg_cfg()
        topics = tg.get("topics") if isinstance(tg.get("topics"), dict) else {}
        return topics if isinstance(topics, dict) else {}

    def _topic_thread_id(self, topic_key: Optional[str]) -> Optional[int]:
        """
        Если включены topics: telegram.topics.thread_ids.{events|cam1|debug} = int
        """
        if not topic_key:
            return None
        topics = self._topics_cfg()
        mapping = topics.get("thread_ids") if isinstance(topics.get("thread_ids"), dict) else {}
        if not isinstance(mapping, dict):
            return None
        v = mapping.get(topic_key)
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    # -------------------------
    # Last OK snapshot
    # -------------------------
    def set_last_ok(self, payload: Dict[str, Any], photo_path: Optional[str] = None) -> None:
        try:
            self.last_ok = {
                "ts": float(payload.get("ts") or time.time()),
                "plate": str(payload.get("plate") or payload.get("plate_norm") or ""),
                "conf": float(payload.get("conf") or 0.0),
                "reason": str(payload.get("reason") or ""),
                "photo_path": str(photo_path) if photo_path else "",
            }
        except Exception:
            pass

    # -------------------------
    # Public API
    # -------------------------
    def enqueue_ok(self, payload: Dict[str, Any], photo_path: Optional[str]) -> None:
        """
        Рекомендуемая логика:
        - events: короткий текст (всегда)
        - cam1: фото+подпись (только если send_photo=True и есть photo_path)
        """
        tg = self._get_tg_cfg()
        if not tg.get("enabled"):
            return
        chat_id = str(tg.get("chat_id") or "").strip()
        if not chat_id:
            return

        plate = str(payload.get("plate") or payload.get("plate_norm") or "—")
        conf = payload.get("conf")
        reason = str(payload.get("reason") or "")
        level = str(payload.get("level") or "info")

        # Текст для events (короткий)
        events_text = f"✅ {plate}"
        if tg.get("include_conf") and conf is not None:
            try:
                events_text += f" (conf={float(conf):.2f})"
            except Exception:
                pass
        if reason and reason != "ok":
            events_text += f" [{reason}]"
        if level == "debug":
            events_text = "🟦 " + events_text

        # Подпись для фото (cam1)
        cam_caption = f"✅ GateBox: {plate}"
        if tg.get("include_conf") and conf is not None:
            try:
                cam_caption += f" (conf={float(conf):.2f})"
            except Exception:
                pass
        if reason:
            cam_caption += f"\nreason: {reason}"

        send_photo = bool(tg.get("send_photo", True))
        final_photo = photo_path if (send_photo and photo_path) else None

        # last_ok хранит фото (если реально отправляем фото)
        self.set_last_ok(payload, photo_path=final_photo)

        # 1) events всегда (но без фото)
        if self._topics_cfg().get("send_events_text", True):
            self._put(TgTask(text=events_text, photo_path=None, topic_key="events"))

        # 2) cam1 фото+подпись (если есть)
        if final_photo and self._topics_cfg().get("send_cam1_photo", True):
            self._put(TgTask(text=cam_caption, photo_path=final_photo, topic_key="cam1"))

    def enqueue_debug(self, text: str, photo_path: Optional[str] = None) -> None:
        tg = self._get_tg_cfg()
        if not tg.get("enabled"):
            return
        if not bool(tg.get("debug_enabled", False)):
            return
        chat_id = str(tg.get("chat_id") or "").strip()
        if not chat_id:
            return
        self._put(TgTask(text=f"🔧 {text}", photo_path=photo_path, topic_key="debug"))

    # -------------------------
    # Queue + worker
    # -------------------------
    def _put(self, task: TgTask) -> None:
        try:
            self.q.put_nowait(task)
        except Exception:
            self.log("[tg] WARN: queue is full, drop notify")

    def _rate_limit_sleep(self) -> None:
        tg = self._get_tg_cfg()
        try:
            rate = float(tg.get("rate_limit_sec", 2.0))
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

            tg = self._get_tg_cfg()
            chat_id = str(tg.get("chat_id") or "").strip()
            if not chat_id:
                continue

            self._rate_limit_sleep()

            thread_id = self._topic_thread_id(task.topic_key)

            ok = False
            for attempt in (1, 2, 3):
                try:
                    if task.photo_path and os.path.exists(task.photo_path):
                        self.client.send_photo(chat_id, task.photo_path, caption=task.text, message_thread_id=thread_id)
                    else:
                        self.client.send_message(chat_id, task.text, message_thread_id=thread_id)
                    ok = True
                    break
                except Exception as e:
                    self.log(f"[tg] WARN: send failed attempt={attempt}: {type(e).__name__}: {e}")
                    time.sleep(0.7 * attempt)

            if ok:
                self._last_send_ts = time.time()