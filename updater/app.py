# =========================================================
# Файл: updater/app.py
# Проект: LPR GateBox
# Версия: v0.3.15-updater-metrics-ui-schema-ru
# Изменено: 2026-02-11
#
# Что сделано:
# - NEW: добавлен endpoint GET /metrics для UI "Система → Ресурсы"
# - FIX: /metrics теперь возвращает СХЕМУ, которую ждёт UI:
#        host.cpu_pct, host.mem_*_mb, host.disk_*.{used_mb,total_mb}, containers[].raw_mem
# - NEW: host.cpu_pct вычисляется по /proc/stat (дельта за 150мс)
# - KEEP: текущая логика обновлений, effective compose persist и helper net-heal без изменений
# =========================================================

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import subprocess
import threading
import time
import os
import zipfile
import re
import shutil
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from typing import Optional, Dict, Any, Tuple, List


# -------------------------
# Config / state
# -------------------------

PORT = int(os.environ.get("UPDATER_PORT", "9010"))

STATE: Dict[str, Any] = {
    "running": False,
    "step": None,
    "last_result": None,
    "last_check": None,
    "last_error": None,
    "rollback_path": None,
    "rollback_saved_at": None,
    "last_action": None,
    "compose_effective": None,
    "compose_effective_persist": None,
}
LOG: List[str] = []

PROJECT_DIR = os.environ.get("PROJECT_DIR", "/project").strip() or "/project"
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose.yml").strip() or "docker-compose.yml"
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config").strip() or "/config"

COMPOSE_PROJECT_NAME = os.environ.get("COMPOSE_PROJECT_NAME", "lpr_gatebox").strip() or "lpr_gatebox"

FALLBACK_BUILD = os.environ.get("UPDATE_FALLBACK_BUILD", "0") == "1"

HEALTH_URL = os.environ.get("HEALTH_URL", "").strip()
HEALTH_TIMEOUT_SEC = float(os.environ.get("HEALTH_TIMEOUT_SEC", "60") or "60")

UPDATE_SERVICES = [s.strip() for s in os.environ.get("UPDATE_SERVICES", "gatebox,rtsp_worker").split(",") if s.strip()]
if not UPDATE_SERVICES:
    UPDATE_SERVICES = ["gatebox", "rtsp_worker"]

ROLLBACK_PATH = Path(CONFIG_DIR) / "rollback.json"
ROLLBACK_OVERRIDE_PATH = Path("/tmp/rollback.override.yml")

# effective compose внутри контейнера
EFFECTIVE_COMPOSE_PATH = Path("/tmp/docker-compose.effective.yml")
# effective compose в bind-mounted проекте (чтобы видел helper)
EFFECTIVE_COMPOSE_PERSIST = Path(PROJECT_DIR) / ".updater" / "docker-compose.effective.yml"

SELF_CONTAINER = os.environ.get("SELF_CONTAINER", "").strip() or os.environ.get("HOSTNAME", "").strip() or "updater"

_HELPER_IMAGE: Optional[str] = None
_LAST_RUN_TAIL: List[str] = []


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.append(line)
    if len(LOG) > 1500:
        LOG.pop(0)


def _tail_add(line: str):
    _LAST_RUN_TAIL.append(line)
    if len(_LAST_RUN_TAIL) > 220:
        del _LAST_RUN_TAIL[:80]


def run(cmd: list[str], cwd: str = PROJECT_DIR) -> int:
    global _LAST_RUN_TAIL
    _LAST_RUN_TAIL = []

    log(f"$ {' '.join(cmd)}")
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert p.stdout is not None
    for line in p.stdout:
        s = line.rstrip()
        log(s)
        _tail_add(s)
    p.wait()
    return p.returncode


def run_out(cmd: list[str], cwd: str = PROJECT_DIR, timeout_sec: float = 12.0) -> str:
    log(f"$ {' '.join(cmd)}")
    out = subprocess.check_output(
        cmd,
        cwd=cwd,
        text=True,
        stderr=subprocess.STDOUT,
        timeout=timeout_sec,
    )
    for line in out.splitlines():
        log(line.rstrip())
    return out


