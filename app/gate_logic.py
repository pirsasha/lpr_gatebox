# =========================================================
# Файл: app/gate_logic.py
# Проект: LPR GateBox
# Версия: v0.3.2
# Изменено: 2026-02-09  (UTC+3)
# Автор: Александр
# ---------------------------------------------------------
# Что сделано:
# - FIX: normalize_ru_plate() стал best-effort:
#        * парсит номер "сквозь мусор" (берём символы по порядку)
#        * поддерживает кейс, когда OCR съедает удвоенную букву (НН -> Н)
#        * НЕ угадывает отсутствующую первую букву (это невозможно без whitelist)
# - NEW: опциональный fuzzy_whitelist_enabled (по умолчанию False):
#        * если включить — при нестрогом OCR пробуем найти ближайший номер из whitelist
#          (малый edit-distance, ранний выход), и тогда можно "восстановить" У616НН761
# =========================================================

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
        # FIX: по умолчанию допускаем и 2, и 3 цифры (161 и 761 оба валидны)
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
# Нормализация (best-effort)
# ----------------------------
def _cleanup_and_map(pred: str) -> str:
    """Базовая чистка: upper + LAT->CYR + фильтр RU_ALLOWED."""
    s = (pred or "").strip().upper()
    s = "".join(LAT2CYR.get(ch, ch) for ch in s)
    s = "".join(ch for ch in s if ch in RU_ALLOWED)
    return s


def _take_letter(ch: str) -> Optional[str]:
    """Берём символ как букву РФ номера (с минимальной починкой цифр->букв)."""
    if ch in RU_LETTERS:
        return ch
    if ch in DIGIT2LETTER:
        return DIGIT2LETTER[ch]
    return None


def _take_digit(ch: str) -> Optional[str]:
    """Берём символ как цифру (с минимальной починкой букв->цифр)."""
    if ch in RU_DIGITS:
        return ch
    d = LETTER2DIGIT.get(ch)
    if d in RU_DIGITS:
        return d
    return None


def _build_strict_from_stream(prefix: str, region: str) -> str:
    """
    Пробуем собрать L DDD LL + region из prefix, проходя по символам слева направо.

    ВАЖНО:
    - мы НЕ можем восстановить отсутствующую первую букву, если её вообще нет в prefix.
      (пример: "616Н761" -> не восстановить "У" без whitelist/fuzzy)
    - но можем чинить частый кейс: OCR "съел" вторую букву (НН -> Н).
    """
    if not prefix or not region:
        return ""

    # 1) L (первую букву берём как "первую встреченную букву" в prefix)
    L1: Optional[str] = None
    i = 0
    while i < len(prefix) and L1 is None:
        L1 = _take_letter(prefix[i])
        i += 1
    if L1 is None:
        return ""

    # 2) DDD (дальше набираем 3 цифры)
    digits: List[str] = []
    while i < len(prefix) and len(digits) < 3:
        d = _take_digit(prefix[i])
        if d is not None:
            digits.append(d)
        i += 1
    if len(digits) != 3:
        return ""

    # 3) LL (дальше набираем 2 буквы)
    letters: List[str] = []
    while i < len(prefix) and len(letters) < 2:
        L = _take_letter(prefix[i])
        if L is not None:
            letters.append(L)
        i += 1

    # FIX: если нашли только 1 букву — часто это "НН" -> "Н" (OCR слип)
    if len(letters) == 1:
        letters.append(letters[0])

    if len(letters) != 2:
        return ""

    plate = f"{L1}{digits[0]}{digits[1]}{digits[2]}{letters[0]}{letters[1]}{region}"
    return plate


def normalize_ru_plate(pred_latin: str) -> str:
    """
    Best-effort нормализация под РФ номер.

    Цели:
    - получить максимально "строгий" формат LDDDLLRR (RR = 2-3 цифры)
    - максимально терпимо к OCR-артефактам (мусор/пропуски)
    - НЕ угадываем отсутствующую первую букву (невозможно без внешних данных)

    Алгоритм:
    1) чистим строку (LAT->CYR, фильтр RU_ALLOWED)
    2) выделяем регион (последние 2-3 цифры)
    3) пытаемся собрать строгий номер из prefix (сквозной парсер)
    4) если не вышло — возвращаем "как есть после чистки"
    """
    s = _cleanup_and_map(pred_latin)
    if not s:
        return ""

    # регион: последние 2-3 цифры
    m = re.search(r"(\d{2,3})$", s)
    if not m:
        return s

    region = m.group(1)
    prefix = s[: -len(region)]
    if not prefix:
        return s

    built = _build_strict_from_stream(prefix, region)
    return built if built else s


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
    s = re.sub(r"[\s\-_:;,.]+", "", s)
    return s


