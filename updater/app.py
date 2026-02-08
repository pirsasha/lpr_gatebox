from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import subprocess
import threading
import time
import os
import zipfile
from pathlib import Path

# =========================================================
# CONFIG
# =========================================================

PORT = int(os.environ.get("UPDATER_PORT", "9010"))
PROJECT_DIR = os.environ.get("PROJECT_DIR", "/project")
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose.yml")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")

# 0 = только pull (продуктовый режим с registry)
# 1 = если pull не сработал -> build (dev/локалка)
PULL_FALLBACK_BUILD = os.environ.get("PULL_FALLBACK_BUILD", "1") == "1"

# =========================================================
# STATE
# =========================================================

STATE = {
    "running": False,
    "step": None,
    "last_result": None,
    "last_check": None,
}
LOG = []

# =========================================================
# METRICS CACHE
# =========================================================

METRICS_CACHE = {"ts": 0.0, "data": None}
METRICS_LOCK = threading.Lock()
METRICS_TTL_SEC = float(os.environ.get("METRICS_TTL_SEC", "2.0"))

# =========================================================
# HELPERS
# =========================================================

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.append(line)
    if len(LOG) > 500:
        LOG.pop(0)


def run(cmd):
    log(f"$ {' '.join(cmd)}")
    p = subprocess.Popen(
        cmd,
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert p.stdout is not None
    for line in p.stdout:
        log(line.rstrip())
    p.wait()
    return p.returncode


def run_compose(args):
    # docker-compose (v1) для совместимости
    return run(["docker-compose", "-f", COMPOSE_FILE, *args])


# =========================================================
# UPDATE LOGIC
# =========================================================

def do_update():
    STATE["running"] = True
    try:
        # 1) pull
        STATE["step"] = "pull"
        rc_pull = run_compose(["pull"])
        if rc_pull != 0:
            log(f"pull failed rc={rc_pull}")
            if PULL_FALLBACK_BUILD:
                # 2) fallback build
                STATE["step"] = "build"
                rc_build = run_compose(["build"])
                if rc_build != 0:
                    raise RuntimeError(f"build failed rc={rc_build}")
            else:
                raise RuntimeError(f"pull failed rc={rc_pull} (no fallback)")

        # 3) restart/up
        STATE["step"] = "restart"
        rc_up = run_compose(["up", "-d"])
        if rc_up != 0:
            raise RuntimeError(f"up failed rc={rc_up}")

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


# =========================================================
# METRICS HELPERS
# =========================================================

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
    return round((dt - didle) / dt * 100.0, 1)


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
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}};{{.CPUPerc}};{{.MemUsage}}",
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip().splitlines()

        items = []
        for line in out:
            parts = line.split(";")
            if len(parts) != 3:
                continue
            name, cpu, mem = parts
            mem_used = ""
            mem_limit = ""
            if " / " in mem:
                mem_used, mem_limit = [x.strip() for x in mem.split(" / ", 1)]
            items.append(
                {
                    "name": name.strip(),
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
        "mem_total_mb": (mem_total_kb // 1024) if mem_total_kb else None,
        "mem_used_mb": (mem_used_kb // 1024) if mem_used_kb else None,
        "mem_avail_mb": (mem_avail_kb // 1024) if mem_avail_kb else None,
        "disk_root": _disk_usage_mb("/"),
        "disk_project": _disk_usage_mb(PROJECT_DIR or "/"),
        "disk_config": _disk_usage_mb(CONFIG_DIR or "/"),
        "kernel": _read_first_line("/proc/version"),
    }

    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as f:
            host["load1"] = float(f.read().split()[0])
    except Exception:
        pass

    return {
        "ok": True,
        "host": host,
        "containers": _docker_stats(),
    }


# =========================================================
# HTTP HANDLER
# =========================================================

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
            self._json(200, {"log": LOG[-200:]})
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
                now = time.time()

                with METRICS_LOCK:
                    cached = METRICS_CACHE["data"]
                    ts = METRICS_CACHE["ts"]
                    if cached is not None and (now - ts) < METRICS_TTL_SEC:
                        self._json(200, cached)
                        return

                data = get_metrics()

                with METRICS_LOCK:
                    METRICS_CACHE["data"] = data
                    METRICS_CACHE["ts"] = now

                self._json(200, data)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        self._json(404, {"error": "not found"})


# =========================================================
# START
# =========================================================

log(f"updater starting on :{PORT} project={PROJECT_DIR} compose={COMPOSE_FILE} fallback_build={PULL_FALLBACK_BUILD}")
HTTPServer(("", PORT), Handler).serve_forever()