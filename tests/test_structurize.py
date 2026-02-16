import asyncio
from pathlib import Path

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

    async def fake_call_gemini(pool, prompt, model, images=None, retries=5):
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
            model="gemini-test",
            on_progress=on_progress,
        )
    )

    assert len(sections) == 1
    assert sections[0].title == "Intro"
    assert sections[0].content == "Готовый текст раздела"
    assert sections[0].slide_indices == [1]
    assert progress[-1] == 100

