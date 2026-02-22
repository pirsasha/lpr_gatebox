# =========================================================
# Файл: app/integrations/cloudpub/manager.py
# Проект: LPR GateBox
# Версия: v0.3.43-cloudpub-docker-only
# Обновлено: 2026-02-22
#
# Что сделано:
# - CHG: Убрана SDK-интеграция CloudPub полностью (только docker-режим).
# - NEW: Управление cloudpub через docker run/stop/rm, URL парсится из docker logs.
# - NEW: auto_expire_min (авто-отключение).
# - NEW: audit лог событий для UI диагностики.
# =========================================================

from __future__ import annotations

import os
import re
import threading
import time
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse


def _bootstrap_env() -> None:
    os.environ.setdefault("HOME", "/tmp")
    os.environ.setdefault("TMPDIR", "/tmp")
    try:
        os.makedirs("/tmp", exist_ok=True)
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


def _normalize_origin_target(server_ip: str, default_port: int = 8080) -> str:
    """
    Возвращает строго "host:port".

    Если пусто -> "gatebox:8080" (важно: cloudpub контейнер НЕ видит localhost gatebox)
    """
    s = (server_ip or "").strip()
    if not s:
        return "gatebox:8080"

    if "://" in s:
        try:
            p = urlparse(s)
            host = p.hostname or ""
            port = p.port or default_port
            if host:
                return f"{host}:{port}"
        except Exception:
            pass

    if ":" in s:
        return s

    return f"{s}:{default_port}"


