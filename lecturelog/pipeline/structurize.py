from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Callable

from lecturelog.llm.gemini import call_gemini
from lecturelog.llm.key_pool import KeyPool
from lecturelog.models import Section
from lecturelog.srt import extract_srt_fragment


_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _read_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _parse_json_payload(raw: str):
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def structurize(
    srt_path: Path,
    slide_images: list[Path],
    output_dir: Path,
    pool: KeyPool,
    model: str,
    on_progress: Callable[[int, str], None],
) -> list[Section]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pool.model = model

    srt_content = srt_path.read_text(encoding="utf-8")

    on_progress(5, "Определение структуры лекции")
    split_prompt = _read_prompt("split_v1.md") + "\n" + srt_content
    split_raw = await call_gemini(pool, split_prompt)
    split_sections = _parse_json_payload(split_raw)

    sections: list[Section] = [
        Section(
            title=item["title"],
            start=item["start"],
            end=item["end"],
            content="",
            slide_indices=[],
        )
        for item in split_sections
    ]

    if slide_images:
        on_progress(25, "Привязка слайдов к разделам")
        slide_prompt = _read_prompt("slide_match_v1.md")
        payload = json.dumps(
            [{"title": s.title, "start": s.start, "end": s.end} for s in sections],
            ensure_ascii=False,
            indent=2,
        )
        images = [img.read_bytes() for img in slide_images]
        slide_raw = await call_gemini(pool, f"{slide_prompt}\n\nРазделы:\n{payload}", images=images)
        mapping = _parse_json_payload(slide_raw)
        for idx, section in enumerate(sections):
            values = mapping.get(str(idx), [])
            if isinstance(values, list):
                section.slide_indices = [int(v) for v in values if isinstance(v, (int, float, str))]

    section_prompt_template = _read_prompt("section_v1.md")

    async def process_one(idx: int, section: Section):
        fragment = extract_srt_fragment(srt_content, section.start, section.end)
        prompt = (
            section_prompt_template.format(title=section.title, start=section.start, end=section.end)
            + "\n"
            + fragment
        )

        images: list[bytes] = []
        for slide_idx in section.slide_indices:
            position = slide_idx - 1
            if 0 <= position < len(slide_images):
                images.append(slide_images[position].read_bytes())

        section.content = await call_gemini(pool, prompt, images=images or None)
        pct = min(95, 35 + int((idx + 1) / max(1, len(sections)) * 60))
        on_progress(pct, f"Сформирован раздел {idx + 1}/{len(sections)}")

    await asyncio.gather(*[process_one(i, section) for i, section in enumerate(sections)])

    output_path = output_dir / "sections.json"
    output_path.write_text(
        json.dumps([section.model_dump() for section in sections], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    on_progress(100, "Структурирование завершено")
    return sections
