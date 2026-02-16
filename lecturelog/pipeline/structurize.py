from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, Callable

from lecturelog.llm.gemini import call_gemini
from lecturelog.llm.key_pool import KeyPool
from lecturelog.models import Section
from lecturelog.srt import extract_srt_fragment

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _read_prompt(filename: str) -> str:
    prompt_path = PROMPTS_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


def _parse_json(raw_text: str) -> Any:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()
    return json.loads(text)


async def _emit_progress(on_progress: Callable[[int], Any], value: int) -> None:
    maybe_awaitable = on_progress(value)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


async def _render_section(
    section_index: int,
    section_data: dict[str, Any],
    srt_content: str,
    section_prompt_template: str,
    slide_mapping: dict[int, list[int]],
    slide_images: list[Path],
    slide_bytes: list[bytes],
    pool: KeyPool,
    model: str,
) -> tuple[int, Section]:
    title = str(section_data["title"])
    start = str(section_data["start"])
    end = str(section_data["end"])

    fragment = extract_srt_fragment(srt_content, start, end)
    prompt = section_prompt_template.format(title=title, start=start, end=end)
    prompt = f"{prompt}\n{fragment}"

    related_slide_indices = slide_mapping.get(section_index, [])
    related_images = [
        slide_bytes[slide_idx - 1]
        for slide_idx in related_slide_indices
        if 1 <= slide_idx <= len(slide_images)
    ]

    content = await call_gemini(
        pool=pool,
        prompt=prompt,
        model=model,
        images=related_images if related_images else None,
    )
    return (
        section_index,
        Section(
            title=title,
            start=start,
            end=end,
            content=content.strip(),
            slide_indices=related_slide_indices,
        ),
    )


async def structurize(
    srt_path: Path,
    slide_images: list[Path],
    output_dir: Path,
    pool: KeyPool,
    model: str,
    on_progress: Callable[[int], Any],
) -> list[Section]:
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_content = srt_path.read_text(encoding="utf-8")
    await _emit_progress(on_progress, 5)

    split_prompt = f"{_read_prompt('split_v1.md')}\n{srt_content}"
    split_raw = await call_gemini(pool=pool, prompt=split_prompt, model=model)
    sections_data = _parse_json(split_raw)
    if not isinstance(sections_data, list):
        raise ValueError("Ответ split-этапа должен быть JSON-массивом")

    await _emit_progress(on_progress, 35)

    slide_mapping: dict[int, list[int]] = {}
    slide_bytes = [path.read_bytes() for path in slide_images]
    if slide_images:
        slide_prompt = _read_prompt("slide_match_v1.md")
        slide_prompt = f"{slide_prompt}\n\nРазделы лекции:\n{json.dumps(sections_data, ensure_ascii=False)}"
        mapping_raw = await call_gemini(
            pool=pool,
            prompt=slide_prompt,
            model=model,
            images=slide_bytes,
        )
        parsed_mapping = _parse_json(mapping_raw)
        if not isinstance(parsed_mapping, dict):
            raise ValueError("Ответ slide_match-этапа должен быть JSON-объектом")
        for key, value in parsed_mapping.items():
            if not isinstance(value, list):
                continue
            slide_mapping[int(key)] = [int(item) for item in value]

    await _emit_progress(on_progress, 55)

    section_prompt_template = _read_prompt("section_v1.md")
    tasks = [
        asyncio.create_task(
            _render_section(
                section_index=index,
                section_data=section,
                srt_content=srt_content,
                section_prompt_template=section_prompt_template,
                slide_mapping=slide_mapping,
                slide_images=slide_images,
                slide_bytes=slide_bytes,
                pool=pool,
                model=model,
            )
        )
        for index, section in enumerate(sections_data)
    ]

    rendered_sections: list[tuple[int, Section]] = []
    total = len(tasks)
    done = 0
    for task in asyncio.as_completed(tasks):
        rendered_sections.append(await task)
        done += 1
        await _emit_progress(on_progress, 55 + int((done / max(total, 1)) * 45))

    rendered_sections.sort(key=lambda item: item[0])
    result = [section for _, section in rendered_sections]

    await _emit_progress(on_progress, 100)
    return result