def _abs_compose_path() -> str:
    p = Path(COMPOSE_FILE)
    if p.is_absolute():
        return str(p)
    return str(Path(PROJECT_DIR) / p)


# =========================================================
# NEW/FIX: metrics helpers (UI schema)
# =========================================================

_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?\s*$")


def _bytes_to_mb(x: Optional[int]) -> Optional[float]:
    if x is None:
        return None
    return float(x) / (1024.0 * 1024.0)


def _read_meminfo_bytes() -> Dict[str, int]:
    """
    Linux-only: /proc/meminfo.
    Возвращаем байты.
    """
    out: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    key = parts[0][:-1]
                    val = int(parts[1])
                    unit = parts[2] if len(parts) >= 3 else ""
                    if unit.lower() == "kb":
                        out[key] = val * 1024
                    else:
                        out[key] = val
    except Exception:
        pass
    return out


def _read_proc_stat() -> Optional[Tuple[int, int]]:
    """
    /proc/stat: возвращаем (total, idle) в тиках.
    """
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            line = f.readline()
        if not line.startswith("cpu "):
            return None
        parts = line.split()
        # cpu user nice system idle iowait irq softirq steal guest guest_nice
        nums = [int(x) for x in parts[1:] if x.isdigit() or (x and x[0].isdigit())]
        if len(nums) < 4:
            return None
        user, nice, system, idle = nums[0], nums[1], nums[2], nums[3]
        iowait = nums[4] if len(nums) > 4 else 0
        irq = nums[5] if len(nums) > 5 else 0
        softirq = nums[6] if len(nums) > 6 else 0
        steal = nums[7] if len(nums) > 7 else 0
        idle_all = idle + iowait
        non_idle = user + nice + system + irq + softirq + steal
        total = idle_all + non_idle
        return total, idle_all
    except Exception:
        return None


def _cpu_pct_sample(delay_sec: float = 0.15) -> Optional[float]:
    """
    CPU% по дельте /proc/stat.
    """
    a = _read_proc_stat()
    if not a:
        return None
    time.sleep(delay_sec)
    b = _read_proc_stat()
    if not b:
        return None
    total1, idle1 = a
    total2, idle2 = b
    dt = total2 - total1
    di = idle2 - idle1
    if dt <= 0:
        return None
    busy = max(0.0, float(dt - di))
    pct = (busy / float(dt)) * 100.0
    return pct


def _parse_size_to_bytes(s: str) -> Optional[int]:
    """
    Парсим размеры docker stats: '12.3MiB', '1.02GiB', '500kB', '1024B'
    """
    s = (s or "").strip()
    if not s:
        return None
    m = _SIZE_RE.match(s)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").strip()

    unit_map = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    if not unit:
        return int(num)
    k = unit.lower()
    if k not in unit_map:
        return None
    return int(num * unit_map[k])


def _disk_usage(path: str) -> Dict[str, Any]:
    try:
        du = shutil.disk_usage(path)
        return {"path": path, "total": du.total, "used": du.used, "free": du.free}
    except Exception as e:
        return {"path": path, "error": str(e)}


