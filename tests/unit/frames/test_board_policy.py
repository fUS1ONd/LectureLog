from lecturelog.infrastructure.frames.board import board_candidates
from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import compute_signals
from lecturelog.infrastructure.frames.types import FramesTuning, Regime
from tests.support.synthetic_video import board_frames


def _prepare(tmp_path, *, board_kind="chalk", fps=1.0, **kw):
    frames = board_frames(**kw)
    if board_kind == "marker":
        # Маркерная доска: тёмный штрих на светлом фоне — инверсия меловой синтетики
        frames = [255 - f for f in frames]
    if fps != 1.0:
        # Синтетическое повышение fps: каждый кадр длится 1/fps секунды
        frames = [f for f in frames for _ in range(int(fps))]
    store = ThumbStore(tmp_path / "thumbs")
    track = compute_signals(iter(frames), fps=fps, thumbs=store)
    regime = Regime(0.0, len(frames) / fps, "board", board_kind=board_kind)
    return track, store, regime


def test_written_pause_produces_candidate(tmp_path):
    # Пишет 30с, потом 25с ничего не меняется → один кандидат «дописал»
    track, store, regime = _prepare(
        tmp_path, write_secs=30, erase_at=None, total_secs=55, seed=1)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 1
    assert 30 <= cands[0].ts <= 50  # после остановки письма, с учётом окна стабильности
    assert cands[0].source == "board_model" and cands[0].image is not None


def test_erase_snapshots_last_stable_state(tmp_path):
    # Пишет 40с, на 50-й стирание → кандидат пред-стирания с ts до 50с
    track, store, regime = _prepare(
        tmp_path, write_secs=40, erase_at=50, total_secs=70, seed=2)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert any(c.ts < 50 for c in cands)


def test_no_candidates_on_empty_board(tmp_path):
    track, store, regime = _prepare(
        tmp_path, write_secs=0, erase_at=None, total_secs=40, seed=3)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert cands == []  # min_ink_px: пустую доску не снимаем


def test_novelty_gate_no_duplicate_shots(tmp_path):
    # Одна доска, две длинные паузы БЕЗ дописывания между ними → один кандидат
    track, store, regime = _prepare(
        tmp_path, write_secs=25, erase_at=None, total_secs=80, seed=4)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 1


def test_marker_board_detected(tmp_path):
    # Маркер: тёмное на светлом — та же политика с обратной полярностью ink
    track, store, regime = _prepare(
        tmp_path, board_kind="marker",
        write_secs=30, erase_at=None, total_secs=55, seed=1)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 1
    assert 30 <= cands[0].ts <= 50


def test_fps_independent_thresholds(tmp_path):
    # Те же 55 секунд контента при fps=2: пороги в секундах, не в кадрах,
    # поэтому результат тот же — один кандидат «дописал» в том же окне
    track, store, regime = _prepare(
        tmp_path, fps=2.0, write_secs=30, erase_at=None, total_secs=55, seed=1)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 1
    assert 30 <= cands[0].ts <= 50
