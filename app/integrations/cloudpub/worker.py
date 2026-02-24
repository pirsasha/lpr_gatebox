# =========================================================
# Файл: app/integrations/cloudpub/worker.py
# Проект: LPR GateBox
# Версия: v0.3.38-cloudpub-sdk-worker-mask-fix
# Обновлено: 2026-02-20 (UTC+1)
# Автор: Александр + ChatGPT
#
# FIX:
# - Игнорируем "***"/"•••" в token/password при connect,
#   чтобы UI после рестарта не ломал переподключение.
# =========================================================

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Optional


def _bootstrap_env() -> None:
    os.environ.setdefault("HOME", "/tmp")
    os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/.config")
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")
    os.environ.setdefault("XDG_STATE_HOME", "/tmp/.local/state")
    os.environ.setdefault("TMPDIR", "/tmp")

    for p in (
        "/tmp",
        "/tmp/.config",
        "/tmp/.cache",
        "/tmp/.local",
        "/tmp/.local/state",
        "/tmp/.config/cloudpub",
        "/tmp/.cache/cloudpub",
        "/tmp/.local/state/cloudpub",
    ):
        try:
            os.makedirs(p, exist_ok=True)
        except Exception:
            pass


def _is_masked_secret(v: str) -> bool:
    s = (v or "").strip()
    return s in ("***", "•••")


def _reply(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class _State:
    def __init__(self) -> None:
        self.conn: Any = None
        self.endpoint_guid: Optional[str] = None
        self.public_url: Optional[str] = None
        self.server_ip: str = ""
        self.last_ok_ts: Optional[int] = None
        self.last_error: Optional[str] = None
        self.connection_state: str = "offline"  # offline|online
        self.state_reason: str = ""
        self.audit: list[dict] = []

    def audit_add(self, action: str, ok: bool, detail: str) -> None:
        try:
            self.audit.insert(
                0,
                {"ts": int(time.time()), "action": action, "ok": bool(ok), "detail": str(detail)[:500]},
            )
            del self.audit[200:]
        except Exception:
            pass

    def snapshot(self) -> Dict[str, Any]:
        return {
            "connection_state": self.connection_state,
            "state_reason": self.state_reason,
            "server_ip": self.server_ip,
            "public_url": self.public_url,
            "management_url": None,
            "last_ok_ts": self.last_ok_ts,
            "last_error": self.last_error,
            "audit": self.audit[:50],
            "mode": "sdk-worker",
        }


def main() -> int:
    _bootstrap_env()

    try:
        from cloudpub_python_sdk import Connection, Protocol, Auth  # type: ignore
    except Exception as e:
        _reply({"ok": False, "error": f"sdk_import_failed: {e}"})
        return 2

    st = _State()
    _reply({"ok": True, "ready": True, "ts": int(time.time())})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception:
            _reply({"ok": False, "error": "bad_json"})
            continue

        cmd = str(req.get("cmd") or "")

        if cmd == "ping":
            _reply({"ok": True, "pong": True, "ts": int(time.time())})
            continue

        if cmd == "status":
            _reply({"ok": True, "state": st.snapshot()})
            continue

        if cmd == "audit_clear":
            st.audit.clear()
            st.audit_add("audit_clear", True, "manual")
            _reply({"ok": True})
            continue

        if cmd == "disconnect":
            try:
                if st.conn is not None:
                    try:
                        if st.endpoint_guid:
                            st.conn.unpublish(st.endpoint_guid)
                    except Exception:
                        pass
                    try:
                        if hasattr(st.conn, "close"):
                            st.conn.close()
                    except Exception:
                        pass

                st.conn = None
                st.endpoint_guid = None
                st.public_url = None
                st.last_error = None
                st.connection_state = "offline"
                st.state_reason = "disconnected"
                st.audit_add("disconnect", True, "manual")
                _reply({"ok": True, "state": st.snapshot()})
            except Exception as e:
                st.last_error = str(e)
                st.audit_add("disconnect", False, str(e))
                _reply({"ok": False, "error": str(e), "state": st.snapshot()})
            continue

        if cmd == "connect":
            enabled = bool(req.get("enabled", True))
            if not enabled:
                st.connection_state = "offline"
                st.state_reason = "cloudpub_disabled"
                st.last_error = None
                st.audit_add("connect", False, "disabled")
                _reply({"ok": False, "error": "cloudpub_disabled", "state": st.snapshot()})
                continue

            token = str(req.get("token") or "").strip()
            email = str(req.get("email") or "").strip()
            password = str(req.get("password") or "").strip()
            server_ip = str(req.get("server_ip") or "").strip()

            # FIX: "***" из UI не должен перебивать реальные значения
            if _is_masked_secret(token):
                token = ""
            if _is_masked_secret(password):
                password = ""

            auth_mode = "emailpass" if (email and password) else "token"
            if auth_mode == "token" and not token:
                st.audit_add("connect", False, "missing token")
                _reply({"ok": False, "error": "cloudpub_not_configured_token", "state": st.snapshot()})
                continue
            if auth_mode == "emailpass" and (not email or not password):
                st.audit_add("connect", False, "missing email/password")
                _reply({"ok": False, "error": "cloudpub_not_configured_emailpass", "state": st.snapshot()})
                continue

            try:
                if st.conn is not None and st.endpoint_guid:
                    try:
                        st.conn.unpublish(st.endpoint_guid)
                    except Exception:
                        pass
            except Exception:
                pass
            st.conn = None
            st.endpoint_guid = None
            st.public_url = None

            try:
                kwargs: Dict[str, Any] = {
                    "log_level": str(req.get("log_level") or "error"),
                    "verbose": bool(req.get("verbose", False)),
                }
                if auth_mode == "token":
                    kwargs["token"] = token
                else:
                    kwargs["email"] = email
                    kwargs["password"] = password

                try:
                    conn = Connection(**kwargs)
                except Exception as e1:
                    msg1 = str(e1)
                    if "Failed to initialize logging" in msg1:
                        try:
                            kwargs2 = dict(kwargs)
                            kwargs2.pop("log_level", None)
                            kwargs2["verbose"] = False
                            conn = Connection(**kwargs2)
                        except Exception:
                            kwargs3 = dict(kwargs)
                            kwargs3["log_level"] = "off"
                            kwargs3["verbose"] = False
                            conn = Connection(**kwargs3)
                    else:
                        raise

                endpoint = conn.publish(
                    Protocol.HTTP,
                    "127.0.0.1:8080",
                    name="LPR GateBox UI",
                    auth=Auth.NONE,
                )

                st.conn = conn
                st.endpoint_guid = getattr(endpoint, "guid", None)
                st.public_url = getattr(endpoint, "url", None)
                st.server_ip = server_ip
                st.last_ok_ts = int(time.time())
                st.last_error = None
                st.connection_state = "online"
                st.state_reason = ""
                st.audit_add("connect", True, f"{auth_mode} url={st.public_url}")

                _reply({"ok": True, "state": st.snapshot(), "auth_mode": auth_mode})
            except Exception as e:
                st.conn = None
                st.endpoint_guid = None
                st.public_url = None
                st.last_ok_ts = None
                st.last_error = str(e)
                st.connection_state = "offline"
                st.state_reason = "cloudpub_connect_failed"
                st.audit_add("connect", False, f"{auth_mode} error={e}")

                _reply({"ok": False, "error": str(e), "state": st.snapshot(), "auth_mode": auth_mode})
            continue

        if cmd == "quit":
            _reply({"ok": True, "bye": True})
            return 0

        _reply({"ok": False, "error": f"unknown_cmd:{cmd}"})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())