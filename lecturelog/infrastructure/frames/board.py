"""Стадия D1: политика доски — background model, ink-метрики, «дописал» (дизайн §5.D1).

Ключевое: все метрики считаются ПО МОДЕЛИ, а не по сырым кадрам — вставший
перед доской препод иначе выглядит как стирание."""
from __future__ import annotations

import cv2
import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import motion_mask
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime


class BackgroundModel:
    """Реконструкция доски без человека: пиксель обновляется, только если
    в нём не было движения gate_k кадров подряд. Наивная медиана ломается,
    когда препод стоит на месте дольше полуокна, — гейт по движению нет."""

    def __init__(self, first: np.ndarray, gate_k: int = 5) -> None:
        self._model = first.copy()
        self._still = np.zeros(first.shape, dtype=np.int32)
        self._gate_k = gate_k

    def update(self, frame: np.ndarray, motion: np.ndarray) -> np.ndarray:
        self._still = np.where(motion, 0, self._still + 1)
        ready = self._still >= self._gate_k
        self._model[ready] = frame[ready]
        return self._model

    def snapshot(self) -> np.ndarray:
        return self._model.copy()

    def reset(self, frame: np.ndarray) -> None:
        """Сброс при смене поверхности (едущая доска, панорама)."""
        self._model = frame.copy()
        self._still[:] = 0


def ink_mask(model: np.ndarray, board_kind: str, delta: int, open_px: int = 0) -> np.ndarray:
    """Штриховые пиксели: нормализация освещения делением на сильно размытую
    версию, затем порог с полярностью по типу доски (мел — светлое на тёмном).
    open_px > 1 — морфологическое открытие: убирает мелкие пятна-шум (остатки
    текстуры препода, «въехавшей» в background model на редких стоп-кадрах)."""
    blur = cv2.GaussianBlur(model, (0, 0), sigmaX=15)
    norm = cv2.divide(model.astype(np.float32), np.maximum(blur, 1).astype(np.float32))
    # Полярность: мел — светлое на тёмном, маркер — тёмное на светлом
    mask = (norm > 1.0 + delta / 128.0 if board_kind == "chalk"
            else norm < 1.0 - delta / 128.0)
    if open_px > 1:
        kernel = np.ones((open_px, open_px), np.uint8)
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel).astype(bool)
    return mask


def _roi_slice(shape: tuple[int, int], bbox) -> tuple[slice, slice]:
    if bbox is None:
        return slice(None), slice(None)
    h, w = shape
    x, y, bw, bh = bbox
    return (slice(int(y * h), int((y + bh) * h)), slice(int(x * w), int((x + bw) * w)))


def board_candidates(
    regime: Regime, track, store: ThumbStore, tuning: FramesTuning
) -> list[Candidate]:
    """Проход по кадрам режима доски: копим модель, следим за ink(t).

    Кандидат «дописал»: ink стабилен >= board_stable_s И вырос на novelty_frac
    с прошлого снимка. Стирание: падение ink на erase_drop_frac за erase_window_s
    → фиксируем последнее стабильное состояние ДО падения (оно уже в модели)."""
    i0 = int(regime.start_s * track.fps)
    i1 = int(regime.end_s * track.fps)
    if i1 - i0 < 2:
        return []
    ys, xs = _roi_slice(store.get(i0).shape, regime.bbox)
    kind = regime.board_kind if regime.board_kind != "none" else "chalk"

    # Пороги в секундах → в кадры через fps трека (при fps != 1 иначе поедет)
    stable_frames = max(1, round(tuning.board_stable_s * track.fps))
    erase_frames = max(1, round(tuning.erase_window_s * track.fps))

    model = BackgroundModel(store.get(i0), gate_k=tuning.gate_k)
    prev = store.get(i0)
    candidates: list[Candidate] = []
    ink_hist: list[int] = []
    last_shot_ink: np.ndarray | None = None
    stable_run = 0
    shift_run = 0  # подряд кадров с shift выше порога (фильтр одиночных выбросов)
    # Последнее стабильное состояние — для снимка пред-стирания
    last_stable: tuple[float, np.ndarray, np.ndarray] | None = None  # (ts, model, ink)

    def emit(ts: float, snap: np.ndarray, ink: np.ndarray, score: float) -> None:
        nonlocal last_shot_ink
        candidates.append(Candidate(ts=ts, kind="board", source="board_model",
                                    score=score, regime=regime, image=snap))
        last_shot_ink = ink

    for i in range(i0 + 1, i1):
        cur = store.get(i)
        motion = motion_mask(prev, cur)
        if tuning.motion_dilate_extra > 0:
            # Расширенная маска движения: гасит пиксели текстуры препода,
            # случайно совпавшие кадр-к-кадру, — они не «замирают» в модели
            motion = cv2.dilate(motion.astype(np.uint8), np.ones((3, 3), np.uint8),
                                iterations=tuning.motion_dilate_extra).astype(bool)
        m = model.update(cur, motion)
        prev = cur
        shift_run = shift_run + 1 if track.shift[i] > tuning.board_shift_reset else 0
        if shift_run >= tuning.board_shift_persist:
            # Едущая доска/панорама (устойчивый сдвиг, не одиночный выброс):
            # зафиксировать накопленное и сбросить модель
            if last_stable is not None and _is_novel(last_stable[2], last_shot_ink, tuning):
                emit(last_stable[0], last_stable[1], last_stable[2], score=1.0)
            model.reset(cur)
            ink_hist.clear()
            stable_run = 0
            last_stable = None
            continue

        ink = ink_mask(m[ys, xs], kind, tuning.ink_delta, tuning.ink_open_px)
        count = int(ink.sum())
        ink_hist.append(count)
        ts = i / track.fps

        # Стирание: резкое падение против максимума недавнего окна
        win = ink_hist[-(erase_frames + 1):]
        if (len(win) > erase_frames and max(win) > tuning.min_ink_px
                and count < max(win) * (1 - tuning.erase_drop_frac)):
            if last_stable is not None and _is_novel(last_stable[2], last_shot_ink, tuning):
                emit(last_stable[0], last_stable[1], last_stable[2], score=1.2)
            ink_hist.clear()
            stable_run = 0
            last_stable = None
            continue

        # Стабильность: |Δink| в пределах эпсилона
        eps = max(tuning.ink_stable_eps_min,
                  int(tuning.ink_stable_eps_frac * max(count, 1)))
        if len(ink_hist) >= 2 and abs(ink_hist[-1] - ink_hist[-2]) <= eps:
            stable_run += 1
        else:
            stable_run = 0

        if stable_run >= stable_frames and count >= tuning.min_ink_px:
            last_stable = (ts, model.snapshot(), ink)
            if _is_novel(ink, last_shot_ink, tuning):
                emit(ts, model.snapshot(), ink, score=1.0)
            stable_run = 0  # не эмитить каждую секунду той же стабильности

    # Хвост режима: доска, дописанная к самому концу (граница режима — снимаем)
    if last_stable is not None and _is_novel(last_stable[2], last_shot_ink, tuning):
        emit(last_stable[0], last_stable[1], last_stable[2], score=0.9)
    return candidates


def _is_novel(ink: np.ndarray, last_shot: np.ndarray | None, tuning: FramesTuning) -> bool:
    """Новизна: >= novelty_frac нового ink с прошлого снимка (иначе — дубль доски)."""
    total = int(ink.sum())
    if total < tuning.min_ink_px:
        return False
    if last_shot is None:
        return True
    new = int((ink & ~last_shot).sum())
    return new / max(total, 1) >= tuning.novelty_frac
