"""Стадия A: пер-кадровые сигналы грубого прохода (дизайн §5.A).

Один линейный проход по потоку gray-кадров; всё численно дёшево
(< 1 мин на 5400 кадров 320px). Попутно пишутся JPEG-тумбы."""

from __future__ import annotations

from collections.abc import Iterator

import cv2
import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.types import SignalTrack

_MOTION_THRESH = 15  # порог бинаризации диффа
_EDGE_THRESH = 40.0  # порог магнитуды Sobel для «пикселя-грани»
_DILATE = np.ones((3, 3), np.uint8)


def dhash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, :-1] > small[:, 1:]).flatten()
    return int.from_bytes(np.packbits(bits).tobytes(), "big")


def motion_mask(prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
    """Бинаризованный дифф, дилатированный: где движется. Используется и здесь,
    и в background model доски (D1)."""
    diff = cv2.absdiff(cur, prev)
    mask = (diff > _MOTION_THRESH).astype(np.uint8)
    return cv2.dilate(mask, _DILATE).astype(bool)


def _edge_density(gray: np.ndarray) -> float:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    return float((mag > _EDGE_THRESH).mean())


def compute_signals(
    frames: Iterator[np.ndarray],
    fps: float,
    thumbs: ThumbStore | None = None,
    ignore_bottom_frac: float = 0.0,
) -> SignalTrack:
    """ignore_bottom_frac — доля нижней части кадра, исключаемая из временных
    сигналов (mad/motion/shift): вшитые субтитры меняются каждые 1–3 с и иначе
    рвут все плато (реальный кейс — лекция с hardsub). edge/dhash/тумбы
    считаются по полному кадру: субтитры почти не влияют на плотность граней,
    а тумбы нужны целиком для VLM."""
    mad: list[float] = []
    motion: list[float] = []
    edge: list[float] = []
    shift: list[float] = []
    hashes: list[int] = []
    prev: np.ndarray | None = None
    prev_f32: np.ndarray | None = None

    for idx, frame in enumerate(frames):
        if thumbs is not None:
            thumbs.put(idx, frame)
        hashes.append(dhash(frame))
        edge.append(_edge_density(frame))
        crop = frame
        if ignore_bottom_frac > 0.0:
            crop = frame[: max(1, int(frame.shape[0] * (1.0 - ignore_bottom_frac))), :]
        f32 = crop.astype(np.float32)
        if prev is None:
            mad.append(0.0)
            motion.append(0.0)
            shift.append(0.0)
        else:
            mad.append(float(cv2.absdiff(crop, prev).mean()))
            motion.append(float(motion_mask(prev, crop).mean()))
            (dx, dy), _resp = cv2.phaseCorrelate(prev_f32, f32)
            shift.append(float(np.hypot(dx, dy)))
        prev, prev_f32 = crop, f32

    return SignalTrack(
        fps=fps,
        mad=np.asarray(mad, dtype=np.float32),
        motion_frac=np.asarray(motion, dtype=np.float32),
        edge=np.asarray(edge, dtype=np.float32),
        shift=np.asarray(shift, dtype=np.float32),
        dhash=hashes,
    )
