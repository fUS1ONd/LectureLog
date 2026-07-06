from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime


def test_tuning_defaults_are_sane():
    t = FramesTuning()
    assert t.analysis_fps == 1.0 and t.analysis_width == 320
    assert 0 < t.build_containment <= 1.0
    assert t.max_frames <= t.max_candidates


def test_regime_duration():
    r = Regime(start_s=10.0, end_s=40.0, kind="slides")
    assert r.duration_s == 30.0


def test_candidate_ordering_by_ts():
    a, b = Candidate(ts=5.0, kind="slides"), Candidate(ts=2.0, kind="board")
    assert sorted([a, b], key=lambda c: c.ts)[0] is b
