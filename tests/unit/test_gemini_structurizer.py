import json

import pytest

from lecturelog.infrastructure.structurize.gemini_structurizer import (
    GeminiStructurizer,
    _parse_json,
)


def test_parse_json_strips_code_fence():
    assert _parse_json("```json\n[1, 2]\n```") == [1, 2]


def test_parse_json_plain():
    assert _parse_json('{"a": 1}') == {"a": 1}


class ScriptedGemini:
    """Отдаёт ответы по очереди в порядке вызовов и фиксирует параметры каждого вызова."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.on_usage_seen = []
        self.recorded_calls = []

    async def call(
        self, prompt, models, images=None, *, on_usage=None, response_json=False, effort=None
    ):
        self.on_usage_seen.append(on_usage)
        self.recorded_calls.append({"models": list(models), "effort": effort, "images": images})
        r = self._responses[self.calls]
        self.calls += 1
        return r


def _make_structurizer(gemini, prompts_dir):
    return GeminiStructurizer(
        gemini_client=gemini,
        split_models=["m"],
        subsplit_models=["m"],
        render_models=["m"],
        concurrency_subsplit=1,
        concurrency_render=1,
        prompts_dir=prompts_dir,
        effort_split="low",
        effort_subsplit="low",
        effort_render="low",
    )


def _make_structurizer_distinct_stages(gemini, prompts_dir):
    """Structurizer с РАЗНЫМИ model-списками и effort по стадиям — чтобы тест мог
    отличить, какой .call принадлежит какой стадии, и поймать перепутанный effort."""
    return GeminiStructurizer(
        gemini_client=gemini,
        split_models=["split-model"],
        subsplit_models=["subsplit-model"],
        render_models=["render-model"],
        concurrency_subsplit=1,
        concurrency_render=1,
        prompts_dir=prompts_dir,
        effort_split="low",
        effort_subsplit="medium",
        effort_render="high",
    )


@pytest.fixture
def prompts_dir(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    for name in (
        "split_topics_v1.md",
        "split_v1.md",
        "slide_match_topics_v1.md",
        "slide_match_v1.md",
        "section_v1.md",
    ):
        (d / name).write_text("prompt {title} {start} {end}", encoding="utf-8")
    return d


@pytest.mark.asyncio
async def test_structurize_without_slides_builds_topics(tmp_path, prompts_dir):
    srt = tmp_path / "t.srt"
    srt.write_text("1\n00:00:00,000 --> 00:05:00,000\nтекст лекции\n", encoding="utf-8")

    # порядок вызовов: split_topics -> subsplit(тема0) -> render(секция0)
    topics_json = json.dumps([{"title": "Тема 1", "start": "0:00", "end": "5:00"}])
    sections_json = json.dumps([{"title": "Подтема 1", "start": "0:00", "end": "5:00"}])
    rendered_md = "## Подтема 1\nсодержание"
    gemini = ScriptedGemini([topics_json, sections_json, rendered_md])

    structurizer = _make_structurizer(gemini, prompts_dir)
    topics = await structurizer.structurize(
        srt_path=srt, slide_images=[], output_dir=tmp_path / "out"
    )

    assert len(topics) == 1
    assert topics[0].title == "Тема 1"
    assert len(topics[0].sections) == 1
    assert topics[0].sections[0].title == "Подтема 1"
    assert "содержание" in topics[0].sections[0].content


@pytest.mark.asyncio
async def test_structurize_subsplit_fallback_on_bad_json(tmp_path, prompts_dir):
    srt = tmp_path / "t.srt"
    srt.write_text("1\n00:00:00,000 --> 00:05:00,000\nтекст\n", encoding="utf-8")
    topics_json = json.dumps([{"title": "Тема 1", "start": "0:00", "end": "5:00"}])
    bad_subsplit = "это не json"
    rendered_md = "контент"
    gemini = ScriptedGemini([topics_json, bad_subsplit, rendered_md])

    structurizer = _make_structurizer(gemini, prompts_dir)
    topics = await structurizer.structurize(
        srt_path=srt, slide_images=[], output_dir=tmp_path / "out"
    )
    # fallback: тема целиком становится одной секцией с её title/start/end
    assert len(topics) == 1
    assert len(topics[0].sections) == 1
    assert topics[0].sections[0].title == "Тема 1"


@pytest.mark.asyncio
async def test_structurize_forwards_on_usage_to_every_gemini_call(tmp_path, prompts_dir):
    srt = tmp_path / "t.srt"
    srt.write_text("1\n00:00:00,000 --> 00:05:00,000\nтекст\n", encoding="utf-8")
    topics_json = json.dumps([{"title": "Тема 1", "start": "0:00", "end": "5:00"}])
    sections_json = json.dumps([{"title": "Подтема 1", "start": "0:00", "end": "5:00"}])
    gemini = ScriptedGemini([topics_json, sections_json, "контент"])

    async def on_usage(payload):
        return None

    structurizer = _make_structurizer(gemini, prompts_dir)
    await structurizer.structurize(
        srt_path=srt, slide_images=[], output_dir=tmp_path / "out", on_usage=on_usage
    )

    # каждый вызов gemini получил наш non-None on_usage
    assert gemini.on_usage_seen
    assert all(cb is on_usage for cb in gemini.on_usage_seen)


@pytest.mark.asyncio
async def test_structurize_with_slides_uses_per_stage_effort_and_models(tmp_path, prompts_dir):
    """Ветка со слайд-картинками: split -> subsplit -> rough slide-match ->
    fine slide-match -> render. Проверяем, что КАЖДЫЙ .call получил effort и
    models своей стадии (split/subsplit/render не перепутаны), и что вызовы
    с картинками (rough/fine slide-match, render с привязанным слайдом)
    действительно происходят."""
    srt = tmp_path / "t.srt"
    srt.write_text("1\n00:00:00,000 --> 00:05:00,000\nтекст лекции\n", encoding="utf-8")

    slide_path = tmp_path / "slide1.png"
    slide_path.write_bytes(b"fake-slide-bytes")

    topics_json = json.dumps([{"title": "Тема 1", "start": "0:00", "end": "5:00"}])
    sections_json = json.dumps([{"title": "Подтема 1", "start": "0:00", "end": "5:00"}])
    rough_mapping_json = json.dumps({"0": [1]})
    fine_mapping_json = json.dumps({"0": [1]})
    rendered_md = "## Подтема 1\nсодержание"

    gemini = ScriptedGemini(
        [topics_json, sections_json, rough_mapping_json, fine_mapping_json, rendered_md]
    )

    structurizer = _make_structurizer_distinct_stages(gemini, prompts_dir)
    topics = await structurizer.structurize(
        srt_path=srt, slide_images=[slide_path], output_dir=tmp_path / "out"
    )

    assert len(topics) == 1
    assert topics[0].sections[0].slide_indices == [1]

    assert gemini.calls == 5
    calls = gemini.recorded_calls

    # Сопоставляем стадию по models-списку в записанном вызове.
    split_calls = [c for c in calls if c["models"] == ["split-model"]]
    subsplit_calls = [c for c in calls if c["models"] == ["subsplit-model"]]
    render_calls = [c for c in calls if c["models"] == ["render-model"]]

    # split_topics: 1 вызов, без картинок, effort_split
    assert len(split_calls) == 1
    assert split_calls[0]["effort"] == "low"
    assert split_calls[0]["images"] is None

    # subsplit + rough slide-match + fine slide-match: 3 вызова, effort_subsplit
    assert len(subsplit_calls) == 3
    assert all(c["effort"] == "medium" for c in subsplit_calls)
    # среди subsplit-вызовов есть вызовы с картинками (rough и fine slide-match)
    with_images = [c for c in subsplit_calls if c["images"]]
    assert len(with_images) == 2

    # render: 1 вызов, effort_render, с привязанным слайдом -> есть картинки
    assert len(render_calls) == 1
    assert render_calls[0]["effort"] == "high"
    assert render_calls[0]["images"]
