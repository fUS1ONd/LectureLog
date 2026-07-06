"""Стадия D3: политика слайдов — плато и дедуп прогрессивных builds (дизайн §5.D3)."""
from __future__ import annotations

import cv2
import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime

_EDGE_THRESH = 40.0


def _content_mask(gray: np.ndarray) -> np.ndarray:
    """Бинаризованная маска контента по градиентам — общая для светлых и тёмных тем."""
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy) > _EDGE_THRESH


def _plateau_runs(mad: np.ndarray, low: float, min_len: int) -> list[tuple[int, int]]:
    """Индексные интервалы [a, b) с mad < low длиной >= min_len."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(mad):
        if v < low and start is None:
            start = i
        elif v >= low and start is not None:
            if i - start >= min_len:
                runs.append((start, i))
            start = None
    if start is not None and len(mad) - start >= min_len:
        runs.append((start, len(mad)))
    return runs


def slide_candidates(
    regime: Regime, track, store: ThumbStore, tuning: FramesTuning
) -> list[Candidate]:
    i0 = int(regime.start_s * track.fps)
    i1 = int(regime.end_s * track.fps)
    mad = track.mad[i0:i1]
    min_len = max(1, int(tuning.plateau_min_s * track.fps))
    guard = max(1, int(tuning.plateau_guard_s * track.fps))

    raw: list[Candidate] = []
    for a, b in _plateau_runs(mad, tuning.mad_low, min_len):
        # Последний кадр плато, но >= guard до следующей ступеньки (transition-смаз)
        idx = max(a, b - 1 - guard) + i0
        raw.append(Candidate(ts=idx / track.fps, kind="slides", regime=regime))

    # Дедуп builds: вложенность масок контента — |prev ∧ next| / |prev| > порога
    # и контент вырос → это build того же слайда, держим позднюю (финальную) версию.
    deduped: list[Candidate] = []
    prev_mask: np.ndarray | None = None
    for cand in raw:
        mask = _content_mask(store.get(int(cand.ts * track.fps)))
        if prev_mask is not None:
            containment = float((prev_mask & mask).sum()) / max(int(prev_mask.sum()), 1)
            if containment > tuning.build_containment and mask.sum() >= prev_mask.sum():
                deduped[-1] = cand  # поздний вытесняет ранний
                prev_mask = mask
                continue
        deduped.append(cand)
        prev_mask = mask

    # Cap на режим: слайд со встроенным видео/демо может дать шквал плато
    if len(deduped) > tuning.max_per_regime:
        deduped = deduped[: tuning.max_per_regime]
    return deduped
