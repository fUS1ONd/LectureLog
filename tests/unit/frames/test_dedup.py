import numpy as np

from lecturelog.infrastructure.frames.dedup import dedup_candidates
from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, SignalTrack


def _track(n: int, edge: list[float] | None = None) -> SignalTrack:
    return SignalTrack(
        fps=1.0,
        mad=np.zeros(n, np.float32),
        motion_frac=np.zeros(n, np.float32),
        edge=np.asarray(edge if edge is not None else [0.1] * n, np.float32),
        shift=np.zeros(n, np.float32),
        dhash=[0] * n,
    )


def _slide(seed: int) -> np.ndarray:
    """Детерминированный «слайд»: контрастные блоки, уникальные по seed."""
    rng = np.random.default_rng(seed)
    img = np.full((90, 160), 235, dtype=np.uint8)
    for row in range(4):
        y = 10 + row * 20
        x = 10
        while x < 140:
            w = int(rng.integers(8, 25))
            img[y : y + 6, x : x + w] = 30
            x += w + 6
    return img


def test_repeated_slide_collapsed(tmp_path):
    # Один и тот же слайд показан трижды (ts=5, 100, 130) → остаётся один кадр
    store = ThumbStore(tmp_path)
    same, other = _slide(1), _slide(2)
    for idx in range(200):
        store.put(idx, same if idx in (5, 100, 130) else other)
    cands = [
        Candidate(ts=5.0, kind="slides"),
        Candidate(ts=50.0, kind="slides"),
        Candidate(ts=100.0, kind="slides"),
        Candidate(ts=130.0, kind="slides"),
    ]
    out = dedup_candidates(cands, store, _track(200), FramesTuning())
    assert [c.ts for c in out] == [5.0, 50.0]


def test_duplicate_prefers_higher_edge_density(tmp_path):
    # Дубль «полноэкранная врезка vs общий план»: побеждает кадр с большей
    # edge-плотностью (полноэкранный), даже если он позже
    store = ThumbStore(tmp_path)
    same = _slide(3)
    store.put(10, same)
    store.put(60, same)
    edge = [0.1] * 100
    edge[10] = 0.05  # общий план — контента меньше
    edge[60] = 0.30  # полноэкранная врезка
    cands = [Candidate(ts=10.0, kind="slides"), Candidate(ts=60.0, kind="slides")]
    out = dedup_candidates(cands, store, _track(100, edge), FramesTuning())
    assert len(out) == 1 and out[0].ts == 60.0


def test_different_kinds_not_compared(tmp_path):
    store = ThumbStore(tmp_path)
    same = _slide(4)
    store.put(10, same)
    store.put(60, same)
    cands = [Candidate(ts=10.0, kind="slides"), Candidate(ts=60.0, kind="code")]
    out = dedup_candidates(cands, store, _track(100), FramesTuning())
    assert len(out) == 2


def test_pairs_never_dropped(tmp_path):
    # Кандидаты-пары «код+вывод» из дедупа исключены: pair_ts рвать нельзя
    store = ThumbStore(tmp_path)
    same = _slide(5)
    store.put(10, same)
    store.put(60, same)
    cands = [
        Candidate(ts=10.0, kind="code", pair_ts=15.0),
        Candidate(ts=60.0, kind="code"),
    ]
    out = dedup_candidates(cands, store, _track(100), FramesTuning())
    assert len(out) == 2
