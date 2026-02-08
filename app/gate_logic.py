# =========================================================
# Файл: app/gate_logic.py
# Проект: LPR GateBox
# Версия: v0.3.1
# Изменено: 2026-02-06 20:30 (UTC+3)
# Автор: Александр
# ---------------------------------------------------------
# Что сделано:
# - NEW: cleanup_ocr_raw() и is_noise_ocr() — эвристики для отсечения "мусора" OCR (8980/9994/короткие обрывки)
# - NOTE: сама gate-логика (decide) остаётся детерминированной и работает по plate_norm
# =========================================================

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

# ----------------------------
# Алфавиты / маппинги
# ----------------------------
RU_LETTERS = "АВЕКМНОРСТУХ"
RU_DIGITS = "0123456789"
RU_ALLOWED = set(RU_LETTERS + RU_DIGITS)

# Частый маппинг латиницы->кириллицы
LAT2CYR = {
    "A": "А",
    "B": "В",
    "C": "С",
    "E": "Е",
    "H": "Н",
    "K": "К",
    "M": "М",
    "O": "О",
    "P": "Р",
    "T": "Т",
    "X": "Х",
    "Y": "У",
}

# Починка в "цифровых позициях"
LETTER2DIGIT = {
    "О": "0",
    "O": "0",
    "В": "8",  # иногда OCR видит 8 как В и наоборот
    "B": "8",
    "Т": "7",
    "T": "7",
}

# Починка в "буквенных позициях"
DIGIT2LETTER = {
    "0": "О",
    "8": "В",
}

# ----------------------------
# Валидация РФ номера (строго)
# ----------------------------
RU_PLATE_RE_STRICT = re.compile(rf"^[{RU_LETTERS}]\d{{3}}[{RU_LETTERS}]{{2}}\d{{2,3}}$")

# Если список регионов пуст — считаем любые 2-3 цифры допустимыми
KNOWN_REGIONS: set[str] = set()


def extract_region(plate: str) -> str:
    m = re.search(r"(\d{2,3})$", plate)
    return m.group(1) if m else ""


def is_valid_region(region: str) -> bool:
    if not region:
        return False
    if not KNOWN_REGIONS:
        return bool(re.fullmatch(r"\d{2,3}", region))
    return region in KNOWN_REGIONS


def is_valid_ru_plate_strict(plate: str, region_check: bool = True) -> bool:
    if not plate:
        return False
    if not RU_PLATE_RE_STRICT.match(plate):
        return False
    if region_check:
        region = extract_region(plate)
        return is_valid_region(region)
    return True


# ----------------------------
# Нормализация
# ----------------------------
def _cleanup_and_map(pred: str) -> str:
    """Базовая чистка: upper + LAT->CYR + фильтр RU_ALLOWED."""
    s = (pred or "").strip().upper()
    s = "".join(LAT2CYR.get(ch, ch) for ch in s)
    s = "".join(ch for ch in s if ch in RU_ALLOWED)
    return s


