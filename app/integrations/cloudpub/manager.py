# =========================================================
# Файл: app/integrations/cloudpub/manager.py
# Проект: LPR GateBox
# Версия: v0.3.39-cloudpub-mask-fix
# Обновлено: 2026-02-20 (UTC+1)
# Автор: Александр + ChatGPT
#
# FIX:
# - Игнорируем замаскированные секреты ("***", "•••") при connect(),
#   чтобы UI после рестарта не перебивал реальные значения из settings.json.
# =========================================================

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from cloudpub_python_sdk import Connection, Protocol, Auth  # type: ignore


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


_bootstrap_env()


def _is_masked_secret(v: str) -> bool:
    s = (v or "").strip()
    return s in ("***", "•••")


def _normalize_url(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    s = str(u).strip()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    try:
        p = urlparse(s)
        scheme = p.scheme or "https"
        netloc = p.netloc or p.path
        path = p.path if p.netloc else ""
        if not path:
            path = "/"
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception:
        if not s.endswith("/"):
            s += "/"
        return s


@dataclass
class CloudpubState:
    connection_state: str = "offline"  # offline|online|disabled
    state_reason: str = ""
    server_ip: str = ""
    public_url: Optional[str] = None
    ui_url: Optional[str] = None
    management_url: Optional[str] = None
    last_ok_ts: Optional[int] = None
    last_error: Optional[str] = None
    audit: list[dict] = field(default_factory=list)


class CloudpubManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = CloudpubState()
        self._conn: Any = None
        self._endpoint_guid: Optional[str] = None

        self._expire_at_ts: Optional[float] = None
        self._expire_thread: Optional[threading.Thread] = None

    def _audit(self, action: str, ok: bool, detail: str) -> None:
        try:
            self._state.audit.insert(
                0,
                {"ts": int(time.time()), "action": action, "ok": bool(ok), "detail": str(detail)[:500]},
            )
            self._state.audit = self._state.audit[:200]
        except Exception:
            pass

    def clear_audit(self) -> None:
        with self._lock:
            self._state.audit.clear()

    def state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "connection_state": self._state.connection_state,
                "state_reason": self._state.state_reason,
                "mode": "sdk",
                "server_ip": self._state.server_ip,
                "public_url": self._state.public_url,
                "ui_url": self._state.ui_url,
                "management_url": self._state.management_url,
                "last_ok_ts": self._state.last_ok_ts,
                "last_error": self._state.last_error,
                "audit": list(self._state.audit)[:50],
            }

    def _disconnect_locked(self, reason: str) -> None:
        try:
            if self._conn is not None:
                try:
                    if self._endpoint_guid:
                        self._conn.unpublish(self._endpoint_guid)
                except Exception:
                    pass
                try:
                    if hasattr(self._conn, "close"):
                        self._conn.close()
                except Exception:
                    pass
        finally:
            self._conn = None
            self._endpoint_guid = None
            self._state.connection_state = "offline"
            self._state.state_reason = "disconnected" if reason == "manual" else reason
            self._state.public_url = None
            self._state.ui_url = None
            self._state.management_url = None
            self._state.last_error = None

    def disconnect(self) -> Dict[str, Any]:
        with self._lock:
            if self._conn is None:
                self._audit("disconnect", True, "already_offline")
                return self.state()
            self._disconnect_locked(reason="manual")
            self._audit("disconnect", True, "manual")
            return self.state()

    def _ensure_expire_thread(self) -> None:
        if self._expire_thread and self._expire_thread.is_alive():
            return

        def _loop():
            while True:
                time.sleep(1.0)
                with self._lock:
                    if self._expire_at_ts is None:
                        return
                    if time.time() >= self._expire_at_ts:
                        try:
                            self._disconnect_locked(reason="auto_expire")
                            self._audit("auto_expire", True, "expired")
                        finally:
                            self._expire_at_ts = None
                        return

        self._expire_thread = threading.Thread(target=_loop, daemon=True)
        self._expire_thread.start()

    def connect(
        self,
        *,
        enabled: bool,
        token: str | None = None,
        email: str | None = None,
        password: str | None = None,
        server_ip: str = "",
        auto_expire_min: int = 0,
    ) -> Dict[str, Any]:
        with self._lock:
            _bootstrap_env()

            if not enabled:
                self._state.connection_state = "disabled"
                self._state.state_reason = "cloudpub_disabled"
                self._state.last_error = None
                self._audit("connect", False, "disabled")
                raise RuntimeError("cloudpub_disabled")

            t = (token or "").strip()
            em = (email or "").strip()
            pw = (password or "").strip()

            # FIX: UI может прислать "***" — игнорируем как "пусто"
            if _is_masked_secret(t):
                t = ""
            if _is_masked_secret(pw):
                pw = ""

            auth_mode = "emailpass" if (em and pw) else "token"

            if auth_mode == "emailpass":
                if not em or not pw:
                    self._state.connection_state = "offline"
                    self._state.state_reason = "cloudpub_not_configured"
                    self._state.last_error = "missing email/password"
                    self._audit("connect", False, "missing email/password")
                    raise RuntimeError("cloudpub_not_configured_emailpass")
                conn = Connection(email=em, password=pw)
            else:
                if not t:
                    self._state.connection_state = "offline"
                    self._state.state_reason = "cloudpub_not_configured"
                    self._state.last_error = "missing token"
                    self._audit("connect", False, "missing token")
                    raise RuntimeError("cloudpub_not_configured_token")
                conn = Connection(token=t)

            if self._conn is not None:
                try:
                    self._disconnect_locked(reason="reconnect")
                except Exception:
                    pass

            try:
                endpoint = conn.publish(
                    Protocol.HTTP,
                    "127.0.0.1:8080",
                    name="LPR GateBox UI",
                    auth=Auth.NONE,
                )
            except Exception as e:
                self._conn = None
                self._endpoint_guid = None
                self._state.connection_state = "offline"
                self._state.state_reason = "cloudpub_connect_failed"
                self._state.public_url = None
                self._state.ui_url = None
                self._state.management_url = None
                self._state.last_ok_ts = None
                self._state.last_error = str(e)
                self._audit("connect", False, f"{auth_mode} error={e}")
                raise RuntimeError(f"cloudpub_connect_failed: {e}")

            self._conn = conn
            self._endpoint_guid = getattr(endpoint, "guid", None)

            public_url = _normalize_url(getattr(endpoint, "url", None))
            ui_url = public_url

            self._state.connection_state = "online"
            self._state.state_reason = ""
            self._state.server_ip = (server_ip or "").strip()
            self._state.public_url = public_url
            self._state.ui_url = ui_url
            self._state.management_url = ui_url
            self._state.last_ok_ts = int(time.time())
            self._state.last_error = None

            if int(auto_expire_min or 0) > 0:
                self._expire_at_ts = time.time() + float(auto_expire_min) * 60.0
                self._ensure_expire_thread()
            else:
                self._expire_at_ts = None

            self._audit("connect", True, f"{auth_mode} url={public_url}")
            return self.state()


cloudpub_manager = CloudpubManager()