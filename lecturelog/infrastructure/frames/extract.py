"""Стадия E: качественная выемка стопкадров (дизайн §5.E).

Отобранный ts — момент; кадр выбирается отдельно: точечный accurate-seek,
внутри окна ±seek_window_s — кадр максимальной резкости. Доска отдаётся из
background model: full-res реконструкция хвостового окна перед кандидатом
(человек стёрт) + whiteboard cleanup."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.frames.board import BackgroundModel
from lecturelog.infrastructure.frames.ffmpeg_io import _probe_size, decode_gray, decode_window
from lecturelog.infrastructure.frames.signals import motion_mask
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning


def sharpest_frame(frames: list[np.ndarray]) -> np.ndarray:
    """Максимальная резкость = variance of Laplacian; I-frames выигрывают сами."""
    return max(frames, key=lambda f: float(cv2.Laplacian(f, cv2.CV_64F).var()))


def whiteboard_cleanup(gray: np.ndarray, board_kind: str) -> np.ndarray:
    """Маркер: деление на размытый фон → фон белеет, штрихи контрастнее.
    Мел: CLAHE — локальный контраст читается заметно лучше."""
    if board_kind == "chalk":
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        return clahe.apply(gray)
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=25)
    norm = cv2.divide(gray.astype(np.float32), np.maximum(blur, 1).astype(np.float32))
    return np.clip(norm * 230.0, 0, 255).astype(np.uint8)


def _rebuild_board_fullres(video: Path, cand: Candidate, tuning: FramesTuning) -> np.ndarray:
    """Full-res background model по хвостовому окну перед кандидатом:
    analysis-модель (320px) хранится в cand.image как fallback."""
    start = max(0.0, cand.ts - tuning.board_rebuild_s)
    src_w, _ = _probe_size(video)
    frames = decode_gray(video, fps=1.0, width=src_w, start_s=start, end_s=cand.ts + 1.0)
    model: BackgroundModel | None = None
    prev: np.ndarray | None = None
    for frame in frames:
        if model is None:
            model = BackgroundModel(frame, gate_k=tuning.gate_k)
        else:
            model.update(frame, motion_mask(prev, frame))
        prev = frame
    if model is None:  # видео короче окна — деградация на analysis-снимок
        return cand.image
    return model.snapshot()


def render_candidates(
    video: Path, candidates: list[Candidate], out_dir: Path, tuning: FramesTuning
) -> list[SlideImage]:
    """Кандидаты (по ts) → файлы кадров. Код — PNG в нативном разрешении,
    слайды — JPEG q90, доска — PNG из модели + cleanup."""
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[SlideImage] = []
    ordered = sorted(candidates, key=lambda c: c.ts)
    idx = 0
    for cand in ordered:
        idx += 1
        items.append(_render_one(video, cand, cand.ts, out_dir, idx, tuning))
        if cand.pair_ts is not None:
            # Пара «код+вывод»: второй кадр после переключения окна
            idx += 1
            paired = Candidate(ts=cand.pair_ts, kind=cand.kind,
                               regime=cand.regime, source="raw_frame")
            items.append(_render_one(video, paired, cand.pair_ts, out_dir, idx, tuning))
    return items


def _render_one(
    video: Path, cand: Candidate, ts: float, out_dir: Path, idx: int, tuning: FramesTuning
) -> SlideImage:
    if cand.source == "board_model":
        kind = cand.regime.board_kind if cand.regime else "chalk"
        img = whiteboard_cleanup(_rebuild_board_fullres(video, cand, tuning), kind)
        path = out_dir / f"frame-{idx:02d}-board.png"
        cv2.imwrite(str(path), img)
        return SlideImage(path=path, timestamp=ts)

    window = decode_window(video, ts, tuning.seek_window_s)
    img = sharpest_frame(window) if window else None
    if img is None:
        raise RuntimeError(f"не удалось вынуть кадр на ts={ts}")
    if cand.kind in ("code", "terminal"):
        path = out_dir / f"frame-{idx:02d}-{cand.kind}.png"
        cv2.imwrite(str(path), img)  # PNG: JPEG-артефакты убивают мелкий текст
    else:
        path = out_dir / f"frame-{idx:02d}-{cand.kind}.jpg"
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return SlideImage(path=path, timestamp=ts)
