from __future__ import annotations

import subprocess

import numpy as np

from tests.support.synthetic_video import (
    board_frames,
    slides_frames,
    speaker_frames,
    typing_frames,
    write_video,
)


def _duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def test_write_video_creates_playable_file(tmp_path):
    frames = [np.full((180, 320), i * 8, dtype=np.uint8) for i in range(30)]
    path = write_video(frames, tmp_path / "gray.mp4", fps=1)
    assert path.exists()
    assert abs(_duration(path) - 30.0) < 1.5


def test_slides_frames_have_step_transitions():
    frames = slides_frames(n_slides=3, secs_per_slide=10)
    assert len(frames) == 30
    # Внутри слайда кадры идентичны, на границе — заметный скачок
    assert np.array_equal(frames[3], frames[4])
    diff = np.abs(frames[10].astype(int) - frames[9].astype(int)).mean()
    assert diff > 5.0


def test_board_frames_accumulate_ink_and_erase():
    frames = board_frames(write_secs=40, erase_at=50, total_secs=70, seed=1)
    ink_early = (frames[5] > 128).sum()
    ink_late = (frames[45] > 128).sum()
    ink_after_erase = (frames[60] > 128).sum()
    assert ink_late > ink_early  # штрихи копятся
    assert ink_after_erase < ink_late * 0.5  # стирание уничтожило больше половины


def test_typing_frames_have_small_localized_diffs():
    frames = typing_frames(total_secs=30, fps=4, seed=2)
    assert len(frames) == 120
    d = np.abs(frames[41].astype(int) - frames[40].astype(int))
    # Мелкий локализованный дифф: меняется < 3% кадра
    assert (d > 20).mean() < 0.03


def test_speaker_frames_have_large_motion():
    frames = speaker_frames(total_secs=20, seed=3)
    d = np.abs(frames[11].astype(int) - frames[10].astype(int))
    assert (d > 20).mean() > 0.02  # крупный движущийся блоб
