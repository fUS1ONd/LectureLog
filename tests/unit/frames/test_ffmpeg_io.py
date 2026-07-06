import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import (
    ThumbStore,
    decode_gray,
    decode_window,
    probe_duration,
)
from tests.support.synthetic_video import slides_frames, write_video


def _video(tmp_path, secs=20):
    return write_video(slides_frames(n_slides=2, secs_per_slide=secs // 2), tmp_path / "v.mp4")


def test_probe_duration(tmp_path):
    assert abs(probe_duration(_video(tmp_path)) - 20.0) < 1.5


def test_decode_gray_yields_scaled_frames(tmp_path):
    frames = list(decode_gray(_video(tmp_path), fps=1.0, width=160))
    assert 18 <= len(frames) <= 21
    h, w = frames[0].shape
    assert w == 160 and frames[0].dtype == np.uint8


def test_decode_gray_segment(tmp_path):
    frames = list(decode_gray(_video(tmp_path), fps=1.0, width=160, start_s=5.0, end_s=10.0))
    assert 4 <= len(frames) <= 6


def test_thumb_store_roundtrip(tmp_path):
    store = ThumbStore(tmp_path / "thumbs")
    img = np.full((90, 160), 200, dtype=np.uint8)
    store.put(3, img)
    loaded = store.get(3)
    assert loaded.shape == (90, 160)
    assert abs(float(loaded.mean()) - 200.0) < 3.0  # JPEG с потерями, но близко


def test_decode_window_fullres(tmp_path):
    frames = decode_window(_video(tmp_path), ts=10.0, window_s=2.0, max_fps=5)
    assert len(frames) >= 3
    assert frames[0].shape == (180, 320)  # нативное разрешение синтетики


def test_decode_gray_raises_on_corrupt_stream(tmp_path):
    # Усечённый mp4 с faststart: probe проходит, декод обрывается —
    # инфраструктурная ошибка должна пробрасываться, а не давать пустой поток.
    import subprocess

    import pytest

    fast = tmp_path / "fast.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(_video(tmp_path)),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(fast),
        ],
        check=True,
    )
    trunc = tmp_path / "trunc.mp4"
    trunc.write_bytes(fast.read_bytes()[: fast.stat().st_size // 2])
    with pytest.raises(RuntimeError, match="ffmpeg"):
        list(decode_gray(trunc, fps=1.0, width=160))


def test_decode_gray_early_break_does_not_raise(tmp_path):
    # Потребитель может закрыть генератор досрочно — это не ошибка декода
    gen = decode_gray(_video(tmp_path), fps=1.0, width=160)
    next(gen)
    gen.close()
