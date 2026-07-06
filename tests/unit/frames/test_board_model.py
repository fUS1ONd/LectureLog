import numpy as np

from lecturelog.infrastructure.frames.board import BackgroundModel
from lecturelog.infrastructure.frames.signals import motion_mask
from tests.support.synthetic_video import board_frames


def test_model_reconstructs_board_without_teacher():
    frames = board_frames(write_secs=40, erase_at=None, total_secs=60,
                          with_teacher=True, seed=1)
    clean = board_frames(write_secs=40, erase_at=None, total_secs=60,
                         with_teacher=False, seed=1)
    model = BackgroundModel(frames[0], gate_k=5)
    for prev, cur in zip(frames, frames[1:], strict=False):
        m = model.update(cur, motion_mask(prev, cur))
    # Модель ближе к чистой доске, чем сырой кадр (препод стёрт)
    err_model = np.abs(m.astype(int) - clean[-1].astype(int)).mean()
    err_raw = np.abs(frames[-1].astype(int) - clean[-1].astype(int)).mean()
    assert err_model < err_raw * 0.5


def test_stationary_teacher_freezes_region_not_corrupts():
    base = np.full((90, 160), 40, dtype=np.uint8)
    base[30:33, 10:60] = 210  # штрих
    with_teacher = base.copy()
    with_teacher[20:70, 80:110] = 110  # препод встал и замер
    model = BackgroundModel(base, gate_k=5)
    prev = base
    for _ in range(30):  # стоит неподвижно 30 кадров
        model.update(with_teacher, motion_mask(prev, with_teacher))
        prev = with_teacher
    # ВАЖНО: неподвижный препод в итоге въедет в модель (это честно — гейт
    # по движению, не по семантике), но штрих вне препода не пострадал
    m = model.snapshot()
    assert (m[30:33, 10:60] > 180).all()