def _docker_stats(timeout_sec: float = 2.5) -> List[Dict[str, Any]]:
    """
    docker stats --no-stream
    Возвращаем список контейнеров, UI ждёт: name, cpu_pct, raw_mem
    """
    fmt = "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"
    try:
        out = subprocess.check_output(
            ["docker", "stats", "--no-stream", "--format", fmt],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=timeout_sec,
        )
    except Exception as e:
        return [{"error": f"docker_stats_failed: {e}"}]

    items: List[Dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue

        name = parts[0].strip()
        cpu_s = parts[1].strip().replace("%", "").strip()
        mem_usage_s = parts[2].strip()  # "593.3MiB / 15.54GiB"
        mem_pct_s = parts[3].strip().replace("%", "").strip()

        mem_used_b = None
        mem_limit_b = None
        if " / " in mem_usage_s:
            a, b = mem_usage_s.split(" / ", 1)
            mem_used_b = _parse_size_to_bytes(a.strip())
            mem_limit_b = _parse_size_to_bytes(b.strip())

        item = {
            "name": name,
            "cpu_pct": float(cpu_s) if cpu_s else None,

            # FIX: UI рисует именно raw_mem
            "raw_mem": mem_usage_s,

            # оставляем полезные поля "на будущее"
            "mem_usage_raw": mem_usage_s,
            "mem_used_bytes": mem_used_b,
            "mem_limit_bytes": mem_limit_b,
            "mem_pct": float(mem_pct_s) if mem_pct_s else None,
        }
        items.append(item)

    return items


def build_metrics_payload() -> Dict[str, Any]:
    """
    FIX: возвращаем СХЕМУ, которую ждёт ui/src/pages/System.jsx
    """
    mem = _read_meminfo_bytes()
    mem_total_b = mem.get("MemTotal")
    mem_avail_b = mem.get("MemAvailable")

    mem_used_b = None
    if mem_total_b is not None and mem_avail_b is not None:
        mem_used_b = max(0, mem_total_b - mem_avail_b)

    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = load5 = load15 = None

    cpu_pct = _cpu_pct_sample(delay_sec=0.15)

    d_root = _disk_usage("/")
    d_project = _disk_usage("/project")
    d_config = _disk_usage("/config")

    def disk_mb(d: Dict[str, Any]) -> Dict[str, Any]:
        if d.get("error"):
            return {"error": d.get("error")}
        total_b = d.get("total")
        used_b = d.get("used")
        free_b = d.get("free")
        return {
            "total_mb": _bytes_to_mb(total_b),
            "used_mb": _bytes_to_mb(used_b),
            "free_mb": _bytes_to_mb(free_b),
        }

    host = {
        "ts": int(time.time()),
        "load1": load1,
        "load5": load5,
        "load15": load15,

        # UI ждёт:
        "cpu_pct": cpu_pct,
        "mem_total_mb": _bytes_to_mb(mem_total_b),
        "mem_used_mb": _bytes_to_mb(mem_used_b),
        "mem_avail_mb": _bytes_to_mb(mem_avail_b),

        "disk_root": disk_mb(d_root),
        "disk_project": disk_mb(d_project),
        "disk_config": disk_mb(d_config),
    }

    containers = _docker_stats()

    return {
        "ok": True,
        "ts": int(time.time()),
        "host": host,
        "containers": containers,
    }


# -------------------------
# Compose detection (v1/v2)
# -------------------------

_COMPOSE_KIND: Optional[str] = None  # "v2" | "v1"


def detect_compose_kind() -> str:
    global _COMPOSE_KIND
    if _COMPOSE_KIND:
        return _COMPOSE_KIND

    try:
        rc = run(["docker", "compose", "version"], cwd="/")
        if rc == 0:
            _COMPOSE_KIND = "v2"
            log("compose detected: docker compose")
            return _COMPOSE_KIND
    except Exception:
        pass

    try:
        rc = run(["docker-compose", "version"], cwd="/")
        if rc == 0:
            _COMPOSE_KIND = "v1"
            log("compose detected: docker-compose")
            return _COMPOSE_KIND
    except Exception:
        pass

    raise RuntimeError("compose not found: neither `docker compose` nor `docker-compose` available")


# -------------------------
# Host path resolving (Win+Linux)
# -------------------------

_HOST_PROJECT_SOURCE: Optional[str] = None
_HOST_CONFIG_SOURCE: Optional[str] = None

_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_windows_abs_path(p: str) -> bool:
    return bool(_WIN_ABS_RE.match(p.strip())) if isinstance(p, str) else False


def _normalize_win_path_for_yaml(p: str) -> str:
    return p.strip().replace("\\", "/")


def _docker_inspect_container(name_or_id: str) -> Dict[str, Any]:
    raw = subprocess.check_output(["docker", "inspect", name_or_id], text=True, stderr=subprocess.STDOUT)
    arr = json.loads(raw)
    if not arr:
        raise RuntimeError(f"docker inspect returned empty for {name_or_id}")
    return arr[0]


def _resolve_host_bind_source(dest_path: str) -> Optional[str]:
    tried = []
    for ident in [SELF_CONTAINER, "updater"]:
        if not ident:
            continue
        tried.append(ident)
        try:
            j = _docker_inspect_container(ident)
            mounts = j.get("Mounts") or []
            for m in mounts:
                if (m.get("Type") == "bind") and (m.get("Destination") == dest_path):
                    src = m.get("Source")
                    if isinstance(src, str) and src.strip():
                        return src.strip()
        except Exception:
            continue

    log(f"WARN: cannot resolve host bind source for {dest_path}; tried={tried}")
    return None


def _ensure_host_paths():
    global _HOST_PROJECT_SOURCE, _HOST_CONFIG_SOURCE
    if _HOST_PROJECT_SOURCE and _HOST_CONFIG_SOURCE:
        return

    _HOST_PROJECT_SOURCE = _resolve_host_bind_source(PROJECT_DIR)
    _HOST_CONFIG_SOURCE = _resolve_host_bind_source(CONFIG_DIR)

    if not _HOST_CONFIG_SOURCE and _HOST_PROJECT_SOURCE:
        _HOST_CONFIG_SOURCE = str(Path(_HOST_PROJECT_SOURCE) / "config")

    if not _HOST_PROJECT_SOURCE:
        raise RuntimeError("cannot resolve HOST project path for /project bind mount (updater cannot rewrite compose)")
    if not _HOST_CONFIG_SOURCE:
        raise RuntimeError("cannot resolve HOST config path for /config bind mount (updater cannot rewrite compose)")

    log(f"host paths resolved: HOST_PROJECT={_HOST_PROJECT_SOURCE} HOST_CONFIG={_HOST_CONFIG_SOURCE}")


def _rewrite_bind_source_to_host(p: str) -> str:
    p = p.strip()

    if p == ".":
        return _HOST_PROJECT_SOURCE  # type: ignore[arg-type]

    if p.startswith("./"):
        return str(Path(_HOST_PROJECT_SOURCE) / p[2:])  # type: ignore[arg-type]

    if p.startswith(PROJECT_DIR.rstrip("/") + "/"):
        suffix = p[len(PROJECT_DIR.rstrip("/")) + 1 :]
        return str(Path(_HOST_PROJECT_SOURCE) / suffix)  # type: ignore[arg-type]

    if p == PROJECT_DIR:
        return _HOST_PROJECT_SOURCE  # type: ignore[arg-type]

    if p.startswith(CONFIG_DIR.rstrip("/") + "/"):
        suffix = p[len(CONFIG_DIR.rstrip("/")) + 1 :]
        return str(Path(_HOST_CONFIG_SOURCE) / suffix)  # type: ignore[arg-type]

    if p == CONFIG_DIR:
        return _HOST_CONFIG_SOURCE  # type: ignore[arg-type]

    return p


def _rewrite_build_context_to_container(p: str) -> str:
    p = p.strip()

    if _is_windows_abs_path(p):
        return PROJECT_DIR

    if p == ".":
        return PROJECT_DIR

    if p.startswith("./"):
        return str(Path(PROJECT_DIR) / p[2:])

    return p


def _parse_short_volume_token_windows_safe(token: str) -> Optional[Tuple[str, str, Optional[str]]]:
    token = token.strip()
    idx = token.find(":/")
    if idx <= 0:
        parts = token.split(":")
        if len(parts) < 2:
            return None
        src = parts[0].strip()
        target = parts[1].strip()
        mode = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
        return src, target, mode

    src = token[:idx].strip()
    rest = token[idx + 1 :].strip()
    parts2 = rest.split(":", 1)
    target = parts2[0].strip()
    mode = parts2[1].strip() if len(parts2) == 2 and parts2[1].strip() else None
    return src, target, mode


def ensure_effective_compose_file() -> str:
    """
    Генерируем effective compose:
    - /tmp/docker-compose.effective.yml (локально)
    - /project/.updater/docker-compose.effective.yml (persist на хосте через bind mount)
    """
    _ensure_host_paths()
    src_path = Path(_abs_compose_path())
    if not src_path.exists():
        raise FileNotFoundError(f"compose file not found: {src_path}")

    lines = src_path.read_text(encoding="utf-8").splitlines()
    out: List[str] = []

    for line in lines:
        s = line

        # long syntax volumes: source: ./models
        if "source:" in s:
            parts = s.split("source:", 1)
            left = parts[0] + "source:"
            right = parts[1].strip()
            if right:
                raw = right.strip().strip('"').strip("'")
                newp = _rewrite_bind_source_to_host(raw)
                if _is_windows_abs_path(newp):
                    newp = _normalize_win_path_for_yaml(newp)
                s = f"{left} {newp}"

        # build context
        if "context:" in s:
            parts = s.split("context:", 1)
            left = parts[0] + "context:"
            right = parts[1].strip()
            raw = right.strip().strip('"').strip("'")
            newp = _rewrite_build_context_to_container(raw)
            s = f"{left} {newp}"

        # short syntax volumes
        stripped = s.strip()
        if stripped.startswith("- "):
            token = stripped[2:].strip()
            is_bind_candidate = (
                token.startswith("./")
                or token.startswith(".:")
                or token.startswith(PROJECT_DIR)
                or token.startswith(CONFIG_DIR)
                or _is_windows_abs_path(token)
            )
            if is_bind_candidate:
                parsed = _parse_short_volume_token_windows_safe(token)
                if parsed:
                    host_src, target, mode = parsed
                    new_src = _rewrite_bind_source_to_host(host_src)

                    prefix = s[: s.find("- ")]
                    indent2 = prefix + "  "

                    if _is_windows_abs_path(new_src):
                        new_src = _normalize_win_path_for_yaml(new_src)

                        read_only = False
                        if mode:
                            m = mode.strip().lower()
                            if m == "ro":
                                read_only = True
                            elif m == "rw":
                                read_only = False
                            else:
                                read_only = ("ro" in m)

                        out.append(f"{prefix}- type: bind")
                        out.append(f"{indent2}source: {new_src}")
                        out.append(f"{indent2}target: {target}")
                        if read_only:
                            out.append(f"{indent2}read_only: true")
                        continue

                    rebuilt = f"{new_src}:{target}"
                    if mode:
                        rebuilt += f":{mode}"
                    out.append(f"{prefix}- {rebuilt}")
                    continue

        out.append(s)

    text = "\n".join(out) + "\n"

    # 1) /tmp (внутри контейнера)
    EFFECTIVE_COMPOSE_PATH.write_text(text, encoding="utf-8")
    STATE["compose_effective"] = str(EFFECTIVE_COMPOSE_PATH)
    log(f"compose effective written: {EFFECTIVE_COMPOSE_PATH}")

    # 2) persist в /project/.updater (это хост через bind mount)
    try:
        EFFECTIVE_COMPOSE_PERSIST.parent.mkdir(parents=True, exist_ok=True)
        EFFECTIVE_COMPOSE_PERSIST.write_text(text, encoding="utf-8")
        STATE["compose_effective_persist"] = str(EFFECTIVE_COMPOSE_PERSIST)
        log(f"compose effective persisted: {EFFECTIVE_COMPOSE_PERSIST}")
    except Exception as e:
        log(f"WARN: cannot persist effective compose to {EFFECTIVE_COMPOSE_PERSIST}: {e}")

    return str(EFFECTIVE_COMPOSE_PATH)


def compose_cmd(*args: str, extra_files: Optional[list[str]] = None) -> list[str]:
    kind = detect_compose_kind()

    effective = ensure_effective_compose_file()
    files = [effective]
    if extra_files:
        files.extend(extra_files)

    if kind == "v2":
        cmd = ["docker", "compose", "-p", COMPOSE_PROJECT_NAME]
        for f in files:
            cmd += ["-f", f]
        cmd += [*args]
        return cmd

    cmd = ["docker-compose", "-p", COMPOSE_PROJECT_NAME]
    for f in files:
        cmd += ["-f", f]
    cmd += [*args]
    return cmd


# -------------------------
# Preflight / helper image
# -------------------------

def _preflight_compose_file():
    compose_path = Path(_abs_compose_path())
    if not compose_path.exists():
        raise FileNotFoundError(f"compose file not found: {compose_path}")
    if not compose_path.is_file():
        raise FileNotFoundError(f"compose path is not a file: {compose_path}")


def _detect_self_image() -> str:
    global _HELPER_IMAGE
    if _HELPER_IMAGE:
        return _HELPER_IMAGE

    j = _docker_inspect_container(SELF_CONTAINER)
    cfg = j.get("Config") or {}
    img = cfg.get("Image")
    if not isinstance(img, str) or not img.strip():
        j2 = _docker_inspect_container("updater")
        cfg2 = j2.get("Config") or {}
        img = cfg2.get("Image")

    if not isinstance(img, str) or not img.strip():
        raise RuntimeError("cannot detect updater image for helper")

    _HELPER_IMAGE = img.strip()
    log(f"helper image detected: {_HELPER_IMAGE}")
    return _HELPER_IMAGE


# -------------------------
# Health wait
# -------------------------

def _health_candidates() -> List[str]:
    cands: List[str] = []
    if HEALTH_URL:
        cands.append(HEALTH_URL)
    for u in [
        "http://gatebox:8080/api/v1/health",
        "http://host.docker.internal:8080/api/v1/health",
    ]:
        if u not in cands:
            cands.append(u)
    return cands


def wait_health(timeout_sec: float) -> bool:
    t0 = time.time()
    last_err = None
    cands = _health_candidates()
    log("health candidates: " + ", ".join(cands))

    while (time.time() - t0) < timeout_sec:
        for u in cands:
            try:
                with urlopen(u, timeout=3) as r:
                    if r.status == 200:
                        return True
            except URLError as e:
                last_err = f"{u} -> {e}"
            except Exception as e:
                last_err = f"{u} -> {e}"
        time.sleep(1.0)

    if last_err:
        log(f"health wait timeout; last_err={last_err}")
    return False


# -------------------------
# Rollback snapshot (best-effort)
# -------------------------

def _container_id_for_service(service: str) -> Optional[str]:
    try:
        out = run_out(compose_cmd("ps", "-q", service), cwd=PROJECT_DIR, timeout_sec=10.0).strip().splitlines()
        return out[0].strip() if out else None
    except Exception as e:
        log(f"WARN: cannot get container id for service={service}: {e}")
        return None


def _image_id_for_service(service: str) -> Optional[str]:
    try:
        out = run_out(compose_cmd("images", "-q", service), cwd=PROJECT_DIR, timeout_sec=10.0).strip().splitlines()
        return out[0].strip() if out else None
    except Exception as e:
        log(f"WARN: cannot get image id for service={service} (best-effort): {e}")
        return None


def save_rollback_snapshot() -> Dict[str, Any]:
    snap: Dict[str, Any] = {
        "ts": int(time.time()),
        "project": COMPOSE_PROJECT_NAME,
        "compose_file": str(Path(_abs_compose_path())),
        "compose_effective": STATE.get("compose_effective"),
        "compose_effective_persist": STATE.get("compose_effective_persist"),
        "services": {},
    }

    for svc in UPDATE_SERVICES:
        cid = _container_id_for_service(svc)
        img_id = _image_id_for_service(svc)
        snap["services"][svc] = {
            "container_id": cid,
            "image_id": img_id,
            "image_ref": None,
            "repo_digests": [],
            "repo_tags": [],
        }

    ROLLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROLLBACK_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ROLLBACK_PATH)

    STATE["rollback_path"] = str(ROLLBACK_PATH)
    STATE["rollback_saved_at"] = snap["ts"]

    log(f"rollback snapshot saved: {ROLLBACK_PATH}")
    return snap


