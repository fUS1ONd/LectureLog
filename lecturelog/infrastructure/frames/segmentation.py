"""Стадия B: сегментация таймлайна на режимы по оконным статистикам (дизайн §5.B).

v1 — правила по сигнатурам, без change-point detection: окна window_s без
перекрытия классифицируются независимо, соседние окна одного типа сливаются,
коротыши (< min_regime_s) поглощаются более длинным соседом."""
from __future__ import annotations

import numpy as np

from lecturelog.infrastructure.frames.types import FramesTuning, Regime, SignalTrack


def _classify_window(
    mad: np.ndarray, motion: np.ndarray, edge: np.ndarray, shift: np.ndarray, t: FramesTuning
) -> str:
    if float(np.mean(shift)) > t.shift_camera:
        return "camera"
    plateau_frac = float(np.mean(mad < t.mad_low))
    steps = int(np.sum(mad > t.mad_high))
    # «Печать»: почти каждый кадр меняется, но движение мелкое и локализованное
    micro_frac = float(np.mean((mad > 0.05) & (motion < t.micro_area_max)))
    edge_slope = float(np.polyfit(np.arange(len(edge)), edge, 1)[0]) if len(edge) > 2 else 0.0
    motion_mean = float(np.mean(motion))

    if micro_frac > 0.5 and steps <= 1 and motion_mean < t.micro_area_max and plateau_frac <= 0.6:
        return "code"
    if edge_slope > 1e-4 and motion_mean < 0.15:
        return "board"
    if plateau_frac > 0.6:
        return "slides"
    if motion_mean > 0.03:
        return "speaker"
    return "other"


def segment_regimes(track: SignalTrack, tuning: FramesTuning) -> list[Regime]:
    n = track.n_frames
    win = max(1, int(tuning.window_s * track.fps))
    labels: list[str] = []
    for start in range(0, n, win):
        sl = slice(start, min(start + win, n))
        labels.append(
            _classify_window(track.mad[sl], track.motion_frac[sl],
                             track.edge[sl], track.shift[sl], tuning)
        )

    # Склейка соседних окон одного типа в режимы
    regimes: list[Regime] = []
    for w_idx, kind in enumerate(labels):
        start_s = w_idx * win / track.fps
        end_s = min((w_idx + 1) * win, n) / track.fps
        if regimes and regimes[-1].kind == kind:
            regimes[-1].end_s = end_s
        else:
            regimes.append(Regime(start_s=start_s, end_s=end_s, kind=kind))

    # Поглощение коротышей: сливаем с более длинным соседом, пока все >= min_regime_s
    changed = True
    while changed and len(regimes) > 1:
        changed = False
        for i, r in enumerate(regimes):
            if r.duration_s >= tuning.min_regime_s:
                continue
            left = regimes[i - 1] if i > 0 else None
            right = regimes[i + 1] if i + 1 < len(regimes) else None
            host = max((x for x in (left, right) if x is not None),
                       key=lambda x: x.duration_s)
            if host is left:
                left.end_s = r.end_s
            else:
                right.start_s = r.start_s
            regimes.pop(i)
            changed = True
            break
    return regimes
