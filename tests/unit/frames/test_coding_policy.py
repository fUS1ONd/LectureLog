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


def _with_window_switch(frames, switch_sec, fps=FPS):
    # Переключение окна: с момента switch_sec кадры — «другое окно»
    # (инвертированный контент → полноэкранный мгновенный дифф)
    out = list(frames)
    for i in range(int(switch_sec * fps), len(out)):
        out[i] = 255 - out[i]
    return out


def test_window_switch_pairs_code_and_output():
    # burst 2-12с → кандидат ~12.5с, переключение окна на 18с →
    # буст score и pair_ts сразу после переключения
    frames = typing_frames(total_secs=30, fps=FPS, burst_ranges=[(2, 12)], seed=6)
    plain = _cands(frames)[0]
    cands = _cands(_with_window_switch(frames, switch_sec=18))
    assert len(cands) == 1
    boosted = cands[0]
    assert boosted.score > plain.score
    assert boosted.pair_ts is not None
    assert 18 <= boosted.pair_ts <= 20


def test_pair_ts_clamped_to_regime_end():
    # Переключение окна у самого конца режима → pair_ts не выходит за end_s
    total = 30
    frames = typing_frames(total_secs=total, fps=FPS, burst_ranges=[(10, 20)], seed=7)
    cands = _cands(_with_window_switch(frames, switch_sec=total - 2.0 / FPS))
    assert len(cands) == 1
    assert cands[0].pair_ts is not None
    assert cands[0].pair_ts <= total


def test_scroll_mid_is_not_candidate():
    # Скролл в середине тишины не должен породить кандидата и не должен
    # сбросить уже найденную точку остановки
    frames = typing_frames(total_secs=30, fps=FPS, burst_ranges=[(2, 12)],
                           scroll_at=20, seed=5)
    cands = _cands(frames)
    assert len(cands) == 1 and cands[0].ts < 20