# -------------------------
# net-heal (helper mode)
# -------------------------

def _looks_like_network_recreate_issue() -> bool:
    text = "\n".join(_LAST_RUN_TAIL[-120:])
    return ("needs to be recreated" in text) and ("Network" in text)


def _net_heal_helper():
    """
    Запускаем helper-контейнер в network=bridge.
    ВАЖНО: helper читает compose из /project/.updater/docker-compose.effective.yml (файл на хосте).
    """
    _ensure_host_paths()

    net_name = f"{COMPOSE_PROJECT_NAME}_default"
    helper_img = _detect_self_image()

    # гарантируем persist-файл
    ensure_effective_compose_file()

    compose_persist = str(EFFECTIVE_COMPOSE_PERSIST)

    # Команда helper'а
    cmd = (
        "set -e; "
        f"NET={net_name}; "
        "echo '[helper] stop services'; "
        f"docker-compose -p {COMPOSE_PROJECT_NAME} -f {compose_persist} stop {' '.join(UPDATE_SERVICES)} || true; "
        "echo '[helper] rm services'; "
        f"docker-compose -p {COMPOSE_PROJECT_NAME} -f {compose_persist} rm -f {' '.join(UPDATE_SERVICES)} || true; "
        "echo '[helper] disconnect updater from default network'; "
        f"docker network disconnect -f {net_name} updater || true; "
        f"docker network disconnect -f {net_name} {SELF_CONTAINER} || true; "
        "echo '[helper] rm network'; "
        f"docker network rm {net_name} || true; "
        "echo '[helper] up services'; "
        f"docker-compose -p {COMPOSE_PROJECT_NAME} -f {compose_persist} up -d --force-recreate --remove-orphans {' '.join(UPDATE_SERVICES)}; "
        "echo '[helper] done';"
    )

    log("FIX: net-heal via helper (bridge network), compose from /project/.updater (host-visible)")
    rc = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "bridge",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            # Монтируем ХОСТ проект/конфиг, чтобы helper видел compose_persist и config
            "-v",
            f"{_HOST_PROJECT_SOURCE}:/project",
            "-v",
            f"{_HOST_CONFIG_SOURCE}:/config",
            helper_img,
            "sh",
            "-lc",
            cmd,
        ],
        cwd="/",
    )
    if rc != 0:
        raise RuntimeError(f"net-heal helper failed rc={rc}")
    log("FIX: net-heal helper finished OK")


