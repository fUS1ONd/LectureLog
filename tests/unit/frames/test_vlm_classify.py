import json

import numpy as np

from lecturelog.infrastructure.frames.types import FramesTuning, Regime
from lecturelog.infrastructure.frames.vlm import classify_regimes


class FakeLlm:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def call(self, prompt, models, images=None, *, on_usage=None,
                   response_json=False, effort=None, retries=5):
        self.calls.append({"prompt": prompt, "images": images})
        if on_usage is not None:
            await on_usage({"model": models[0], "prompt": 100, "output": 10})
        return self._responses.pop(0)


def _regimes():
    return [
        Regime(0, 60, "slides"),
        Regime(60, 180, "board"),
        Regime(180, 300, "slides"),  # предварительный тип; VLM скажет code
    ]


def _thumb():
    return np.full((90, 160), 128, dtype=np.uint8)


async def test_classify_applies_vlm_verdicts_and_bbox():
    resp = json.dumps([
        {"idx": 1, "type": "slides", "content_bbox": [0.1, 0.1, 0.8, 0.8], "board_kind": "none"},
        {"idx": 2, "type": "board", "content_bbox": [0.0, 0.0, 1.0, 0.9], "board_kind": "chalk"},
        {"idx": 3, "type": "code", "content_bbox": [0.05, 0.0, 0.9, 1.0], "board_kind": "none"},
    ])
    llm = FakeLlm([resp])
    regimes = _regimes()
    out = await classify_regimes(
        llm, ["m"], "low", regimes, [_thumb()] * 3,
        micro_rate=[0.0, 0.0, 0.8], tuning=FramesTuning(), on_usage=None,
    )
    assert [r.kind for r in out] == ["slides", "board", "code"]
    assert out[1].board_kind == "chalk"
    assert out[0].bbox == (0.1, 0.1, 0.8, 0.8)


async def test_tie_breaker_slides_with_code_screenshot():
    # VLM говорит code, но временнáя сигнатура «не печатает» → остаётся slides
    resp = json.dumps([
        {"idx": 1, "type": "code", "content_bbox": [0.1, 0.1, 0.8, 0.8], "board_kind": "none"},
    ])
    out = await classify_regimes(
        FakeLlm([resp]), ["m"], "low", [Regime(0, 60, "slides")], [_thumb()],
        micro_rate=[0.0], tuning=FramesTuning(), on_usage=None,
    )
    assert out[0].kind == "slides"


async def test_implausible_bbox_falls_back_to_none():
    resp = json.dumps([
        {"idx": 1, "type": "slides", "content_bbox": [0.9, 0.9, 0.05, 0.05], "board_kind": "none"},
    ])
    out = await classify_regimes(
        FakeLlm([resp]), ["m"], "low", [Regime(0, 60, "slides")], [_thumb()],
        micro_rate=[0.0], tuning=FramesTuning(), on_usage=None,
    )
    assert out[0].bbox is None  # площадь < 10% — не верим


async def test_batching_over_16_regimes():
    n = 20
    r1 = json.dumps([{"idx": i + 1, "type": "slides", "content_bbox": None,
                      "board_kind": "none"} for i in range(16)])
    r2 = json.dumps([{"idx": i + 1, "type": "slides", "content_bbox": None,
                      "board_kind": "none"} for i in range(4)])
    llm = FakeLlm([r1, r2])
    out = await classify_regimes(
        llm, ["m"], "low", [Regime(i * 30, (i + 1) * 30, "other") for i in range(n)],
        [_thumb()] * n, micro_rate=[0.0] * n, tuning=FramesTuning(), on_usage=None,
    )
    assert len(llm.calls) == 2 and len(out) == n
