"""Стадия D1: политика доски — background model, ink-метрики, «дописал» (дизайн §5.D1).

Ключевое: все метрики считаются ПО МОДЕЛИ, а не по сырым кадрам — вставший
перед доской препод иначе выглядит как стирание."""
from __future__ import annotations

import numpy as np


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
