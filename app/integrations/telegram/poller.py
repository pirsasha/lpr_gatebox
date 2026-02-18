# =========================================================
# Файл: app/integrations/telegram/poller.py
# Проект: LPR GateBox
# Версия: v0.3.5.2-tg-topics-routing
# Обновлено: 2026-02-13 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - FIX: /topics_on создаёт темы и сохраняет thread_ids
# - NEW: /snap и кнопка "Снимок cam1" отправляют в тему cam1 (если есть)
# - NEW: /debug on|off и отправка debug в тему debug (через notifier.enqueue_debug)
# - KEEP: /menu, /open, /status, /last, /photo, /rate
#
# Важно по стилям кнопок:
# - style допускает только: "danger" | "success" | "primary"
# =========================================================

from __future__ import annotations

import json
import socket
import ssl
import time
import threading
from typing import Any, Dict, Callable, Optional, Tuple

from app.integrations.telegram.client import TelegramClient
from app.integrations.telegram.notifier import TelegramNotifier


def _mqtt_encode_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return len(b).to_bytes(2, "big") + b


def _mqtt_encode_remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        digit = n % 128
        n //= 128
        if n > 0:
            digit |= 0x80
        out.append(digit)
        if n == 0:
            break
    return bytes(out)


def mqtt_publish_qos0(
    *,
    host: str,
    port: int,
    topic: str,
    payload: bytes,
    username: Optional[str] = None,
    password: Optional[str] = None,
    client_id: str = "gatebox-tg",
    keepalive: int = 20,
    use_tls: bool = False,
    timeout_sec: float = 5.0,
) -> None:
    proto_name = _mqtt_encode_str("MQTT")
    proto_level = b"\x04"  # 3.1.1
    connect_flags = 0x02  # clean session

    if username is not None:
        connect_flags |= 0x80
        if password is not None:
            connect_flags |= 0x40

    vh = proto_name + proto_level + bytes([connect_flags]) + int(keepalive).to_bytes(2, "big")

    pl = _mqtt_encode_str(client_id)
    if username is not None:
        pl += _mqtt_encode_str(username)
        if password is not None:
            pl += _mqtt_encode_str(password)

    connect_pkt = bytearray()
    connect_pkt.append(0x10)  # CONNECT
    connect_pkt += _mqtt_encode_remaining_length(len(vh) + len(pl))
    connect_pkt += vh
    connect_pkt += pl

    topic_b = _mqtt_encode_str(topic)
    publish_vh = topic_b  # QoS0 no packet id
    publish_pkt = bytearray()
    publish_pkt.append(0x30)  # PUBLISH QoS0
    publish_pkt += _mqtt_encode_remaining_length(len(publish_vh) + len(payload))
    publish_pkt += publish_vh
    publish_pkt += payload

    disconnect_pkt = b"\xE0\x00"

    s = socket.create_connection((host, int(port)), timeout=timeout_sec)
    try:
        if use_tls:
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(s, server_hostname=host)

        s.sendall(connect_pkt)

        hdr = s.recv(4)
        if len(hdr) < 4 or hdr[0] != 0x20 or hdr[1] != 0x02:
            raise RuntimeError(f"mqtt: bad connack header: {hdr!r}")
        rc = hdr[3]
        if rc != 0:
            raise RuntimeError(f"mqtt: connack rc={rc}")

        s.sendall(publish_pkt)
        s.sendall(disconnect_pkt)
    finally:
        try:
            s.close()
        except Exception:
            pass


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

    # -------------------------
    # Config helpers
    # -------------------------
    def _tg_cfg(self) -> Dict[str, Any]:
        cfg = self.get_cfg()
        tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        return tg if isinstance(tg, dict) else {}

    def _topics_cfg(self) -> Dict[str, Any]:
        tg = self._tg_cfg()
        topics = tg.get("topics") if isinstance(tg.get("topics"), dict) else {}
        return topics if isinstance(topics, dict) else {}

    def _topic_thread_id(self, key: str) -> Optional[int]:
        topics = self._topics_cfg()
        mapping = topics.get("thread_ids") if isinstance(topics.get("thread_ids"), dict) else {}
        if not isinstance(mapping, dict):
            return None
        v = mapping.get(key)
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    def _mqtt_cfg(self) -> Dict[str, Any]:
        cfg = self.get_cfg()
        m = cfg.get("mqtt") if isinstance(cfg.get("mqtt"), dict) else {}
        return m if isinstance(m, dict) else {}

    def _is_enabled(self) -> bool:
        return bool(self._tg_cfg().get("enabled"))

    def _paired_chat_id(self) -> str:
        return str(self._tg_cfg().get("chat_id") or "").strip()

    def _is_paired_chat(self, chat_id_s: str) -> bool:
        paired = self._paired_chat_id()
        return bool(paired and paired == chat_id_s)

    def _pair(self, chat_id: str, code: Optional[str]) -> str:
        tg = self._tg_cfg()
        pair_code = tg.get("pair_code")
        if pair_code is not None:
            want = str(pair_code).strip()
            got = str(code or "").strip()
            if want and got != want:
                return "❌ Неверный код привязки. Напиши: /start <код>"
        self.save_patch({"telegram": {"chat_id": chat_id}})
        return "✅ Привязано! Открой /menu."

    # -------------------------
    # UI
    # -------------------------
    def _menu_kb(self) -> Dict[str, Any]:
        return {
            "keyboard": [
                [{"text": "🟢 Открыть ворота", "style": "success"}],
                [{"text": "📷 Снимок cam1", "style": "primary"}, {"text": "🔎 Статус", "style": "primary"}],
                [{"text": "🧾 Последний OK"}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    def _status_text(self) -> str:
        tg = self._tg_cfg()
        m = self._mqtt_cfg()
        topics = self._topics_cfg()

        chat_id = str(tg.get("chat_id") or "").strip()

        out = "ℹ️ GateBox Telegram\n"
        out += f"- enabled: {bool(tg.get('enabled'))}\n"
        out += f"- paired: {'yes' if chat_id else 'no'}\n"
        out += f"- photo: {bool(tg.get('send_photo', True))}\n"
        out += f"- debug_enabled: {bool(tg.get('debug_enabled', False))}\n"
        out += f"- rate_limit_sec: {tg.get('rate_limit_sec', 2.0)}\n"

        out += "\nℹ️ Topics\n"
        tids = topics.get("thread_ids", {})
        out += f"- thread_ids: {tids}\n"
        out += f"- send_events_text: {topics.get('send_events_text', True)}\n"
        out += f"- send_cam1_photo: {topics.get('send_cam1_photo', True)}\n"

        out += "\nℹ️ MQTT\n"
        out += f"- enabled: {bool(m.get('enabled'))}\n"
        out += f"- host: {m.get('host')}:{m.get('port')}\n"
        out += f"- topic: {m.get('topic')}\n"
        return out

    def _last_text(self) -> str:
        x = self.notifier.last_ok or {}
        if not x:
            return "Пока нет событий OK."
        ts = float(x.get("ts") or 0.0)
        plate = str(x.get("plate") or "—")
        conf = x.get("conf")
        reason = str(x.get("reason") or "")
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "—"
        try:
            c = f"{float(conf):.2f}" if conf is not None else "—"
        except Exception:
            c = "—"
        out = f"✅ Последний OK:\n- plate: {plate}\n- conf: {c}\n- time: {t}"
        if reason:
            out += f"\n- reason: {reason}"
        return out

    def _send_to_topic(self, chat_id_s: str, key: str, text: str) -> None:
        tid = self._topic_thread_id(key)
        self.client.send_message(chat_id_s, text, message_thread_id=tid)

    def _maybe_send_snap(self, chat_id_s: str) -> None:
        x = self.notifier.last_ok or {}
        p = str(x.get("photo_path") or "").strip()
        tid = self._topic_thread_id("cam1")  # снапы всегда в cam1
        if p and p != "None":
            self.client.send_photo(chat_id_s, p, caption="📷 cam1: последний кадр (из OK)", message_thread_id=tid)
            return
        self.client.send_message(chat_id_s, "📷 Снимок cam1: пока нет кадра. (Сначала должен прийти хотя бы один OK с фото)", message_thread_id=tid)

    # -------------------------
    # MQTT
    # -------------------------
    def _mqtt_open(self) -> Tuple[bool, str]:
        m = self._mqtt_cfg()
        if not m.get("enabled"):
            return False, "MQTT выключен (mqtt.enabled=false)."

        host = str(m.get("host") or "").strip()
        port = int(m.get("port") or 1883)
        topic = str(m.get("topic") or "").strip()
        user = str(m.get("user") or "").strip() or None
        pwd = str(m.get("pass") or "").strip() or None

        if not host or not topic:
            return False, "MQTT не настроен (host/topic пустые)."

        payload_cfg = m.get("payload", "1")
        if isinstance(payload_cfg, (dict, list)):
            payload_b = json.dumps(payload_cfg, ensure_ascii=False).encode("utf-8")
        else:
            payload_b = str(payload_cfg).encode("utf-8")

        use_tls = bool(m.get("tls", False))

        mqtt_publish_qos0(
            host=host,
            port=port,
            topic=topic,
            payload=payload_b,
            username=user,
            password=pwd,
            use_tls=use_tls,
            client_id="gatebox-tg",
        )
        return True, f"MQTT publish ok: {topic} <- {payload_b.decode('utf-8', 'replace')}"

    # -------------------------
    # Topics init
    # -------------------------
    def _topics_on(self, chat_id_s: str) -> str:
        names = [("events", "🚗 events"), ("cam1", "📷 cam1"), ("debug", "🔧 debug")]
        thread_ids: Dict[str, int] = {}

        for key, name in names:
            js = self.client.create_forum_topic(chat_id_s, name=name)
            res = js.get("result") if isinstance(js, dict) else None
            if isinstance(res, dict):
                tid = res.get("message_thread_id")
                if isinstance(tid, int):
                    thread_ids[key] = tid

        if not thread_ids:
            return "⚠️ Не удалось создать темы. Убедись, что это форум-чат (Topics включены) и бот админ с правом Manage Topics."

        patch = {
            "telegram": {
                "topics": {
                    "thread_ids": thread_ids,
                    "send_events_text": True,
                    "send_cam1_photo": True,
                }
            }
        }
        self.save_patch(patch)
        return f"✅ Темы созданы и сохранены: {thread_ids}"

    # -------------------------
    # Message handler
    # -------------------------
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
            self.client.send_message(chat_id_s, reply, reply_markup=self._menu_kb())
            return

        if not self._is_paired_chat(chat_id_s):
            if self._paired_chat_id():
                self.client.send_message(chat_id_s, "❌ Этот чат не привязан. Напиши /start в нужном чате.")
            return

        if cmd == "/menu":
            self.client.send_message(chat_id_s, "Пульт GateBox:", reply_markup=self._menu_kb())
            return

        if cmd == "/help":
            self.client.send_message(
                chat_id_s,
                "Команды:\n"
                "- /menu\n"
                "- /open (MQTT gate/open)\n"
                "- /snap (в тему cam1)\n"
                "- /status\n"
                "- /last\n"
                "- /photo on|off\n"
                "- /rate <sec>\n"
                "- /debug on|off\n"
                "- /topics_on\n"
            )
            return

        if cmd == "/status":
            self.client.send_message(chat_id_s, self._status_text(), message_thread_id=self._topic_thread_id("events"))
            return

        if cmd == "/last":
            self.client.send_message(chat_id_s, self._last_text(), message_thread_id=self._topic_thread_id("events"))
            return

        if cmd == "/snap":
            self._maybe_send_snap(chat_id_s)
            return

        if cmd == "/open":
            ok, info = self._mqtt_open()
            self.client.send_message(chat_id_s, ("✅ " if ok else "❌ ") + "Ворота:\n" + info, message_thread_id=self._topic_thread_id("events"))
            return

        if cmd == "/photo":
            if arg not in ("on", "off"):
                self.client.send_message(chat_id_s, "Используй: /photo on|off", message_thread_id=self._topic_thread_id("events"))
                return
            self.save_patch({"telegram": {"send_photo": True if arg == "on" else False}})
            self.client.send_message(chat_id_s, f"✅ send_photo={arg}", message_thread_id=self._topic_thread_id("events"))
            return

        if cmd == "/rate":
            try:
                sec = float(arg) if arg is not None else None
            except Exception:
                sec = None
            if sec is None or sec < 0.2 or sec > 30:
                self.client.send_message(chat_id_s, "Используй: /rate <сек>, например /rate 2.0 (0.2..30)", message_thread_id=self._topic_thread_id("events"))
                return
            self.save_patch({"telegram": {"rate_limit_sec": sec}})
            self.client.send_message(chat_id_s, f"✅ rate_limit_sec={sec}", message_thread_id=self._topic_thread_id("events"))
            return

        if cmd == "/debug":
            if arg not in ("on", "off"):
                cur = bool(self._tg_cfg().get("debug_enabled", False))
                self.client.send_message(chat_id_s, f"Текущее: debug_enabled={cur}\nИспользуй: /debug on|off", message_thread_id=self._topic_thread_id("debug"))
                return
            self.save_patch({"telegram": {"debug_enabled": True if arg == "on" else False}})
            self.client.send_message(chat_id_s, f"✅ debug_enabled={arg}", message_thread_id=self._topic_thread_id("debug"))
            # пример debug-сообщения
            self.notifier.enqueue_debug(f"debug mode -> {arg}")
            return

        if cmd == "/topics_on":
            try:
                self.client.send_message(chat_id_s, self._topics_on(chat_id_s))
            except Exception as e:
                self.client.send_message(chat_id_s, f"⚠️ topics_on: {type(e).__name__}: {e}")
            return

        # reply-кнопки:
        t = text.strip().lower()

        if t in ("🟢 открыть ворота", "открыть ворота"):
            ok, info = self._mqtt_open()
            self.client.send_message(chat_id_s, ("✅ " if ok else "❌ ") + "Ворота:\n" + info, message_thread_id=self._topic_thread_id("events"))
            return

        if t in ("📷 снимок cam1", "снимок cam1"):
            self._maybe_send_snap(chat_id_s)
            return

        if t in ("🔎 статус", "статус"):
            self.client.send_message(chat_id_s, self._status_text(), message_thread_id=self._topic_thread_id("events"))
            return

        if t in ("🧾 последний ok", "последний ok"):
            self.client.send_message(chat_id_s, self._last_text(), message_thread_id=self._topic_thread_id("events"))
            return

    # -------------------------
    # Main loop
    # -------------------------
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
                emsg = str(e)
                if "409" in emsg and "terminated by other getUpdates request" in emsg:
                    self.log("[tg] WARN: getUpdates conflict (409). Этот bot token используется в другом процессе. Пауза polling 30s.")
                    time.sleep(30.0)
                    continue
                self.log(f"[tg] WARN: poll failed: {type(e).__name__}: {e}")
                time.sleep(2.0)
