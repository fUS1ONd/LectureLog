from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import compute_signals
from lecturelog.infrastructure.frames.slides_policy import slide_candidates
from lecturelog.infrastructure.frames.types import FramesTuning, Regime
from tests.support.synthetic_video import slides_frames


def _prepare(tmp_path, **kw):
    frames = slides_frames(**kw)
    store = ThumbStore(tmp_path / "t")
    track = compute_signals(iter(frames), fps=1.0, thumbs=store)
    return track, store, Regime(0.0, float(len(frames)), "slides")


def test_one_candidate_per_slide(tmp_path):
    track, store, regime = _prepare(tmp_path, n_slides=3, secs_per_slide=15)
    cands = slide_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 3
    # Кандидат — конец плато с отступом guard от следующей ступеньки
    assert 10 <= cands[0].ts <= 13


def test_builds_dedup_keeps_final_version(tmp_path):
    # 2 слайда с builds: плато «до» и «после» билда, дедуп по вложенности масок
    track, store, regime = _prepare(tmp_path, n_slides=2, secs_per_slide=20, builds=True)
    cands = slide_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 2  # по одному на слайд — финальные версии builds
    # Финальная версия — из второй половины слайда (после билда)
    assert cands[0].ts >= 10


def test_cap_per_regime(tmp_path):
    tuning = FramesTuning(max_per_regime=2)
    track, store, regime = _prepare(tmp_path, n_slides=5, secs_per_slide=10)
    cands = slide_candidates(regime, track, store, tuning)
    assert len(cands) <= 2