@dataclass
class CloudpubState:
    connection_state: str = "offline"  # offline|online|disabled
    state_reason: str = ""
    mode: str = "docker"
    origin_target: str = ""
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

        self._docker_container_name: str = (
            os.environ.get("CLOUDPUB_DOCKER_NAME", "lpr_gatebox_cloudpub").strip()
            or "lpr_gatebox_cloudpub"
        )
        self._docker_network: str = (
            os.environ.get("CLOUDPUB_DOCKER_NETWORK", "lpr_gatebox_default").strip()
            or "lpr_gatebox_default"
        )
        self._docker_image: str = (
            os.environ.get("CLOUDPUB_DOCKER_IMAGE", "cloudpub/cloudpub:latest").strip()
            or "cloudpub/cloudpub:latest"
        )

        self._expire_at_ts: Optional[float] = None
        self._expire_thread: Optional[threading.Thread] = None

    # ----------------- helpers -----------------

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
                "mode": self._state.mode,
                "origin_target": self._state.origin_target,
                "server_ip": self._state.origin_target,  # backward-compat for UI
                "public_url": self._state.public_url,
                "ui_url": self._state.ui_url,
                "management_url": self._state.management_url,
                "last_ok_ts": self._state.last_ok_ts,
                "last_error": self._state.last_error,
                "audit": list(self._state.audit)[:50],
            }

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

    # ----------------- docker ops -----------------

    def _docker_run(self, args: list[str], timeout: float = 20.0) -> tuple[int, str, str]:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")

    def _docker_container_exists(self) -> bool:
        rc, out, _ = self._docker_run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=10.0)
        if rc != 0:
            return False
        names = {x.strip() for x in out.splitlines() if x.strip()}
        return self._docker_container_name in names

    def _docker_stop_rm(self) -> None:
        self._docker_run(["docker", "stop", self._docker_container_name], timeout=15.0)
        self._docker_run(["docker", "rm", "-f", self._docker_container_name], timeout=15.0)

    def _docker_logs(self) -> str:
        rc, out, err = self._docker_run(["docker", "logs", self._docker_container_name], timeout=10.0)
        if rc != 0:
            return (out + "\n" + err).strip()
        return out.strip()

    def _docker_parse_public_url(self, logs: str) -> Optional[str]:
        if not logs:
            return None
        # стараемся вытащить ссылку вида https://....cloudpub.ru/...
        m = re.search(r"(https?://[^\s]+cloudpub\.ru[^\s]*)", logs)
        if not m:
            return None
        return _normalize_url(m.group(1))

    # ----------------- connect/disconnect -----------------

    def _connect_docker_locked(self, *, token: str, origin_target: str) -> Dict[str, Any]:
        if not token:
            raise RuntimeError("cloudpub_not_configured_token")

        if self._docker_container_exists():
            self._docker_stop_rm()

        # Запускаем cloudpub контейнер в сети compose, чтобы видел gatebox:8080
        cmd = [
            "docker", "run", "-d",
            "--name", self._docker_container_name,
            "--network", self._docker_network,
            "-e", f"TOKEN={token}",
            self._docker_image,
            "publish", "http", origin_target,
        ]

        rc, out, err = self._docker_run(cmd, timeout=30.0)
        if rc != 0:
            raise RuntimeError(f"cloudpub_docker_run_failed: {err.strip() or out.strip() or 'unknown'}")

        public_url: Optional[str] = None
        last_logs = ""
        for _ in range(40):  # ~10 сек
            time.sleep(0.25)
            last_logs = self._docker_logs()
            public_url = self._docker_parse_public_url(last_logs)
            if public_url:
                break

        if not public_url:
            raise RuntimeError(f"cloudpub_docker_no_url: {last_logs[-800:]}")

        self._state.connection_state = "online"
        self._state.state_reason = ""
        self._state.mode = "docker"
        self._state.origin_target = origin_target
        self._state.public_url = public_url
        self._state.ui_url = public_url
        self._state.management_url = public_url
        self._state.last_ok_ts = int(time.time())
        self._state.last_error = None

        self._audit("connect", True, f"docker target={origin_target} url={public_url}")
        return self.state()

    def _disconnect_locked(self, reason: str) -> None:
        try:
            self._docker_stop_rm()
        except Exception:
            pass

        self._state.connection_state = "offline"
        self._state.state_reason = "disconnected" if reason == "manual" else reason
        self._state.public_url = None
        self._state.ui_url = None
        self._state.management_url = None
        self._state.last_error = None
        self._state.last_ok_ts = None

    def disconnect(self) -> Dict[str, Any]:
        with self._lock:
            self._disconnect_locked(reason="manual")
            self._audit("disconnect", True, "manual")
            return self.state()

    def connect(
        self,
        *,
        enabled: bool,
        token: str | None = None,
        server_ip: str = "",
        auto_expire_min: int = 0,
        backend: str | None = None,   # оставляем для совместимости UI (игнорируем)
        protocol: str | None = None,  # оставляем для совместимости UI (игнорируем)
        email: str | None = None,     # совместимость (игнорируем)
        password: str | None = None,  # совместимость (игнорируем)
    ) -> Dict[str, Any]:
        _ = backend, protocol, email, password

        with self._lock:
            _bootstrap_env()

            if not enabled:
                self._state.connection_state = "disabled"
                self._state.state_reason = "cloudpub_disabled"
                self._state.last_error = None
                self._audit("connect", False, "disabled")
                raise RuntimeError("cloudpub_disabled")

            t = (token or "").strip()
            if _is_masked_secret(t):
                t = ""

            origin_target = _normalize_origin_target(server_ip)

            # переподключение
            try:
                self._disconnect_locked(reason="reconnect")
            except Exception:
                pass

            try:
                st = self._connect_docker_locked(token=t, origin_target=origin_target)
            except Exception as e:
                self._state.connection_state = "offline"
                self._state.state_reason = "cloudpub_connect_failed"
                self._state.mode = "docker"
                self._state.origin_target = origin_target
                self._state.last_error = str(e)
                self._audit("connect", False, f"docker target={origin_target} err={e}")
                raise RuntimeError(f"cloudpub_connect_failed: {e}")

            # auto-expire
            if int(auto_expire_min or 0) > 0:
                self._expire_at_ts = time.time() + float(auto_expire_min) * 60.0
                self._ensure_expire_thread()
            else:
                self._expire_at_ts = None

            return st


cloudpub_manager = CloudpubManager()