def normalize_ru_plate(pred_latin: str) -> str:
    """
    Умная нормализация под РФ номер:

    1) чистим строку (LAT->CYR, выкидываем мусор)
    2) пробуем выделить регион (последние 2-3 цифры)
    3) из префикса собираем [L][DDD][LL] с починкой O/0:
       - на позиции цифр: О->0 (и ещё немного безопасных замен)
       - на позиции букв: 0->О, 8->В (минимально)
       - если цифр меньше 3 — добиваем слева нулями

    Если собрать не получилось — возвращаем "как было после чистки".
    """
    s = _cleanup_and_map(pred_latin)
    if not s:
        return ""

    # 1) регион (2-3 цифры в конце)
    m = re.search(r"(\d{2,3})$", s)
    region = m.group(1) if m else ""
    prefix = s[: -len(region)] if region else s

    if not region:
        return s

    if len(prefix) < 3:
        return s

    def take_letter(ch: str) -> str:
        if ch in RU_LETTERS:
            return ch
        if ch in DIGIT2LETTER:
            return DIGIT2LETTER[ch]
        return ch

    def take_digit(ch: str) -> str:
        if ch in RU_DIGITS:
            return ch
        return LETTER2DIGIT.get(ch, ch)

    # L
    L1 = take_letter(prefix[0])

    rest = prefix[1:]
    digits: List[str] = []
    letters: List[str] = []

    # DDD
    i = 0
    while i < len(rest) and len(digits) < 3:
        d = take_digit(rest[i])
        if d in RU_DIGITS:
            digits.append(d)
        i += 1

    if len(digits) < 3:
        digits = (["0"] * (3 - len(digits))) + digits

    # LL
    while i < len(rest) and len(letters) < 2:
        L = take_letter(rest[i])
        if L in RU_LETTERS:
            letters.append(L)
        else:
            if rest[i] in DIGIT2LETTER:
                letters.append(DIGIT2LETTER[rest[i]])
        i += 1

    if len(letters) < 2:
        tail = prefix[1:]
        for ch in tail:
            if len(letters) >= 2:
                break
            L = take_letter(ch)
            if L in RU_LETTERS and L not in letters:
                letters.append(L)

    if L1 not in RU_LETTERS or len(digits) != 3 or len(letters) != 2:
        return s

    normalized = f"{L1}{''.join(digits)}{''.join(letters)}{region}"
    return normalized


# ----------------------------
# Фильтрация OCR "мусора" (продуктовый режим)
# ----------------------------
def cleanup_ocr_raw(raw: str) -> str:
    """Чистим сырой OCR перед эвристиками.

    Важно:
    - Эта функция НЕ заменяет normalize_ru_plate().
    - Здесь только "гигиена" строки, чтобы стабильно ловить мусор.
    """
    if raw is None:
        return ""
    s = str(raw).strip().upper()
    # убираем разделители, которые часто прилетают из OCR/пайплайна
    s = re.sub(r"[\s\-_:;,.]+", "", s)
    return s


def is_noise_ocr(raw: str) -> bool:
    """Эвристика: считаем результат OCR "мусором" и скрываем его из UI по умолчанию.

    Критерии (best-effort):
    - слишком коротко (<=4) → почти всегда не номер
    - только цифры и длина <=5 → типичный мусор (8980/9994/123)
    - повторяющиеся цифры (9994/0000) → мусор
    """
    s = cleanup_ocr_raw(raw)
    if not s:
        return True
    if len(s) <= 4:
        return True
    if s.isdigit() and len(s) <= 5:
        return True
    # частые "псевдо-номера" из 4 цифр, особенно с 8/9
    if s.isdigit() and len(s) == 4 and s[0] in ("8", "9"):
        return True
    # повторяющиеся символы (0000, 8888)
    if len(s) >= 4 and len(set(s)) == 1:
        return True
    return False


def _whitelist_preclean(s: str) -> str:
    """
    CHG v0.3.0: whitelist может содержать пробелы/дефисы/подчёркивания и т.п.
    Пример: "У 616 НН 761" -> "У616НН761"
    """
    if s is None:
        return ""
    s2 = str(s).strip().upper()
    s2 = re.sub(r"[\s\-_]+", "", s2)
    return s2


