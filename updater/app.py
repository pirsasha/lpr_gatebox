# =========================================================
# Файл: updater/app.py
# Проект: LPR GateBox
# Версия: v0.3.2
# Изменено: 2026-02-08
# Что сделано:
# - FIX: единый compose project name, чтобы не было конфликта портов (8080)
# - CHG: docker-compose -> docker compose (Compose v2)
# - NEW: COMPOSE_PROJECT_NAME / UPDATE_FALLBACK_BUILD
# - KEEP: /status /log /report /metrics
# =========================================================

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import subprocess
import threading
import time
import os
import zipfile
from pathlib import Path

PORT = int(os.environ.get("UPDATER_PORT", "9010"))

STATE = {
    "running": False,
    "step": None,
    "last_result": None,
    "last_check": None,
}
LOG = []

PROJECT_DIR = os.environ.get("PROJECT_DIR", "/project")
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose.yml")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")

# IMPORTANT: чтобы updater управлял ТЕМ ЖЕ стеком, что и пользователь
COMPOSE_PROJECT_NAME = os.environ.get("COMPOSE_PROJECT_NAME", "lpr_gatebox").strip() or "lpr_gatebox"

# 0 = не билдим в проде (рекомендовано)
FALLBACK_BUILD = os.environ.get("UPDATE_FALLBACK_BUILD", "0") == "1"


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.append(line)
    if len(LOG) > 700:
        LOG.pop(0)


def run(cmd, cwd=PROJECT_DIR):
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
        log(line.rstrip())
    p.wait()
    return p.returncode


def compose_cmd(*args: str):
    """
    Always use Compose v2: `docker compose`
    And force same project name via -p
    """
    return ["docker", "compose", "-p", COMPOSE_PROJECT_NAME, "-f", COMPOSE_FILE, *args]


def do_update():
    if STATE["running"]:
        return

    STATE["running"] = True
    try:
        STATE["step"] = "pull"
        rc1 = run(compose_cmd("pull"))
        if rc1 != 0:
            if FALLBACK_BUILD:
                log("pull failed; fallback_build=True -> trying build")
                STATE["step"] = "build"
                rc_b = run(compose_cmd("build"))
                if rc_b != 0:
                    raise RuntimeError(f"build failed rc={rc_b}")
            else:
                raise RuntimeError(f"pull failed rc={rc1}")

        STATE["step"] = "restart"
        rc2 = run(compose_cmd("up", "-d"))
        if rc2 != 0:
            raise RuntimeError(f"up failed rc={rc2}")

        STATE["last_result"] = "ok"
    except Exception as e:
        log(f"ERROR: {e}")
        STATE["last_result"] = "error"
    finally:
        STATE["running"] = False
        STATE["step"] = None


def make_report():
    report_path = Path("/tmp/report.zip")
    with zipfile.ZipFile(report_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["settings.json", "whitelist.json"]:
            p = Path(CONFIG_DIR) / name
            if p.exists():
                z.write(p, arcname=name)

        z.writestr("update.log", "\n".join(LOG))

    return report_path


# -------------------------
# Metrics helpers
# -------------------------

def _read_first_line(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readline().strip()
    except Exception:
        return ""


def _read_meminfo_kb():
    mem_total = None
    mem_avail = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1])
    except Exception:
        pass
    return mem_total, mem_avail


def _read_cpu_percent(interval_sec: float = 0.15) -> float:
    def snap():
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                parts = f.readline().split()
            if not parts or parts[0] != "cpu":
                return None
            nums = list(map(int, parts[1:]))
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)
            return total, idle
        except Exception:
            return None

    a = snap()
    if not a:
        return 0.0
    time.sleep(interval_sec)
    b = snap()
    if not b:
        return 0.0

    total_a, idle_a = a
    total_b, idle_b = b
    dt = total_b - total_a
    didle = idle_b - idle_a
    if dt <= 0:
        return 0.0
    usage = (dt - didle) / dt * 100.0
    return round(usage, 1)


def _disk_usage_mb(path: str = "/"):
    try:
        st = os.statvfs(path)
        total = (st.f_frsize * st.f_blocks) // (1024 * 1024)
        free = (st.f_frsize * st.f_bavail) // (1024 * 1024)
        used = total - free
        return {"path": path, "total_mb": total, "used_mb": used, "free_mb": free}
    except Exception:
        return {"path": path, "total_mb": None, "used_mb": None, "free_mb": None}


def _docker_stats():
    try:
        cmd = [
            "docker", "stats", "--no-stream",
            "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}}",
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip().splitlines()
        items = []
        for line in out:
            parts = line.split(";")
            if len(parts) != 3:
                continue
            name, cpu, mem = parts[0].strip(), parts[1].strip(), parts[2].strip()
            mem_used = ""
            mem_limit = ""
            if " / " in mem:
                mem_used, mem_limit = [x.strip() for x in mem.split(" / ", 1)]
            items.append(
                {
                    "name": name,
                    "cpu_pct": cpu.replace("%", "").strip(),
                    "mem_used": mem_used or mem,
                    "mem_limit": mem_limit,
                    "raw_mem": mem,
                }
            )
        return items
    except Exception as e:
        return {"error": str(e)}


def get_metrics():
    mem_total_kb, mem_avail_kb = _read_meminfo_kb()
    mem_used_kb = None
    if mem_total_kb is not None and mem_avail_kb is not None:
        mem_used_kb = mem_total_kb - mem_avail_kb

    host = {
        "ts": int(time.time()),
        "load1": None,
        "cpu_pct": _read_cpu_percent(),
        "mem_total_mb": (mem_total_kb // 1024) if mem_total_kb is not None else None,
        "mem_used_mb": (mem_used_kb // 1024) if mem_used_kb is not None else None,
        "mem_avail_mb": (mem_avail_kb // 1024) if mem_avail_kb is not None else None,
        "disk_root": _disk_usage_mb("/"),
        "disk_project": _disk_usage_mb(PROJECT_DIR if PROJECT_DIR else "/"),
        "disk_config": _disk_usage_mb(CONFIG_DIR if CONFIG_DIR else "/"),
        "kernel": _read_first_line("/proc/version"),
    }

    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as f:
            host["load1"] = float(f.read().split()[0])
    except Exception:
        host["load1"] = None

    return {"ok": True, "host": host, "containers": _docker_stats()}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/check":
            STATE["last_check"] = time.time()
            # пока без “реального” сравнения версий — сделаем на следующем шаге
            self._json(200, {"ok": True, "updates": "unknown"})
            return

        if self.path == "/start":
            if not STATE["running"]:
                threading.Thread(target=do_update, daemon=True).start()
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/status":
            self._json(200, STATE)
            return

        if self.path == "/log":
            self._json(200, {"log": LOG[-300:]})
            return

        if self.path == "/report":
            report = make_report()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", "attachment; filename=gatebox_report.zip")
            self.end_headers()
            with open(report, "rb") as f:
                self.wfile.write(f.read())
            return

        if self.path == "/metrics":
            try:
                self._json(200, get_metrics())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        self._json(404, {"error": "not found"})


log(f"updater starting on :{PORT} project={PROJECT_DIR} compose={COMPOSE_FILE} project_name={COMPOSE_PROJECT_NAME} fallback_build={FALLBACK_BUILD}")
HTTPServer(("", PORT), Handler).serve_forever()