def is_noise_ocr(raw: str) -> bool:
    """Эвристика: считаем результат OCR "мусором" и скрываем его из UI по умолчанию."""
    s = cleanup_ocr_raw(raw)
    if not s:
        return True
    if len(s) <= 4:
        return True
    if s.isdigit() and len(s) <= 5:
        return True
    if s.isdigit() and len(s) == 4 and s[0] in ("8", "9"):
        return True
    if len(s) >= 4 and len(set(s)) == 1:
        return True
    return False


def _whitelist_preclean(s: str) -> str:
    """
    whitelist может содержать пробелы/дефисы/подчёркивания и т.п.
    Пример: "У 616 НН 761" -> "У616НН761"
    """
    if s is None:
        return ""
    s2 = str(s).strip().upper()
    s2 = re.sub(r"[\s\-_]+", "", s2)
    return s2


# ----------------------------
# Fuzzy match по whitelist (ОПЦИОНАЛЬНО)
# ----------------------------
def _levenshtein_limited(a: str, b: str, max_dist: int) -> int:
    """
    Левенштейн с ранним выходом (если точно > max_dist).
    Нужен только для маленького max_dist (1-3).
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1

    # dp по строкам
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        min_row = cur[0]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
            if cur[-1] < min_row:
                min_row = cur[-1]
        if min_row > max_dist:
            return max_dist + 1
        prev = cur
    return prev[-1]


def _fuzzy_pick_from_whitelist(plate_norm: str, whitelist: set[str], max_dist: int = 2) -> Tuple[str, int]:
    """
    Возвращает (best_plate, dist). Если не нашли — ("", max_dist+1)
    """
    if not plate_norm or not whitelist:
        return ("", max_dist + 1)

    best = ""
    best_d = max_dist + 1
    for w in whitelist:
        d = _levenshtein_limited(plate_norm, w, max_dist=max_dist)
        if d < best_d:
            best_d = d
            best = w
            if best_d == 0:
                break
    return (best, best_d)


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

    # NEW: fuzzy whitelist match (по умолчанию выключено — безопасный режим)
    # Если включить, то OCR с пропусками может быть "восстановлен" до whitelist-номера.
    fuzzy_whitelist_enabled: bool = False
    fuzzy_max_dist: int = 2

    _hits: Dict[str, List[float]] = field(default_factory=dict)
    _last_open_ts: float = 0.0

    def __post_init__(self) -> None:
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

        # 1) строгая валидация
        valid = is_valid_ru_plate_strict(plate_norm, region_check=self.region_check)

        # 2) NEW: (опционально) fuzzy восстановление до whitelist-номера
        #    Это решает кейсы типа: "У616Н761" -> "У616НН761" (OCR съел одну Н).
        #    Но если OCR вообще потерял первую букву ("616Н761"), то fuzzy может помочь
        #    только если whitelist маленький и номер уникален — поэтому выключено по умолчанию.
        fuzzy_used = False
        fuzzy_dist = None
        if (not valid) and self.fuzzy_whitelist_enabled and self.whitelist:
            best, dist = _fuzzy_pick_from_whitelist(
                plate_norm, self.whitelist, max_dist=int(self.fuzzy_max_dist)
            )
            if best and dist <= int(self.fuzzy_max_dist):
                plate_norm = best
                valid = True
                fuzzy_used = True
                fuzzy_dist = dist

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
                "fuzzy_used": fuzzy_used,
                "fuzzy_dist": fuzzy_dist,
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
                "fuzzy_used": fuzzy_used,
                "fuzzy_dist": fuzzy_dist,
            }

        allowed = self._in_whitelist(plate_norm)
        if not allowed:
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
                "fuzzy_used": fuzzy_used,
                "fuzzy_dist": fuzzy_dist,
            }

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
                "fuzzy_used": fuzzy_used,
                "fuzzy_dist": fuzzy_dist,
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
                "fuzzy_used": fuzzy_used,
                "fuzzy_dist": fuzzy_dist,
            }

        self._last_open_ts = now
        return {
            "plate": plate_norm,
            "valid": True,
            "allowed": True,
            "ok": True,
            "reason": "ok" if not fuzzy_used else "ok_fuzzy_whitelist",
            "stabilized": True,
            "stab_reason": "confirmed",
            "hits": hits,
            "hits_window_sec": hits_window_sec,
            "fuzzy_used": fuzzy_used,
            "fuzzy_dist": fuzzy_dist,
        }