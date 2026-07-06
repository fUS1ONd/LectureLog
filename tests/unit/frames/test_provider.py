import json

from lecturelog.infrastructure.frames.provider import VideoFrameProvider
from lecturelog.infrastructure.frames.types import FramesTuning
from tests.support.synthetic_video import slides_frames, speaker_frames, write_video
from tests.unit.frames.test_vlm_classify import FakeLlm

SRT = """1
00:00:00,000 --> 00:00:30,000
вступление

2
00:00:30,000 --> 00:02:00,000
смотрим слайды
"""


def _video(tmp_path):
    # 30с спикер + 90с слайды (3 слайда по 30с)
    frames = speaker_frames(total_secs=30, seed=1) + slides_frames(
        n_slides=3, secs_per_slide=30)
    return write_video(frames, tmp_path / "lecture.mp4", fps=1)


def _srt(tmp_path):
    p = tmp_path / "t.srt"
    p.write_text(SRT, encoding="utf-8")
    return p


def _classify_resp(kinds):
    return json.dumps([
        {"idx": i + 1, "type": k, "content_bbox": None, "board_kind": "none"}
        for i, k in enumerate(kinds)
    ])


def _qc_keep_all(n):
    return json.dumps([
        {"idx": i + 1, "keep": True, "caption": f"Слайд {i + 1}", "dup_group": None}
        for i in range(n)
    ])


async def test_end_to_end_slides_lecture(tmp_path):
    llm = FakeLlm([_classify_resp(["speaker", "slides"]), _qc_keep_all(3)])
    provider = VideoFrameProvider(
        video_path=_video(tmp_path), srt_path=_srt(tmp_path),
        llm=llm, models=["m"], effort="low", tuning=FramesTuning(),
    )
    usage_events = []
    items = await provider.get_slides(tmp_path / "out",
                                      on_usage=lambda p: usage_events.append(p))
    assert 2 <= len(items) <= 3          # по кандидату на слайд
    assert all(i.timestamp is not None and i.timestamp >= 30 for i in items)
    assert all(i.caption for i in items)  # подписи из QC
    assert items == sorted(items, key=lambda i: i.timestamp)
    assert len(usage_events) == 2         # classify + qc


async def test_vlm_down_degrades_to_signatures(tmp_path):
    class BrokenLlm:
        async def call(self, *a, **kw):
            raise RuntimeError("free tier исчерпан")

    provider = VideoFrameProvider(
        video_path=_video(tmp_path), srt_path=_srt(tmp_path),
        llm=BrokenLlm(), models=["m"], effort="low", tuning=FramesTuning(),
    )
    items = await provider.get_slides(tmp_path / "out")
    # Классификация по сигнатурам B, QC пропущен — кадры есть, подписей нет
    assert len(items) >= 1
    assert all(i.caption is None for i in items)


async def test_speaker_only_video_returns_empty(tmp_path):
    video = write_video(speaker_frames(total_secs=60, seed=2), tmp_path / "v.mp4")
    llm = FakeLlm([_classify_resp(["speaker"])])
    provider = VideoFrameProvider(
        video_path=video, srt_path=_srt(tmp_path),
        llm=llm, models=["m"], effort="low", tuning=FramesTuning(),
    )
    assert await provider.get_slides(tmp_path / "out") == []  # это норма (дизайн §1)


def test_cap_is_pair_aware():
    # Пара «код+вывод» на границе cap не должна рваться: либо оба, либо никто
    from lecturelog.infrastructure.frames.provider import VideoFrameProvider
    from lecturelog.infrastructure.frames.types import Candidate

    cands = [
        Candidate(ts=10.0, kind="slides"),
        Candidate(ts=20.0, kind="code", pair_ts=25.0),  # рендерится в 2 кадра
        Candidate(ts=30.0, kind="slides"),
    ]
    capped = VideoFrameProvider._cap_by_frames(cands, max_frames=2)
    # Пара не влезает целиком (1+2 > 2) → пропущена, следующий одиночный взят
    assert [c.ts for c in capped] == [10.0, 30.0]
    assert sum(2 if c.pair_ts is not None else 1 for c in capped) <= 2

    capped3 = VideoFrameProvider._cap_by_frames(cands, max_frames=3)
    assert [c.ts for c in capped3] == [10.0, 20.0]  # пара целиком, 3-й не влез
