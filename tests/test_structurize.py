import asyncio
from pathlib import Path

import pytest

import lecturelog.pipeline.structurize as structurize_module
from lecturelog.pipeline.structurize import structurize


def test_structurize_runs_three_stages_and_returns_sections(tmp_path, monkeypatch) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "split_v1.md").write_text("SPLIT\n", encoding="utf-8")
    (prompts_dir / "slide_match_v1.md").write_text("MATCH\n", encoding="utf-8")
    (prompts_dir / "section_v1.md").write_text("SECTION {title} {start} {end}\n", encoding="utf-8")
    monkeypatch.setattr(structurize_module, "PROMPTS_DIR", prompts_dir)

    srt_path = tmp_path / "lecture.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nПервая часть\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nВторая часть\n",
        encoding="utf-8",
    )

    slide_image = tmp_path / "slide-01.png"
    slide_image.write_bytes(b"png-data")

    async def fake_call_gemini(pool, prompt, models=None, images=None):
        if prompt.startswith("SPLIT"):
            return '[{"title":"Intro","start":"00:00:00","end":"00:00:04"}]'
        if prompt.startswith("MATCH"):
            assert images is not None and len(images) == 1
            return '{"0":[1]}'
        if prompt.startswith("SECTION"):
            assert images is not None and len(images) == 1
            return "Готовый текст раздела"
        raise AssertionError("Неожиданный промпт")

    monkeypatch.setattr(structurize_module, "call_gemini", fake_call_gemini)

    progress: list[int] = []

    def on_progress(value: int) -> None:
        progress.append(value)

    sections = asyncio.run(
        structurize(
            srt_path=srt_path,
            slide_images=[slide_image],
            output_dir=tmp_path / "out",
            pool=object(),
            models=["gemini-test"],
            on_progress=on_progress,
        )
    )

    assert len(sections) == 1
    assert sections[0].title == "Intro"
    assert sections[0].content == "Готовый текст раздела"
    assert sections[0].slide_indices == [1]
    assert progress[-1] == 100


@pytest.mark.anyio
async def test_structurize_passes_models_list(tmp_path):
    """structurize передаёт список моделей в call_gemini."""
    from unittest.mock import MagicMock, patch
    from lecturelog.pipeline.structurize import structurize
    from lecturelog.llm.key_pool import KeyPool

    srt = tmp_path / "t.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nПривет мир\n", encoding="utf-8")

    captured_models = []
    call_n = {"n": 0}

    async def fake_call_gemini(pool, prompt, models=None, images=None):
        captured_models.append(models)
        call_n["n"] += 1
        if call_n["n"] == 1:
            return '[{"title":"Раздел 1","start":"00:00:00","end":"00:00:05"}]'
        if call_n["n"] == 2:
            return '{}'
        return "текст раздела"

    client = MagicMock()
    pool = KeyPool(clients=[client], rpm_per_key=1000)

    with patch("lecturelog.pipeline.structurize.call_gemini", side_effect=fake_call_gemini):
        await structurize(
            srt_path=srt,
            slide_images=[],
            output_dir=tmp_path / "out",
            pool=pool,
            models=["gemini-3-flash-preview", "gemini-2.5-flash"],
            on_progress=lambda p: None,
        )

    assert all(m == ["gemini-3-flash-preview", "gemini-2.5-flash"] for m in captured_models)

