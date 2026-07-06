from lecturelog.infrastructure.frames.coding import coding_candidates_from_frames
from lecturelog.infrastructure.frames.types import FramesTuning, Regime
from tests.support.synthetic_video import typing_frames

FPS = 4


def _cands(frames, srt_blocks=(), start_s=0.0):
    regime = Regime(start_s, start_s + len(frames) / FPS, "code")
    return coding_candidates_from_frames(
        frames, fps=FPS, regime=regime, tuning=FramesTuning(),
        srt_blocks=list(srt_blocks),
    )


def test_stop_point_after_burst():
    # Печать 2-12с, тишина 12-30с → одна точка остановки ~13-17с
    frames = typing_frames(total_secs=30, fps=FPS, burst_ranges=[(2, 12)], seed=1)
    cands = _cands(frames)
    assert len(cands) == 1
    assert 12 <= cands[0].ts <= 18


def test_cursor_blink_is_not_edit():
    # Только курсор мигает — вообще нет кандидатов (нет edit-burst)
    frames = typing_frames(total_secs=20, fps=FPS, burst_ranges=[], seed=2)
    assert _cands(frames) == []


def test_two_bursts_two_candidates():
    frames = typing_frames(total_secs=40, fps=FPS,
                           burst_ranges=[(2, 12), (20, 32)], seed=3)
    cands = _cands(frames)
    assert len(cands) == 2


def test_transcript_trigger_boosts_score():
    frames = typing_frames(total_secs=30, fps=FPS, burst_ranges=[(2, 12)], seed=4)
    plain = _cands(frames)[0]
    boosted = _cands(frames, srt_blocks=[(13.0, "а теперь запустим и посмотрим")])[0]
    assert boosted.score > plain.score


def test_scroll_mid_is_not_candidate():
    # Скролл в середине тишины не должен породить кандидата и не должен
    # сбросить уже найденную точку остановки
    frames = typing_frames(total_secs=30, fps=FPS, burst_ranges=[(2, 12)],
                           scroll_at=20, seed=5)
    cands = _cands(frames)
    assert len(cands) == 1 and cands[0].ts < 20
