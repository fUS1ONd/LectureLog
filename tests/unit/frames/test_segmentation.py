import numpy as np

from lecturelog.infrastructure.frames.segmentation import segment_regimes
from lecturelog.infrastructure.frames.types import FramesTuning, SignalTrack


def _track(mad, motion, edge, shift):
    n = len(mad)
    return SignalTrack(
        fps=1.0,
        mad=np.asarray(mad, np.float32),
        motion_frac=np.asarray(motion, np.float32),
        edge=np.asarray(edge, np.float32),
        shift=np.asarray(shift, np.float32),
        dhash=[0] * n,
    )


def test_slides_then_board():
    # 0–120: слайды (плато + ступенька каждые 30с); 120–240: доска (edge растёт)
    mad = [0.1] * 120 + [1.0] * 120
    for i in (30, 60, 90):
        mad[i] = 30.0
    motion = [0.0] * 120 + [0.01] * 120
    edge = [0.05] * 120 + list(np.linspace(0.05, 0.25, 120))
    shift = [0.0] * 240
    regimes = segment_regimes(_track(mad, motion, edge, shift), FramesTuning())
    kinds = [r.kind for r in regimes]
    assert kinds[0] == "slides" and kinds[-1] == "board"
    assert regimes[0].end_s <= 150  # граница около 120с (окно 30с → допуск)


def test_speaker_only_and_camera():
    mad = [5.0] * 60 + [8.0] * 60
    motion = [0.10] * 60 + [0.20] * 60
    edge = [0.08] * 120
    shift = [0.2] * 60 + [4.0] * 60  # вторая половина — панорама
    regimes = segment_regimes(_track(mad, motion, edge, shift), FramesTuning())
    assert regimes[0].kind == "speaker"
    assert regimes[-1].kind == "camera"


def test_short_segments_merged():
    # 10-секундный чужеродный кусок внутри слайдов должен слиться
    mad = [0.1] * 55 + [15.0] * 10 + [0.1] * 55
    motion = [0.0] * 55 + [0.3] * 10 + [0.0] * 55
    edge = [0.05] * 120
    shift = [0.0] * 120
    regimes = segment_regimes(_track(mad, motion, edge, shift), FramesTuning())
    assert all(r.duration_s >= FramesTuning().min_regime_s for r in regimes)
