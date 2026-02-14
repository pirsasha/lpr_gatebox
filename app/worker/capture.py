# =========================================================
# Файл: app/worker/capture.py
# Проект: LPR GateBox
# Версия: v0.3.7-all-in-one-split
# =========================================================

from __future__ import annotations

import os
import time
import json
import subprocess
import threading
from typing import Dict, Optional, Tuple

import cv2
import numpy as np


def apply_opencv_ffmpeg_options(rtsp_transport: str, open_timeout_ms: int, read_timeout_ms: int) -> None:
    """Настраиваем OpenCV->FFmpeg опции для RTSP."""
    if os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS"):
        return
    stimeout_us = max(1, int(open_timeout_ms)) * 1000
    rwtimeout_us = max(1, int(read_timeout_ms)) * 1000
    transport = "tcp" if (rtsp_transport or "").lower() != "udp" else "udp"
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        f"rtsp_transport;{transport}|stimeout;{stimeout_us}|rw_timeout;{rwtimeout_us}"
        f"|fflags;nobuffer|flags;low_delay|max_delay;0|reorder_queue_size;0"
    )


def open_capture(rtsp_url: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap


class FrameGrabber(threading.Thread):
    def __init__(
        self,
        rtsp_url: str,
        read_fps: float,
        freeze_enable: bool,
        freeze_every_n: int,
        freeze_diff_mean_thr: float,
        freeze_max_sec: float,
        rtsp_drain_grabs: int,
        rtsp_transport: str,
        rtsp_open_timeout_ms: int,
        rtsp_read_timeout_ms: int,
    ):
        super().__init__(daemon=True)
        self.rtsp_url = rtsp_url
        self.read_interval = 1.0 / max(1.0, float(read_fps))

        self.freeze_enable = bool(freeze_enable)
        self.freeze_every_n = int(max(1, freeze_every_n))
        self.freeze_diff_mean_thr = float(freeze_diff_mean_thr)
        self.freeze_max_sec = float(freeze_max_sec)

        self.rtsp_drain_grabs = int(max(0, rtsp_drain_grabs))

        self.rtsp_transport = rtsp_transport
        self.rtsp_open_timeout_ms = int(rtsp_open_timeout_ms)
        self.rtsp_read_timeout_ms = int(rtsp_read_timeout_ms)

        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._last_frame: Optional[np.ndarray] = None
        self._last_ts: float = 0.0

        self._frames = 0
        self._t0 = time.time()
        self._reopens = 0

        self._prev_small: Optional[np.ndarray] = None
        self._freeze_since: float = 0.0
        self._tick = 0

        self.running = True

    def _open(self) -> None:
        apply_opencv_ffmpeg_options(self.rtsp_transport, self.rtsp_open_timeout_ms, self.rtsp_read_timeout_ms)
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = open_capture(self.rtsp_url)
        self._reopens += 1

    def stop(self) -> None:
        self.running = False

    def get(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            return self._last_frame, float(self._last_ts)

    def stats(self) -> Dict[str, float]:
        dt = max(1e-3, time.time() - self._t0)
        return {"read_fps_eff": float(self._frames) / dt, "reopens": float(self._reopens)}

    def _maybe_freeze_reopen(self, frame_bgr: np.ndarray, now: float) -> bool:
        if not self.freeze_enable:
            return False
        self._tick += 1
        if self.freeze_every_n > 1 and (self._tick % int(self.freeze_every_n) != 0):
            return False

        try:
            g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (160, 120), interpolation=cv2.INTER_AREA)
        except Exception:
            return False

        if self._prev_small is None or self._prev_small.shape != g.shape:
            self._prev_small = g
            self._freeze_since = 0.0
            return False

        dm = float(np.mean(cv2.absdiff(self._prev_small, g)))
        self._prev_small = g

        if dm <= self.freeze_diff_mean_thr:
            if self._freeze_since == 0.0:
                self._freeze_since = now
            elif (now - self._freeze_since) >= self.freeze_max_sec:
                print(f"[rtsp_worker] WARN: grabber freeze (diff_mean={dm:.3f}) for {now-self._freeze_since:.1f}s -> reopen")
                self._freeze_since = 0.0
                self._open()
                time.sleep(0.2)
                return True
        else:
            self._freeze_since = 0.0

        return False

    def run(self) -> None:
        self._open()

        while self.running:
            t0 = time.time()
            now = t0

            if self._cap is None or not self._cap.isOpened():
                self._open()
                time.sleep(0.2)
                continue

            try:
                ok = self._cap.grab()
            except Exception:
                ok = False

            if not ok:
                self._open()
                time.sleep(0.2)
                continue

            for _ in range(int(self.rtsp_drain_grabs)):
                try:
                    self._cap.grab()
                except Exception:
                    break

            ok, frame = self._cap.retrieve()
            if not ok or frame is None:
                self._open()
                time.sleep(0.2)
                continue

            self._maybe_freeze_reopen(frame, now)

            with self._lock:
                self._last_frame = frame
                self._last_ts = now

            self._frames += 1

            dt = time.time() - t0
            if dt < self.read_interval:
                time.sleep(self.read_interval - dt)


class FFmpegPipeGrabber(threading.Thread):
    def __init__(self, rtsp_url: str, transport: str, read_fps: float, probe: bool, threads: int, read_timeout_sec: float):
        super().__init__(daemon=True)
        self.rtsp_url = rtsp_url
        self.transport = "udp" if transport == "udp" else "tcp"
        self.read_interval = 1.0 / max(1.0, float(read_fps))

        self.probe = bool(probe)
        self.threads = int(threads)
        self.read_timeout_sec = float(read_timeout_sec)

        self._lock = threading.Lock()
        self._last_frame: Optional[np.ndarray] = None
        self._last_ts: float = 0.0

        self._frames = 0
        self._t0 = time.time()
        self._restarts = 0

        self._proc: Optional[subprocess.Popen] = None
        self._w: int = 0
        self._h: int = 0
        self.running = True

    def stop(self) -> None:
        self.running = False
        self._kill_proc()

    def get(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            return self._last_frame, float(self._last_ts)

    def stats(self) -> Dict[str, float]:
        dt = max(1e-3, time.time() - self._t0)
        return {"read_fps_eff": float(self._frames) / dt, "restarts": float(self._restarts)}

    def _kill_proc(self) -> None:
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.kill()
        except Exception:
            pass

    def _probe_size(self) -> Tuple[int, int]:
        if not self.probe:
            return (0, 0)
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-rtsp_transport", self.transport,
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                self.rtsp_url,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
            if r.returncode != 0:
                return (0, 0)
            data = json.loads(r.stdout or "{}")
            streams = data.get("streams") or []
            if not streams:
                return (0, 0)
            w = int(streams[0].get("width") or 0)
            h = int(streams[0].get("height") or 0)
            return (w, h)
        except Exception:
            return (0, 0)

    def _start_proc(self, w: int, h: int) -> None:
        self._kill_proc()
        self._restarts += 1
        self._w, self._h = int(w), int(h)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", self.transport,
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-an",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-i", self.rtsp_url,
            "-pix_fmt", "bgr24",
            "-f", "rawvideo",
            "pipe:1",
        ]
        if int(self.threads) > 0:
            cmd.insert(1, "-threads")
            cmd.insert(2, str(int(self.threads)))

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def _read_exact(self, n: int, timeout_sec: float) -> Optional[bytes]:
        p = self._proc
        if p is None or p.stdout is None:
            return None
        fd = p.stdout.fileno()
        buf = bytearray()
        deadline = time.time() + float(timeout_sec)

        while len(buf) < n:
            left = deadline - time.time()
            if left <= 0:
                return None
            try:
                import select
                r, _, _ = select.select([fd], [], [], min(0.2, left))
            except Exception:
                r = [fd]

            if not r:
                continue
            try:
                chunk = os.read(fd, n - len(buf))
            except Exception:
                return None
            if not chunk:
                return None
            buf.extend(chunk)

        return bytes(buf)

    def run(self) -> None:
        w, h = self._probe_size()
        if w <= 0 or h <= 0:
            self._w, self._h = 0, 0
        else:
            self._start_proc(w, h)

        while self.running:
            t0 = time.time()
            now = t0

            if self._proc is None or self._proc.stdout is None or self._w <= 0 or self._h <= 0:
                time.sleep(0.3)
                continue

            frame_bytes = int(self._w) * int(self._h) * 3
            b = self._read_exact(frame_bytes, self.read_timeout_sec)
            if b is None:
                self._start_proc(self._w, self._h)
                time.sleep(0.2)
                continue

            frame = np.frombuffer(b, dtype=np.uint8).reshape((int(self._h), int(self._w), 3))

            with self._lock:
                self._last_frame = frame
                self._last_ts = now

            self._frames += 1

            dt = time.time() - t0
            if dt < self.read_interval:
                time.sleep(self.read_interval - dt)


class AutoGrabber:
    def __init__(
        self,
        rtsp_url: str,
        read_fps: float,
        capture_backend: str,
        rtsp_transport: str,
        rtsp_open_timeout_ms: int,
        rtsp_read_timeout_ms: int,
        ffmpeg_probe: bool,
        ffmpeg_threads: int,
        ffmpeg_read_timeout_sec: float,
        auto_switch_check_sec: float,
        auto_switch_age_ms: int,
        auto_switch_streak: int,
        auto_switch_cooldown_sec: float,
        freeze_enable: bool,
        freeze_every_n: int,
        freeze_diff_mean_thr: float,
        freeze_max_sec: float,
        rtsp_drain_grabs: int,
    ):
        self.rtsp_url = rtsp_url
        self.read_fps = float(read_fps)

        self.capture_backend = capture_backend
        self.rtsp_transport = rtsp_transport
        self.rtsp_open_timeout_ms = int(rtsp_open_timeout_ms)
        self.rtsp_read_timeout_ms = int(rtsp_read_timeout_ms)

        self.ffmpeg_probe = bool(ffmpeg_probe)
        self.ffmpeg_threads = int(ffmpeg_threads)
        self.ffmpeg_read_timeout_sec = float(ffmpeg_read_timeout_sec)

        self.auto_switch_check_sec = float(auto_switch_check_sec)
        self.auto_switch_age_ms = int(auto_switch_age_ms)
        self.auto_switch_streak = int(auto_switch_streak)
        self.auto_switch_cooldown_sec = float(auto_switch_cooldown_sec)

        self.freeze_enable = bool(freeze_enable)
        self.freeze_every_n = int(max(1, freeze_every_n))
        self.freeze_diff_mean_thr = float(freeze_diff_mean_thr)
        self.freeze_max_sec = float(freeze_max_sec)
        self.rtsp_drain_grabs = int(max(0, rtsp_drain_grabs))

        self._backend = "opencv"
        self._grabber = None  # type: ignore
        self._lock = threading.Lock()

        self._bad_streak = 0
        self._last_switch = 0.0

        self._mon = threading.Thread(target=self._monitor_loop, daemon=True)

    def start(self) -> None:
        backend = (self.capture_backend or "opencv").strip().lower()
        if backend not in ("auto", "opencv", "ffmpeg"):
            backend = "auto"

        if backend == "ffmpeg":
            if self._start_ffmpeg():
                self._backend = "ffmpeg"
            else:
                self._start_opencv()
        else:
            self._start_opencv()

        self._mon.start()

    def stop(self) -> None:
        with self._lock:
            g = self._grabber
        try:
            if g is not None:
                g.stop()
        except Exception:
            pass

    def get(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            g = self._grabber
        if g is None:
            return None, 0.0
        return g.get()

    def stats(self) -> Dict[str, float]:
        with self._lock:
            g = self._grabber
        return g.stats() if g is not None else {}

    def backend_name(self) -> str:
        with self._lock:
            return str(self._backend)

    def _start_opencv(self) -> None:
        g = FrameGrabber(
            self.rtsp_url,
            self.read_fps,
            freeze_enable=self.freeze_enable,
            freeze_every_n=self.freeze_every_n,
            freeze_diff_mean_thr=self.freeze_diff_mean_thr,
            freeze_max_sec=self.freeze_max_sec,
            rtsp_drain_grabs=self.rtsp_drain_grabs,
            rtsp_transport=self.rtsp_transport,
            rtsp_open_timeout_ms=self.rtsp_open_timeout_ms,
            rtsp_read_timeout_ms=self.rtsp_read_timeout_ms,
        )
        g.start()
        with self._lock:
            old = self._grabber
            self._grabber = g
            self._backend = "opencv"
        try:
            if old is not None:
                old.stop()
        except Exception:
            pass

    def _start_ffmpeg(self) -> bool:
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=1.5)
        except Exception:
            return False

        g = FFmpegPipeGrabber(
            self.rtsp_url,
            self.rtsp_transport,
            self.read_fps,
            probe=self.ffmpeg_probe,
            threads=self.ffmpeg_threads,
            read_timeout_sec=self.ffmpeg_read_timeout_sec,
        )
        g.start()

        t0 = time.time()
        ok = False
        while time.time() - t0 < 2.0:
            fr, ts = g.get()
            if fr is not None and ts > 0:
                ok = True
                break
            time.sleep(0.05)

        if not ok:
            try:
                g.stop()
            except Exception:
                pass
            return False

        with self._lock:
            old = self._grabber
            self._grabber = g
            self._backend = "ffmpeg"
        try:
            if old is not None:
                old.stop()
        except Exception:
            pass
        return True

    def _monitor_loop(self) -> None:
        while True:
            time.sleep(max(0.2, float(self.auto_switch_check_sec)))

            if self.capture_backend != "auto":
                continue

            fr, ts = self.get()
            if ts <= 0:
                continue
            age_ms = (time.time() - float(ts)) * 1000.0

            backend = self.backend_name()

            if backend == "opencv":
                if age_ms >= float(self.auto_switch_age_ms):
                    self._bad_streak += 1
                else:
                    self._bad_streak = 0

                if self._bad_streak >= int(self.auto_switch_streak):
                    now = time.time()
                    if now - self._last_switch > float(self.auto_switch_cooldown_sec):
                        if self._start_ffmpeg():
                            print(f"[rtsp_worker] CHG: capture backend -> ffmpeg (age_ms={age_ms:.1f})")
                        self._last_switch = now
                        self._bad_streak = 0
            else:
                if age_ms >= float(self.auto_switch_age_ms) * 2.5:
                    now = time.time()
                    if now - self._last_switch > float(self.auto_switch_cooldown_sec):
                        self._start_opencv()
                        self._last_switch = now
                        print(f"[rtsp_worker] CHG: capture backend -> opencv (ffmpeg degraded age_ms={age_ms:.1f})")
