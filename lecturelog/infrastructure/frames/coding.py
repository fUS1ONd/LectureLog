"""Стадия D2: live-coding — «точки остановки» (дизайн §5.D2).

Режимы code передекодируются на code_fps (см. provider): 1 fps не видит
паттерн печати. Здесь — чистая логика от списка кадров: state machine
edit-burst → тишина → кандидат; транскрипт — бесплатный оракул (буст score).

Мигающий курсор и посимвольная печать дают диффы одного порядка (пара
пикселей за кадр), поэтому мгновенный per-frame diff их не различает —
различается только «скважность»: печать меняет кадр почти каждый шаг,
курсор — раз в несколько кадров. Сигнал сглаживается скользящим средним
(окно edit_smooth_s) перед сравнением с порогами edit_area_*, что и
разводит печать (плато сглаженного сигнала выше порога) и мигание
(плато ниже порога)."""
from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime

# Словарь триггеров завершённости из транскрипта (дизайн §5.D2)
_TRIGGERS = (
    "запустим", "запускаем", "скомпилируем", "компилируем", "сохраняем",
    "сохраним", "вот и всё", "вот и все", "готово", "смотрите, что получилось",
    "посмотрим, что получилось", "выполним", "проверим",
)


def _area_frac(prev: np.ndarray, cur: np.ndarray, diff_thresh: int) -> float:
    return float((cv2.absdiff(cur, prev) > diff_thresh).mean())


def _vertical_shift(prev: np.ndarray, cur: np.ndarray) -> float:
    (_dx, dy), _ = cv2.phaseCorrelate(prev.astype(np.float32), cur.astype(np.float32))
    return abs(float(dy))


def coding_candidates_from_frames(
    frames: list[np.ndarray],
    fps: float,
    regime: Regime,
    tuning: FramesTuning,
    srt_blocks: list[tuple[float, str]],
) -> list[Candidate]:
    """srt_blocks — [(start_sec, text)] реплики транскрипта (для оракула)."""
    if len(frames) < 3:
        return []
    burst_frames_needed = tuning.edit_burst_s * fps
    quiet_frames_needed = tuning.stop_quiet_s * fps
    smooth_window = max(1, round(tuning.edit_smooth_s * fps))

    candidates: list[Candidate] = []
    edit_accum = 0.0      # накопленные кадры-правки текущего burst
    quiet_run = 0.0
    burst_done = False    # был ли burst, ждущий точку остановки
    last_switch_ts: float | None = None
    recent_raw: deque[float] = deque(maxlen=smooth_window)

    for i in range(1, len(frames)):
        ts = regime.start_s + i / fps
        raw_area = _area_frac(frames[i - 1], frames[i], tuning.code_diff_thresh)
        recent_raw.append(raw_area)
        area = sum(recent_raw) / len(recent_raw)  # сглаженный сигнал правок
        # Скролл распознаём по мгновенному сдвигу, а не по сглаженной площади:
        # одиночный кадр скролла — большой вертикальный shift независимо от area.
        is_scroll = raw_area > tuning.edit_area_min and _vertical_shift(
            frames[i - 1], frames[i]) > tuning.scroll_shift_min

        # Переключение окна — одиночный полноэкранный дифф; проверяем по
        # мгновенной (raw) площади: сглаживание размазало бы его ниже порога.
        if raw_area >= tuning.switch_area_min and not is_scroll:
            # Переключение окна: буст последнего кандидата + кадр вывода (пара)
            last_switch_ts = ts
            if candidates and ts - candidates[-1].ts <= tuning.pair_window_s:
                candidates[-1].score += 1.0
                # Кадр вывода после «устаканивания», но не дальше конца режима
                candidates[-1].pair_ts = min(
                    ts + tuning.pair_settle_s, regime.end_s - 1.0 / fps)
            edit_accum = 0.0
            quiet_run = 0.0
            burst_done = False
        elif is_scroll:
            # Середина скролла — не кандидат и не тишина; burst не сбрасываем
            quiet_run = 0.0
        elif tuning.edit_area_min <= area <= tuning.edit_area_max:
            edit_accum += 1
            quiet_run = 0.0
            if edit_accum >= burst_frames_needed:
                burst_done = True
        elif area < tuning.edit_area_min:
            quiet_run += 1
            if burst_done and quiet_run >= quiet_frames_needed:
                cand_ts = ts - quiet_run / fps  # начало тишины = точка остановки
                score = 1.0 + _oracle_boost(cand_ts, srt_blocks, tuning)
                candidates.append(Candidate(ts=cand_ts, kind="code",
                                            regime=regime, score=score))
                burst_done = False
                edit_accum = 0.0
    _ = last_switch_ts
    return candidates


def _oracle_boost(ts: float, srt_blocks: list[tuple[float, str]], t: FramesTuning) -> float:
    """Триггер («запустим», «сохраняем»…) в окне ±oracle_window_s → +0.5 к score."""
    for block_ts, text in srt_blocks:
        if abs(block_ts - ts) <= t.oracle_window_s:
            lowered = text.lower()
            if any(trig in lowered for trig in _TRIGGERS):
                return 0.5
    return 0.0
