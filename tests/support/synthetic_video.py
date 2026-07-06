"""Детерминированные синтетические видео для тестов стадии кадров.

Кадры — numpy uint8 grayscale (H, W); write_video собирает их в mp4 через
ffmpeg rawvideo-пайп. Генераторы имитируют сигнатуры типов лекций из дизайна:
слайды (ступеньки+плато), доска (накопление ink + препод + стирание),
live-coding (мелкие диффы + курсор + скролл), спикер (крупное движение).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

H, W = 180, 320


def write_video(frames: list[np.ndarray], path: Path, fps: int = 1) -> Path:
    h, w = frames[0].shape[:2]
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        input=b"".join(np.ascontiguousarray(f).tobytes() for f in frames),
        check=True,
    )
    return path


def _text_block(rng: np.random.Generator, frame: np.ndarray, y: int, dark: bool) -> None:
    """Строка «текста»: серия тёмных (или светлых) прямоугольников-слов."""
    x = 20
    while x < W - 40:
        word_w = int(rng.integers(10, 30))
        frame[y : y + 6, x : x + word_w] = 30 if dark else 220
        x += word_w + 8


def slides_frames(
    n_slides: int = 3, secs_per_slide: int = 10, builds: bool = False, seed: int = 0
) -> list[np.ndarray]:
    """Слайды: светлый фон, статичный «текст», резкая смена между слайдами.
    builds=True — на середине слайда добавляется ещё одна строка (bullet-build)."""
    rng = np.random.default_rng(seed)
    frames: list[np.ndarray] = []
    for _s in range(n_slides):
        base = np.full((H, W), 235, dtype=np.uint8)
        for row in range(3):
            _text_block(rng, base, 30 + row * 30, dark=True)
        built = base.copy()
        _text_block(rng, built, 150, dark=True)
        for t in range(secs_per_slide):
            if builds and t >= secs_per_slide // 2:
                frames.append(built.copy())
            else:
                frames.append(base.copy())
    return frames


def board_frames(
    write_secs: int = 40,
    erase_at: int | None = 50,
    total_secs: int = 70,
    with_teacher: bool = True,
    seed: int = 0,
) -> list[np.ndarray]:
    """Доска (мел): тёмный фон, каждую секунду записи добавляется штрих;
    «препод» — серый прямоугольник, медленно ездит и перекрывает доску;
    в erase_at доска полностью очищается за 2 секунды."""
    rng = np.random.default_rng(seed)
    board = np.full((H, W), 40, dtype=np.uint8)
    frames: list[np.ndarray] = []
    for t in range(total_secs):
        if t < write_secs:
            # Новый штрих: короткая светлая линия в «зоне письма», едущей слева направо
            x0 = 20 + int((W - 80) * t / max(write_secs, 1))
            y0 = int(rng.integers(30, H - 40))
            board[y0 : y0 + 3, x0 : x0 + int(rng.integers(15, 35))] = 210
        if erase_at is not None and erase_at <= t < erase_at + 2:
            board[:, :] = 40  # стёрли доску целиком
        frame = board.copy()
        if with_teacher:
            # Препод перекрывает часть доски и медленно двигается
            tx = 40 + int(30 * np.sin(t / 5.0)) + t % 3
            frame[60:170, tx : tx + 50] = 110
        frames.append(frame)
    return frames


def typing_frames(
    total_secs: int = 30,
    fps: int = 4,
    burst_ranges: list[tuple[int, int]] | None = None,
    scroll_at: int | None = None,
    seed: int = 0,
) -> list[np.ndarray]:
    """Live-coding: светлый фон, «код» печатается посимвольно (строка ширится
    на 2px за кадр) в burst-интервалах (секунды), вне них — тишина с мигающим
    курсором; scroll_at — сдвиг контента вверх на 12px (скролл)."""
    if burst_ranges is None:
        burst_ranges = [(2, 12), (18, 26)]
    screen = np.full((H, W), 230, dtype=np.uint8)
    frames: list[np.ndarray] = []
    line_y, line_x = 20, 10
    for i in range(total_secs * fps):
        t = i / fps
        in_burst = any(a <= t < b for a, b in burst_ranges)
        if in_burst:
            screen[line_y : line_y + 5, line_x : line_x + 2] = 40
            line_x += 2
            if line_x > W - 20:
                line_x = 10
                line_y += 10
        if scroll_at is not None and abs(t - scroll_at) < 1.0 / fps:
            screen = np.roll(screen, -12, axis=0)
            screen[-12:, :] = 230
            line_y = max(10, line_y - 12)
        frame = screen.copy()
        if i % (2 * max(fps // 2, 1)) == 0:  # мигающий курсор ~1 Гц
            frame[line_y : line_y + 5, line_x : line_x + 2] = 40
        frames.append(frame)
    return frames


def speaker_frames(total_secs: int = 20, seed: int = 0) -> list[np.ndarray]:
    """«Говорящая голова»: статичный фон, крупный блоб ходит по кадру."""
    rng = np.random.default_rng(seed)
    bg = np.full((H, W), 150, dtype=np.uint8)
    bg[: H // 3, :] = 170
    frames: list[np.ndarray] = []
    for t in range(total_secs):
        frame = bg.copy()
        cx = 100 + int(60 * np.sin(t / 2.0)) + int(rng.integers(-5, 6))
        frame[50:160, cx : cx + 60] = 80
        frames.append(frame)
    return frames