# ----------------------------
# GateDecider
# ----------------------------
@dataclass
class GateDecider:
    min_conf: float = 0.80
    confirm_n: int = 2
    window_sec: float = 2.0
    cooldown_sec: float = 15.0

    whitelist_path: str = "/config/whitelist.json"
    whitelist: set[str] = field(default_factory=set)

    region_check: bool = True
    region_stab: bool = True
    region_stab_window_sec: float = 2.5
    region_stab_min_hits: int = 3
    region_stab_min_ratio: float = 0.60

    _hits: Dict[str, List[float]] = field(default_factory=dict)
    _last_open_ts: float = 0.0

    def __post_init__(self) -> None:
        """
        FIX v0.3.0:
        Загружаем whitelist сразу при создании decider.

        Иначе при старте мог быть сценарий:
        - decider создан
        - apply_settings() не трогал whitelist_path
        - reload_whitelist() не вызвали
        => whitelist пустой => not_in_whitelist даже для "разрешенных".
        """
        self.reload_whitelist()

    def reload_whitelist(self) -> None:
        try:
            if os.path.exists(self.whitelist_path):
                with open(self.whitelist_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                items = data if isinstance(data, list) else data.get("plates", [])

                wl: set[str] = set()
                for x in items:
                    raw = _whitelist_preclean(x)
                    if not raw:
                        continue
                    wl.add(normalize_ru_plate(raw))

                self.whitelist = wl
            else:
                self.whitelist = set()
        except Exception:
            self.whitelist = set()

    def _in_whitelist(self, plate_norm: str) -> bool:
        if not self.whitelist:
            return False
        return plate_norm in self.whitelist

    def _cooldown_ok(self, now: float) -> bool:
        return (now - self._last_open_ts) >= float(self.cooldown_sec)

    def _push_hit(self, plate_norm: str, ts: float) -> None:
        arr = self._hits.get(plate_norm, [])
        arr.append(ts)

        # чистим старые по окну
        w = float(self.window_sec)
        arr = [t for t in arr if ts - t <= w]
        self._hits[plate_norm] = arr

    def decide(self, plate_norm: str, conf: float) -> Dict[str, Any]:
        """
        ВАЖНО: ожидаем уже НОРМАЛИЗОВАННУЮ строку plate_norm.
        raw держим отдельно в payload (в main.py).
        """
        now = time.time()
        hits_window_sec = float(self.window_sec)

        if not plate_norm:
            return {
                "plate": "",
                "valid": False,
                "allowed": False,
                "ok": False,
                "reason": "empty",
                "stabilized": False,
                "stab_reason": "empty",
                "hits": 0,
                "hits_window_sec": hits_window_sec,
            }

        valid = is_valid_ru_plate_strict(plate_norm, region_check=self.region_check)
        if not valid:
            return {
                "plate": plate_norm,
                "valid": False,
                "allowed": False,
                "ok": False,
                "reason": "invalid_format_or_region",
                "stabilized": False,
                "stab_reason": "no_loose_match",
                "hits": 0,
                "hits_window_sec": hits_window_sec,
            }

        if float(conf) < float(self.min_conf):
            return {
                "plate": plate_norm,
                "valid": True,
                "allowed": False,
                "ok": False,
                "reason": "low_conf",
                "stabilized": False,
                "stab_reason": "no_loose_match",
                "hits": 0,
                "hits_window_sec": hits_window_sec,
            }

        allowed = self._in_whitelist(plate_norm)
        if not allowed:
            # копим подтверждения даже если не в whitelist (для UI/статистики)
            self._push_hit(plate_norm, now)
            hits = len(self._hits.get(plate_norm, []))
            stabilized = hits >= int(self.confirm_n)
            return {
                "plate": plate_norm,
                "valid": True,
                "allowed": False,
                "ok": False,
                "reason": "not_in_whitelist",
                "stabilized": stabilized,
                "stab_reason": "not_enough_hits" if not stabilized else "confirmed_but_not_allowed",
                "hits": hits,
                "hits_window_sec": hits_window_sec,
            }

        # confirm/hits
        self._push_hit(plate_norm, now)
        hits = len(self._hits.get(plate_norm, []))
        if hits < int(self.confirm_n):
            return {
                "plate": plate_norm,
                "valid": True,
                "allowed": True,
                "ok": False,
                "reason": "not_enough_hits",
                "stabilized": False,
                "stab_reason": "not_enough_hits",
                "hits": hits,
                "hits_window_sec": hits_window_sec,
            }

        if not self._cooldown_ok(now):
            return {
                "plate": plate_norm,
                "valid": True,
                "allowed": True,
                "ok": False,
                "reason": "cooldown",
                "stabilized": True,
                "stab_reason": "cooldown",
                "hits": hits,
                "hits_window_sec": hits_window_sec,
            }

        self._last_open_ts = now
        return {
            "plate": plate_norm,
            "valid": True,
            "allowed": True,
            "ok": True,
            "reason": "ok",
            "stabilized": True,
            "stab_reason": "confirmed",
            "hits": hits,
            "hits_window_sec": hits_window_sec,
        }