# =========================================================
# Файл: app/worker/policy.py
# Проект: LPR GateBox
# Версия: v0.3.7-all-in-one-split
# =========================================================

from __future__ import annotations

from typing import Dict, List


class PlateEventState:
    def __init__(self, plate_confirm_window_sec: float, plate_resend_sec: float, global_send_min_interval_sec: float, plate_confirm_k: int):
        self.plate_confirm_window_sec = float(plate_confirm_window_sec)
        self.plate_resend_sec = float(plate_resend_sec)
        self.global_send_min_interval_sec = float(global_send_min_interval_sec)
        self.plate_confirm_k = int(plate_confirm_k)

        self.last_sent_ts: float = 0.0
        self.last_sent_plate: str = ""
        self.per_plate_last_sent: Dict[str, float] = {}
        self.plate_hits: Dict[str, List[float]] = {}
        self.last_seen_plate: str = ""
        self.last_seen_ts: float = 0.0

    def _clean_hits(self, now: float) -> None:
        win = self.plate_confirm_window_sec
        for p in list(self.plate_hits.keys()):
            self.plate_hits[p] = [t for t in self.plate_hits[p] if now - t <= win]
            if not self.plate_hits[p]:
                del self.plate_hits[p]

    def note_plate(self, now: float, plate: str) -> int:
        self._clean_hits(now)
        self.plate_hits.setdefault(plate, []).append(now)
        return len(self.plate_hits[plate])

    def can_send_global(self, now: float) -> bool:
        return (now - self.last_sent_ts) >= max(0.0, self.global_send_min_interval_sec)

    def can_send_plate(self, now: float, plate: str) -> bool:
        if self.plate_resend_sec <= 0:
            return True
        last = self.per_plate_last_sent.get(plate, 0.0)
        return (now - last) >= self.plate_resend_sec

    def mark_sent(self, now: float, plate: str) -> None:
        self.last_sent_ts = now
        self.last_sent_plate = plate
        self.per_plate_last_sent[plate] = now

    def mark_seen(self, now: float, plate: str) -> None:
        self.last_seen_plate = plate
        self.last_seen_ts = now
