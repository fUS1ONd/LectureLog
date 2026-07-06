"""Типы стадии кадров: сигналы, режимы, кандидаты, пороги.

Все численные пороги стадии собраны в FramesTuning — единой точке калибровки
(дизайн §11: «пороги — конфиг с дефолтами»). В коде политик магических чисел нет."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class FramesTuning:
    # A: грубый проход
    analysis_fps: float = 1.0
    analysis_width: int = 320
    # B: сегментация
    window_s: int = 30
    min_regime_s: int = 20
    mad_low: float = 2.0          # плато: средний abs-diff ниже — «ничего не меняется»
    mad_high: float = 20.0        # ступенька: смена слайда/склейка
    micro_area_max: float = 0.03  # доля кадра для «мелкого локализованного диффа» (печать)
    shift_camera: float = 1.5     # px глобальной трансляции → ручная камера/панорама
    # D1: доска
    gate_k: int = 5               # кадров без движения до обновления background model
    ink_delta: int = 200          # порог штриха после нормализации освещения (высокий:
                                   # штрих мела/маркера на порядок ярче фона; текстура
                                   # препода, случайно "въехавшая" в модель на редких
                                   # стоп-кадрах, заметно тусклее — так штрих отделяется
                                   # от этого шума)
    board_stable_s: int = 5       # «дописал»: ink стабилен столько секунд
    erase_drop_frac: float = 0.3  # стирание: падение ink за erase_window_s
    erase_window_s: int = 5
    novelty_frac: float = 0.2     # мин. доля нового ink для нового снимка
    board_shift_reset: float = 20.0  # px: едущая доска → сброс модели (высокий — локальные
                                      # переписывания/движение в кадре дают фазовой
                                      # корреляции шум в единицы px, это не панорама)
    min_ink_px: int = 150         # не снимать почти пустую доску
    # D2: live-coding
    code_fps: float = 4.0
    code_width: int = 480
    edit_area_min: float = 0.0005  # ниже — курсор/шум, не правка
    edit_area_max: float = 0.03
    switch_area_min: float = 0.3   # выше — переключение окна
    edit_burst_s: float = 8.0
    stop_quiet_s: float = 4.0
    pair_window_s: float = 15.0
    pair_settle_s: float = 1.0
    oracle_window_s: float = 10.0
    # D3: слайды
    plateau_min_s: int = 4
    plateau_guard_s: int = 2
    build_containment: float = 0.9
    max_per_regime: int = 20      # cap для слайдов с видео-демо (плато не наступает)
    # E: выемка
    seek_window_s: float = 2.0
    board_rebuild_s: int = 60     # окно full-res реконструкции доски перед кандидатом
    # Общие
    max_candidates: int = 80
    max_frames: int = 60
    vlm_batch: int = 16


@dataclass
class SignalTrack:
    """Пер-кадровые сигналы грубого прохода (1 fps, ~320px). Индекс == секунда."""

    fps: float
    mad: np.ndarray          # float32 [N] средний abs-diff с предыдущим кадром
    motion_frac: np.ndarray  # float32 [N] доля «движущихся» пикселей (бинаризованный дифф)
    edge: np.ndarray         # float32 [N] плотность граней (доля пикселей с сильным градиентом)
    shift: np.ndarray        # float32 [N] |глобальная трансляция| в px (phase correlation)
    dhash: list[int]         # грубая идентичность содержимого

    @property
    def n_frames(self) -> int:
        return len(self.mad)

    def idx_to_ts(self, idx: int) -> float:
        return idx / self.fps


REGIME_KINDS = ("slides", "board", "code", "terminal", "camera", "speaker", "other")


@dataclass
class Regime:
    start_s: float
    end_s: float
    kind: str                      # один из REGIME_KINDS
    bbox: tuple[float, float, float, float] | None = None  # нормализованный (x, y, w, h)
    board_kind: str = "none"       # chalk | marker | none

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass
class Candidate:
    """Момент-кандидат. Для source="board_model" несёт синтезированный кадр
    (реконструкция доски в analysis-разрешении — для дедупа/отладки; финальный
    рендер пересобирает модель в full-res, см. extract)."""

    ts: float
    kind: str
    source: str = "raw_frame"      # raw_frame | board_model
    score: float = 1.0
    regime: Regime | None = None
    image: np.ndarray | None = field(default=None, repr=False)
    pair_ts: float | None = None   # live-coding: ts кадра вывода в паре «код+вывод»
