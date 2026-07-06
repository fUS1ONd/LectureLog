import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import compute_signals, dhash
from tests.support.synthetic_video import board_frames, slides_frames, typing_frames


def test_dhash_identical_and_different():
    a = np.full((90, 160), 100, dtype=np.uint8)
    b = a.copy()
    b[:, :80] = 200
    assert dhash(a) == dhash(a.copy())
    assert dhash(a) != dhash(b)


def test_slides_signature_steps_and_plateaus(tmp_path):
    frames = slides_frames(n_slides=3, secs_per_slide=10)
    track = compute_signals(iter(frames), fps=1.0, thumbs=ThumbStore(tmp_path))
    assert track.n_frames == 30
    # Ступеньки на границах слайдов (кадры 10 и 20), плато внутри
    assert track.mad[10] > 5.0 and track.mad[20] > 5.0
    assert float(np.median(track.mad[3:9])) < 1.0


def test_board_signature_rising_edge(tmp_path):
    frames = board_frames(write_secs=40, erase_at=None, total_secs=40, seed=1)
    track = compute_signals(iter(frames), fps=1.0, thumbs=ThumbStore(tmp_path))
    # Плотность граней растёт по мере накопления штрихов
    assert float(track.edge[5:15].mean()) < float(track.edge[30:40].mean())


def test_typing_signature_small_motion(tmp_path):
    frames = typing_frames(total_secs=30, fps=1, burst_ranges=[(0, 30)], seed=2)
    track = compute_signals(iter(frames), fps=1.0, thumbs=ThumbStore(tmp_path))
    burst = track.motion_frac[2:28]
    assert float(burst.max()) < 0.05  # мелкие локализованные диффы, не крупное движение
    assert float((burst > 0).mean()) > 0.4  # но заметная доля кадров с изменениями


def test_thumbs_written(tmp_path):
    frames = slides_frames(n_slides=1, secs_per_slide=5)
    store = ThumbStore(tmp_path)
    compute_signals(iter(frames), fps=1.0, thumbs=store)
    assert store.get(0).shape == frames[0].shape
