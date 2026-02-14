# =========================================================
# Файл: app/core/plate_auto.py
# Проект: LPR GateBox
# Версия: v0.3.7-auto-preproc
# Изменено: 2026-02-11 (UTC+3)
# Автор: Александр + ChatGPT
#
# Что сделано:
# - NEW: авто-режим day/night/glare/blur по метрикам кадра/кропа
# - NEW: гистерезис, чтобы режим не прыгал
# - NEW: AutoDecision (preproc/profile, rectify_on, pad_used, upscale_min, drop)
# =========================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import cv2
import numpy as np


@dataclass
class AutoMetrics:
    luma_mean: float
    luma_p10: float
    luma_p90: float
    blur_var: float
    sat_ratio: float
    dark_ratio: float


@dataclass
class AutoDecision:
    mode: str  # day/night/glare/blur
    profile: str  # day_v1/night_v1/glare_v1/off
    rectify_on: Optional[bool]
    pad_used: Optional[float]
    upscale_min: Optional[Tuple[int, int]]
    drop: bool
    reason: str
    metrics: AutoMetrics


@dataclass
class AutoConfig:
    enable: bool = True
    every_n: int = 3

    luma_day: float = 95.0
    luma_night: float = 65.0
    sat_glare: float = 0.08
    blur_min: float = 35.0

    hyst_sec: float = 2.0

    drop_on_blur: bool = True
    drop_on_glare: bool = False

    allow_rectify: bool = True
    allow_pad: bool = True
    allow_upscale: bool = True

    upscale_day: Tuple[int, int] = (480, 144)
    upscale_night: Tuple[int, int] = (720, 224)

    # pad presets (используем, если allow_pad=True)
    pad_base: float = 0.06
    pad_small: float = 0.12
    pad_small_w: int = 260
    pad_small_h: int = 85
    pad_max: float = 0.16


class AutoState:
    """
    Хранит последний режим и время переключения.
    """
    def __init__(self) -> None:
        self.mode: str = "day"
        self.last_change_ts: float = 0.0
        self.tick: int = 0
        self.last_metrics: Optional[AutoMetrics] = None


def compute_metrics(img_bgr: np.ndarray) -> AutoMetrics:
    """
    Метрики считаем по уменьшенной картинке, чтобы было быстро.
    """
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    # resize для стабильности
    hh, ww = g.shape[:2]
    target_w = 320
    if ww > target_w:
        scale = target_w / float(ww)
        g = cv2.resize(g, (target_w, max(1, int(round(hh * scale)))), interpolation=cv2.INTER_AREA)

    # luma stats
    luma = g.astype(np.float32)
    luma_mean = float(np.mean(luma))
    p10 = float(np.percentile(luma, 10))
    p90 = float(np.percentile(luma, 90))

    # blur: variance of Laplacian
    lap = cv2.Laplacian(g, cv2.CV_64F)
    blur_var = float(lap.var())

    # sat/dark ratios
    sat_ratio = float(np.mean(g >= 250))
    dark_ratio = float(np.mean(g <= 18))

    return AutoMetrics(
        luma_mean=luma_mean,
        luma_p10=p10,
        luma_p90=p90,
        blur_var=blur_var,
        sat_ratio=sat_ratio,
        dark_ratio=dark_ratio,
    )


def _candidate_mode(m: AutoMetrics, cfg: AutoConfig) -> Tuple[str, str]:
    """
    Возвращает (mode, reason).
    """
    if m.sat_ratio >= float(cfg.sat_glare):
        return "glare", f"sat_ratio={m.sat_ratio:.3f}>=glare({cfg.sat_glare})"

    if m.blur_var < float(cfg.blur_min):
        return "blur", f"blur_var={m.blur_var:.1f}<blur_min({cfg.blur_min})"

    if m.luma_mean <= float(cfg.luma_night):
        return "night", f"luma_mean={m.luma_mean:.1f}<=night({cfg.luma_night})"

    if m.luma_mean >= float(cfg.luma_day):
        return "day", f"luma_mean={m.luma_mean:.1f}>=day({cfg.luma_day})"

    # середина -> оставляем как day по умолчанию (будет стабильно)
    return "day", f"mid_luma={m.luma_mean:.1f}"


def decide_auto(
    now_ts: float,
    img_bgr: np.ndarray,
    cfg: AutoConfig,
    st: AutoState,
    bbox_w: Optional[int] = None,
    bbox_h: Optional[int] = None,
) -> Optional[AutoDecision]:
    """
    Возвращает AutoDecision раз в cfg.every_n тиков (или если enable=False -> None).
    bbox_w/h — размер bbox (до pad), чтобы авто мог выбрать pad.
    """
    st.tick += 1
    if not cfg.enable:
        return None
    if cfg.every_n > 1 and (st.tick % int(cfg.every_n) != 0):
        return None

    m = compute_metrics(img_bgr)
    st.last_metrics = m

    cand, reason = _candidate_mode(m, cfg)

    # hysteresis by time
    mode = st.mode
    if cand != st.mode:
        if st.last_change_ts <= 0.0:
            st.last_change_ts = float(now_ts)
        if (float(now_ts) - float(st.last_change_ts)) >= float(cfg.hyst_sec):
            st.mode = cand
            st.last_change_ts = float(now_ts)
            mode = cand
        else:
            mode = st.mode
    else:
        st.last_change_ts = float(now_ts)
        mode = st.mode

    # decision details
    profile = "day_v1"
    if mode == "night":
        profile = "night_v1"
    elif mode == "glare":
        profile = "glare_v1"
    elif mode == "blur":
        # blur: обработка мало помогает; лучше дропать или слать редко
        profile = "off"

    # rectify
    rectify_on: Optional[bool] = None
    if cfg.allow_rectify:
        if mode in ("glare",):
            # блики часто ломают quad -> лучше не усугублять
            rectify_on = False
        elif mode in ("day", "night"):
            rectify_on = True
        elif mode == "blur":
            rectify_on = True  # пусть будет, но может быть drop anyway
    # else: None (не трогаем)

    # pad
    pad_used: Optional[float] = None
    if cfg.allow_pad:
        pad_used = float(cfg.pad_base)
        if bbox_w is not None and bbox_h is not None:
            if int(bbox_w) < int(cfg.pad_small_w) or int(bbox_h) < int(cfg.pad_small_h):
                pad_used = float(cfg.pad_small)
        pad_used = max(0.0, min(float(cfg.pad_max), float(pad_used)))

        # ночью обычно чуть меньше пад, чтобы не тащить шум
        if mode == "night":
            pad_used = max(0.0, min(float(cfg.pad_max), float(pad_used) * 0.90))
        if mode == "glare":
            pad_used = max(0.0, min(float(cfg.pad_max), float(pad_used) * 0.85))

    # upscale
    upscale_min: Optional[Tuple[int, int]] = None
    if cfg.allow_upscale:
        if mode == "night":
            upscale_min = tuple(cfg.upscale_night)
        else:
            upscale_min = tuple(cfg.upscale_day)

    # drop
    drop = False
    if mode == "blur" and cfg.drop_on_blur:
        drop = True
    if mode == "glare" and cfg.drop_on_glare:
        drop = True

    return AutoDecision(
        mode=mode,
        profile=profile,
        rectify_on=rectify_on,
        pad_used=pad_used,
        upscale_min=upscale_min,
        drop=bool(drop),
        reason=str(reason),
        metrics=m,
    )