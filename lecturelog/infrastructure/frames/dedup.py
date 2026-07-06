"""Глобальный дедуп кандидатов до VLM QC.

QC группирует семантические дубли только внутри батча ≤ vlm_batch — повтор
слайда через полчаса лекции (recap) или дубль «полноэкранная врезка vs общий
план» в разные батчи не попадает. Здесь — детерминированный проход по всем
кандидатам: dhash-близнецы одного типа схлопываются, из дублей остаётся кадр
с большей edge-плотностью (полноэкранная врезка выигрывает у общего плана,
поздний build — у раннего). Кандидаты-пары «код+вывод» не трогаются."""

from __future__ import annotations

import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import dhash
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, SignalTrack


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _content_hash(img: np.ndarray, subtitle_band_frac: float) -> int:
    """dhash без нижней полосы: hardsub-субтитры искажают хеш и разводят
    истинные дубли (замер на реальной лекции: с кропом дубли 0-1 бит,
    разный контент 16+)."""
    if subtitle_band_frac > 0.0:
        img = img[: max(1, int(img.shape[0] * (1.0 - subtitle_band_frac))), :]
    return dhash(img)


def dedup_candidates(
    candidates: list[Candidate],
    store: ThumbStore,
    track: SignalTrack,
    tuning: FramesTuning,
) -> list[Candidate]:
    """Схлопнуть визуальные дубли между режимами/батчами. Вход и выход — по ts."""
    kept: list[Candidate] = []
    kept_hash: list[int | None] = []

    def _idx(cand: Candidate) -> int:
        return max(0, min(track.n_frames - 1, int(cand.ts * track.fps)))

    def _edge(cand: Candidate) -> float:
        return float(track.edge[_idx(cand)])

    for cand in sorted(candidates, key=lambda c: c.ts):
        if cand.pair_ts is not None:
            # Пара «код+вывод» — самый сильный сигнал завершённости, не дедупим
            kept.append(cand)
            kept_hash.append(None)
            continue
        if cand.source == "board_model" and cand.image is not None:
            h = _content_hash(cand.image, tuning.subtitle_band_frac)
        else:
            h = _content_hash(store.get(_idx(cand)), tuning.subtitle_band_frac)
        dup_at = next(
            (
                i
                for i, (prev, ph) in enumerate(zip(kept, kept_hash, strict=True))
                if ph is not None
                and prev.kind == cand.kind
                and _hamming(ph, h) <= tuning.dedup_hamming
            ),
            None,
        )
        if dup_at is None:
            kept.append(cand)
            kept_hash.append(h)
        elif _edge(cand) > _edge(kept[dup_at]):
            # Новый экземпляр контрастнее (полноэкранная врезка) — замещает
            # ранний вместе со своим ts: привязка уйдёт к лучшему показу
            kept[dup_at] = cand
            kept_hash[dup_at] = h
    return sorted(kept, key=lambda c: c.ts)
