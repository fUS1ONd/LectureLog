"""Декод видео для стадии кадров: rawvideo-пайп ffmpeg → numpy.

Все функции синхронные (CPU-bound): вызывающий код заворачивает их
в asyncio.to_thread. Тумбы хранятся JPEG'ами на диске, чтобы политики
выбирали кадры без ре-декода (дизайн §5.A)."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np


def probe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def _probe_size(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _even(x: int) -> int:
    return x - (x % 2)


def decode_gray(
    video: Path,
    fps: float,
    width: int,
    start_s: float | None = None,
    end_s: float | None = None,
) -> Iterator[np.ndarray]:
    """Прочитать видео (или отрезок) как поток gray-кадров (H, W) uint8."""
    src_w, src_h = _probe_size(video)
    w = _even(min(width, src_w))
    h = _even(round(src_h * w / src_w))
    cmd = ["ffmpeg", "-loglevel", "error"]
    if start_s is not None:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(video)]
    if end_s is not None:
        cmd += ["-t", f"{end_s - (start_s or 0.0):.3f}"]
    cmd += [
        "-vf", f"fps={fps},scale={w}:{h}",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    frame_bytes = w * h
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
    finally:
        proc.stdout.close()
        proc.wait()


def decode_window(video: Path, ts: float, window_s: float, max_fps: int = 10) -> list[np.ndarray]:
    """Точечная выемка: full-res gray кадры в окне ±window_s вокруг ts
    (accurate seek: -ss перед -i у ffmpeg точный с ре-декодом от keyframe)."""
    start = max(0.0, ts - window_s)
    src_w, _ = _probe_size(video)
    return list(decode_gray(video, fps=max_fps, width=src_w,
                            start_s=start, end_s=ts + window_s))


class ThumbStore:
    """JPEG-тумбы кадров грубого прохода: политики D перечитывают кадры
    по индексу без ре-декода видео (~20 КБ × N)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, idx: int, gray: np.ndarray) -> None:
        cv2.imwrite(str(self._root / f"{idx:06d}.jpg"), gray,
                    [cv2.IMWRITE_JPEG_QUALITY, 85])

    def get(self, idx: int) -> np.ndarray:
        img = cv2.imread(str(self._root / f"{idx:06d}.jpg"), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"нет тумбы {idx}")
        return img