# -------------------------
# Update worker
# -------------------------

def do_update():
    if STATE["running"]:
        return

    STATE["running"] = True
    STATE["last_error"] = None
    STATE["last_action"] = "update"

    try:
        STATE["step"] = "preflight"
        detect_compose_kind()
        _preflight_compose_file()

        ensure_effective_compose_file()

        STATE["step"] = "snapshot"
        save_rollback_snapshot()

        STATE["step"] = "pull"
        rc1 = run(compose_cmd("pull", *UPDATE_SERVICES), cwd=PROJECT_DIR)
        if rc1 != 0:
            if FALLBACK_BUILD:
                log("pull failed; fallback_build=True -> trying build")
                STATE["step"] = "build"
                rc_b = run(compose_cmd("build", *UPDATE_SERVICES), cwd=PROJECT_DIR)
                if rc_b != 0:
                    raise RuntimeError(f"build failed rc={rc_b}")
            else:
                raise RuntimeError(f"pull failed rc={rc1}")

        STATE["step"] = "restart"
        up_args = ["up", "-d", "--force-recreate", "--remove-orphans", *UPDATE_SERVICES]
        rc2 = run(compose_cmd(*up_args), cwd=PROJECT_DIR)

        if rc2 != 0 and _looks_like_network_recreate_issue():
            STATE["step"] = "netheal"
            _net_heal_helper()
            ensure_effective_compose_file()
            log("FIX: retry up after helper net-heal")
            rc2 = run(compose_cmd(*up_args), cwd=PROJECT_DIR)

        if rc2 != 0:
            raise RuntimeError(f"up failed rc={rc2}")

        STATE["step"] = "health"
        if not wait_health(timeout_sec=HEALTH_TIMEOUT_SEC):
            raise RuntimeError("health timeout: gatebox did not become ready")

        STATE["last_result"] = "ok"
        log("UPDATE OK")

    except Exception as e:
        err = str(e)
        log(f"ERROR: {err}")
        STATE["last_result"] = "error"
        STATE["last_error"] = err

    finally:
        STATE["running"] = False
        STATE["step"] = None


