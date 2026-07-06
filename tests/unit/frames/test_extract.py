import numpy as np

from lecturelog.infrastructure.frames.extract import (
    render_candidates,
    sharpest_frame,
    whiteboard_cleanup,
)
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime
from tests.support.synthetic_video import board_frames, slides_frames, write_video


def test_sharpest_frame_picks_non_blurred():
    import cv2
    sharp = slides_frames(n_slides=1, secs_per_slide=1)[0]
    blurred = cv2.GaussianBlur(sharp, (11, 11), 5)
    assert sharpest_frame([blurred, sharp, blurred]) is sharp


def test_whiteboard_cleanup_marker_whitens_background():
    img = np.full((90, 160), 180, dtype=np.uint8)  # сероватый фон
    img[40:43, 20:100] = 60  # штрих маркером
    out = whiteboard_cleanup(img, "marker")
    assert float(np.median(out)) > 200  # фон побелел
    assert out[41, 50] < 128  # штрих остался тёмным


def test_render_candidates_formats(tmp_path):
    video = write_video(slides_frames(n_slides=2, secs_per_slide=10),
                        tmp_path / "v.mp4")
    cands = [
        Candidate(ts=5.0, kind="slides", regime=Regime(0, 20, "slides")),
        Candidate(ts=15.0, kind="code", regime=Regime(0, 20, "code")),
    ]
    frames = render_candidates(video, cands, tmp_path / "out", FramesTuning())
    # Слайды — JPEG q90, код — PNG (JPEG-артефакты убивают мелкий текст)
    assert frames[0].path.suffix == ".jpg" and frames[1].path.suffix == ".png"
    assert all(f.path.exists() for f in frames)
    assert frames[0].timestamp == 5.0


def test_render_board_from_model_snapshot(tmp_path):
    video = write_video(board_frames(write_secs=20, erase_at=None, total_secs=30),
                        tmp_path / "v.mp4")
    snap = board_frames(write_secs=20, erase_at=None, total_secs=30,
                        with_teacher=False)[-1]
    cands = [Candidate(ts=25.0, kind="board", source="board_model", image=snap,
                       regime=Regime(0, 30, "board", board_kind="chalk"))]
    frames = render_candidates(video, cands, tmp_path / "out", FramesTuning())
    assert len(frames) == 1 and frames[0].path.suffix == ".png"
