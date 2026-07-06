import json

from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.frames.types import FramesTuning
from lecturelog.infrastructure.frames.vlm import qc_frames
from tests.unit.frames.test_vlm_classify import FakeLlm


def _items(tmp_path, n):
    import cv2
    import numpy as np

    items = []
    for i in range(n):
        p = tmp_path / f"frame-{i:02d}.jpg"
        cv2.imwrite(str(p), np.full((90, 160), 100 + i, dtype=np.uint8))
        items.append(SlideImage(path=p, timestamp=float(i * 60)))
    return items


async def test_qc_drops_garbage_and_captions(tmp_path):
    resp = json.dumps(
        {
            "results": [
                {"idx": 1, "keep": True, "caption": "Титульный слайд", "dup_group": None},
                {"idx": 2, "keep": False, "caption": None, "dup_group": None},
                {"idx": 3, "keep": True, "caption": "Схема архитектуры", "dup_group": None},
            ]
        }
    )
    out = await qc_frames(
        FakeLlm([resp]),
        ["m"],
        "low",
        _items(tmp_path, 3),
        srt_text_at=lambda ts: "реплика",
        tuning=FramesTuning(),
    )
    assert len(out) == 2
    assert out[0].caption == "Титульный слайд"


async def test_qc_dedup_groups_keep_first(tmp_path):
    resp = json.dumps(
        {
            "results": [
                {"idx": 1, "keep": True, "caption": "Доска: определение", "dup_group": 1},
                {"idx": 2, "keep": True, "caption": "Доска: определение (дубль)", "dup_group": 1},
                {"idx": 3, "keep": True, "caption": "Код примера", "dup_group": None},
            ]
        }
    )
    out = await qc_frames(
        FakeLlm([resp]),
        ["m"],
        "low",
        _items(tmp_path, 3),
        srt_text_at=lambda ts: "",
        tuning=FramesTuning(),
    )
    assert len(out) == 2  # из группы дублей остаётся один (лучший = первый keep)


async def test_qc_accepts_bare_json_array_for_robustness(tmp_path):
    resp = json.dumps(
        [
            {"idx": 1, "keep": True, "caption": "Слайд", "dup_group": None},
            {"idx": 2, "keep": False, "caption": None, "dup_group": None},
        ]
    )
    out = await qc_frames(
        FakeLlm([resp]),
        ["m"],
        "low",
        _items(tmp_path, 2),
        srt_text_at=lambda ts: "",
        tuning=FramesTuning(),
    )
    assert len(out) == 1
    assert out[0].caption == "Слайд"


async def test_qc_malformed_response_keeps_all(tmp_path):
    out = await qc_frames(
        FakeLlm(["не json"]),
        ["m"],
        "low",
        _items(tmp_path, 2),
        srt_text_at=lambda ts: "",
        tuning=FramesTuning(),
    )
    assert len(out) == 2  # деградация: кадры чуть грязнее, но стадия работает