# -------------------------
# HTTP API
# -------------------------

class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/start":
            if not STATE["running"]:
                threading.Thread(target=do_update, daemon=True).start()
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "not found"})

        def do_GET(self):
        # -------------------------------------------------
        # NEW: compatibility endpoints for gatebox UI
        # -------------------------------------------------

        # root: простая проверка "жив ли updater"
        if self.path == "/":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "updater",
                    "version": STATE.get("version"),
                    "self": STATE.get("self"),
                    "endpoints": ["/check", "/status", "/log", "/metrics", "/start", "/version"],
                },
            )
            return

        # /health: совместимость (многие ожидают health)
        if self.path == "/health":
            self._json(200, {"ok": True})
            return

        # /check: ВАЖНО — именно сюда ходит gatebox UI
        # gatebox ранее дергал http://updater:9010/check и падал на 404 → 502 в UI
        if self.path == "/check":
            self._json(
                200,
                {
                    "ok": True,
                    "running": STATE.get("running"),
                    "step": STATE.get("step"),
                    "last_result": STATE.get("last_result"),
                    "last_error": STATE.get("last_error"),
                    "last_check": STATE.get("last_check"),
                    "last_action": STATE.get("last_action"),
                },
            )
            return

        # /version: удобная диагностика на странице "Система"
        if self.path == "/version":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "updater",
                    "version": STATE.get("version"),
                    "self": STATE.get("self"),
                    "project_dir": PROJECT_DIR,
                    "compose_file": COMPOSE_FILE,
                    "config_dir": CONFIG_DIR,
                    "project_name": COMPOSE_PROJECT_NAME,
                    "services": UPDATE_SERVICES,
                    "health_url": HEALTH_URL,
                    "fallback_build": FALLBACK_BUILD,
                    "compose_effective": STATE.get("compose_effective"),
                    "compose_effective_persist": STATE.get("compose_effective_persist"),
                    "rollback_path": str(ROLLBACK_PATH),
                },
            )
            return

        # -------------------------------------------------
        # Existing endpoints (KEEP)
        # -------------------------------------------------

        if self.path == "/status":
            self._json(200, STATE)
            return

        if self.path == "/log":
            self._json(200, {"log": LOG[-500:]})
            return

        # NEW/FIX: метрики хоста и контейнеров для UI
        if self.path == "/metrics":
            self._json(200, build_metrics_payload())
            return

        self._json(404, {"error": "not found"})


log(
    "updater starting on :%s project=%s compose=%s project_name=%s fallback_build=%s health_url=%s services=%s rollback=%s self=%s"
    % (
        PORT,
        PROJECT_DIR,
        _abs_compose_path(),
        COMPOSE_PROJECT_NAME,
        FALLBACK_BUILD,
        HEALTH_URL,
        ",".join(UPDATE_SERVICES),
        str(ROLLBACK_PATH),
        SELF_CONTAINER,
    )
)

HTTPServer(("", PORT), Handler).serve_forever()