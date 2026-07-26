from __future__ import annotations

import asyncio
import inspect
import json
import logging
from pathlib import Path
from typing import Any

from lecturelog.domain.models import Section, Topic
from lecturelog.domain.ports import ProgressCallback, Structurizer, UsageCallback
from lecturelog.domain.slides import (
    SlideAsset,
    SlideAssignment,
    SlidePlacement,
    StructurizeContext,
    StructurizeResult,
)
from lecturelog.infrastructure.llm.llm_client import LlmClient
from lecturelog.infrastructure.slides.alignment.anchoring import anchor_assignment
from lecturelog.infrastructure.slides.alignment.catalog import native_text_fallback
from lecturelog.infrastructure.slides.alignment.diagnostics import write_diagnostic
from lecturelog.infrastructure.slides.alignment.service import (
    AlignmentTuning,
    DocumentAlignmentService,
)
from lecturelog.infrastructure.srt import extract_srt_fragment, format_time
from lecturelog.infrastructure.structurize.slide_backfill import backfill_missing_slides
from lecturelog.infrastructure.structurize.slide_mapping import normalize_slide_mapping

logger = logging.getLogger(__name__)


def _parse_json(raw_text: str) -> Any:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()
    return json.loads(text)


async def _emit_progress(on_progress: ProgressCallback | None, value: int) -> None:
    if on_progress is None:
        return
    maybe_awaitable = on_progress(value)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


class GeminiStructurizer(Structurizer):
    """Реализация порта Structurizer: 4-этапный алгоритм на Gemini.

    Этапы: (1) split транскрипта на темы, (2) subsplit тем на подтемы,
    (3) slide match (грубый по темам + точный по подтемам), (4) render
    Markdown по каждой подтеме. Использует normalize_slide_mapping и
    backfill_missing_slides для чистой логики привязки слайдов.
    """

    def __init__(
        self,
        gemini_client: LlmClient,
        split_models: list[str],
        subsplit_models: list[str],
        render_models: list[str],
        concurrency_subsplit: int,
        concurrency_render: int,
        prompts_dir: Path,
        effort_split: str,
        effort_subsplit: str,
        effort_render: str,
        document_alignment_mode: str = "legacy",
        document_alignment_tuning: AlignmentTuning = AlignmentTuning(),
    ) -> None:
        self._gemini = gemini_client
        self._split_models = split_models
        self._subsplit_models = subsplit_models
        self._render_models = render_models
        self._concurrency_subsplit = concurrency_subsplit
        self._concurrency_render = concurrency_render
        self._prompts_dir = Path(prompts_dir)
        self._effort_split = effort_split
        self._effort_subsplit = effort_subsplit
        self._effort_render = effort_render
        self._document_alignment_mode = document_alignment_mode
        self._document_alignment = DocumentAlignmentService(
            llm=gemini_client,
            models=subsplit_models,
            effort=effort_subsplit,
            prompts_dir=self._prompts_dir,
            tuning=document_alignment_tuning,
        )

    def _read_prompt(self, filename: str) -> str:
        return (self._prompts_dir / filename).read_text(encoding="utf-8")

    async def _split_topic_into_sections(
        self,
        topic_index: int,
        topic_data: dict[str, Any],
        srt_content: str,
        split_prompt_template: str,
        semaphore: asyncio.Semaphore,
        on_usage: UsageCallback | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Разбивает одну тему на подтемы по 3-5 минут."""
        start = str(topic_data["start"])
        end = str(topic_data["end"])
        fragment = extract_srt_fragment(srt_content, start, end)

        try:
            async with semaphore:
                raw = await self._gemini.call(
                    prompt=f"{split_prompt_template}\n{fragment}",
                    models=self._subsplit_models,
                    on_usage=on_usage,
                    effort=self._effort_subsplit,
                )
            sections = _parse_json(raw)
            if not isinstance(sections, list) or not sections:
                raise ValueError("Ответ subsplit должен быть непустым JSON-массивом")
            return (topic_index, sections)
        except Exception as error:
            # Fallback: тема целиком = одна секция
            logger.warning(
                "subsplit темы #%d не удался (%s), fallback в одну секцию",
                topic_index,
                error,
            )
            return (topic_index, [{"title": topic_data["title"], "start": start, "end": end}])

    async def _match_slides_for_topic(
        self,
        topic_index: int,
        sections_data: list[dict[str, Any]],
        slide_bytes: list[bytes],
        slide_indices: list[int],
        semaphore: asyncio.Semaphore,
        on_usage: UsageCallback | None = None,
    ) -> tuple[int, dict[int, list[int]]]:
        """Точный slide match для подтем одной темы."""
        if not slide_indices or not slide_bytes:
            return (topic_index, {})

        topic_slide_bytes = [
            slide_bytes[idx - 1] for idx in slide_indices if 1 <= idx <= len(slide_bytes)
        ]
        if not topic_slide_bytes:
            return (topic_index, {})

        prompt = self._read_prompt("slide_match_v1.md")
        prompt = f"{prompt}\n\nПодразделы темы:\n{json.dumps(sections_data, ensure_ascii=False)}"

        section_count = len(sections_data)
        slide_numbers_sorted = sorted(slide_indices)

        try:
            async with semaphore:
                raw = await self._gemini.call(
                    prompt=prompt,
                    models=self._subsplit_models,
                    images=topic_slide_bytes,
                    on_usage=on_usage,
                    effort=self._effort_subsplit,
                )
            parsed = _parse_json(raw)
            if not isinstance(parsed, dict):
                return (topic_index, {})

            # Преобразуем локальные индексы слайдов в глобальные
            llm_global: dict[int, list[int]] = {}
            for section_key, local_slide_list in parsed.items():
                if not isinstance(local_slide_list, list):
                    continue
                global_slides = []
                for local_idx in local_slide_list:
                    local_pos = int(local_idx) - 1
                    if 0 <= local_pos < len(slide_indices):
                        global_slides.append(slide_indices[local_pos])
                llm_global[int(section_key)] = global_slides

            mapping = normalize_slide_mapping(llm_global, section_count, slide_numbers_sorted)
            return (topic_index, mapping)
        except Exception as error:
            logger.warning(
                "slide match темы #%d не удался (%s), слайды не привязаны",
                topic_index,
                error,
            )
            return (topic_index, {})

    async def _render_section(
        self,
        global_index: int,
        section_data: dict[str, Any],
        srt_content: str,
        section_prompt_template: str,
        slide_indices: list[int],
        slide_bytes: list[bytes],
        semaphore: asyncio.Semaphore,
        on_usage: UsageCallback | None = None,
    ) -> tuple[int, Section]:
        title = str(section_data["title"])
        start = str(section_data["start"])
        end = str(section_data["end"])

        fragment = extract_srt_fragment(srt_content, start, end)
        prompt = section_prompt_template.format(title=title, start=start, end=end)
        prompt = f"{prompt}\n{fragment}"

        related_images = [
            slide_bytes[idx - 1] for idx in slide_indices if 1 <= idx <= len(slide_bytes)
        ]

        async with semaphore:
            content = await self._gemini.call(
                prompt=prompt,
                models=self._render_models,
                images=related_images if related_images else None,
                on_usage=on_usage,
                effort=self._effort_render,
            )
        return (
            global_index,
            Section(
                title=title,
                # Модель может скопировать полный SRT-таймкод (ЧЧ:ММ:СС,МСС);
                # ридер веба принимает только ЧЧ:ММ:СС — нормализуем на границе.
                start=format_time(start),
                end=format_time(end),
                content=content.strip(),
                slide_indices=slide_indices,
            ),
        )

    async def structurize(
        self,
        srt_path: Path,
        output_dir: Path,
        slide_assets: list[SlideAsset] | None = None,
        context: StructurizeContext | None = None,
        on_progress: ProgressCallback | None = None,
        on_usage: UsageCallback | None = None,
        slide_images: list[Path] | None = None,
    ) -> StructurizeResult:
        if slide_assets is None:
            slide_assets = [
                SlideAsset(
                    slide_num=index,
                    path=path,
                    origin="document",
                    extracted_text="",
                    native_text_quality="none",
                )
                for index, path in enumerate(slide_images or [], start=1)
            ]
        context = context or StructurizeContext(source_kind="audio")
        output_dir.mkdir(parents=True, exist_ok=True)
        srt_content = srt_path.read_text(encoding="utf-8")
        slide_bytes = [asset.path.read_bytes() for asset in slide_assets]

        # ── Этап 1: Topic split (0% → 10%) ─────────────────────────
        await _emit_progress(on_progress, 2)
        split_topics_prompt = f"{self._read_prompt('split_topics_v1.md')}\n{srt_content}"
        split_raw = await self._gemini.call(
            prompt=split_topics_prompt,
            models=self._split_models,
            on_usage=on_usage,
            effort=self._effort_split,
        )
        topics_data = _parse_json(split_raw)
        if not isinstance(topics_data, list):
            raise ValueError("Ответ split_topics должен быть JSON-массивом")
        await _emit_progress(on_progress, 10)

        # ── Этап 2: Subsplit (10% → 30%) ───────────────────────────
        subsplit_sem = asyncio.Semaphore(self._concurrency_subsplit)
        split_section_prompt = self._read_prompt("split_v1.md")
        subsplit_tasks = [
            asyncio.create_task(
                self._split_topic_into_sections(
                    topic_index=i,
                    topic_data=topic,
                    srt_content=srt_content,
                    split_prompt_template=split_section_prompt,
                    semaphore=subsplit_sem,
                    on_usage=on_usage,
                )
            )
            for i, topic in enumerate(topics_data)
        ]

        subsplit_results: list[tuple[int, list[dict[str, Any]]]] = []
        done = 0
        for task in asyncio.as_completed(subsplit_tasks):
            subsplit_results.append(await task)
            done += 1
            await _emit_progress(on_progress, 10 + int((done / max(len(subsplit_tasks), 1)) * 20))
        subsplit_results.sort(key=lambda x: x[0])

        topics_sections: list[list[dict[str, Any]]] = [sections for _, sections in subsplit_results]

        v2_assignments: tuple[SlideAssignment, ...] = ()
        if slide_assets and self._document_alignment_mode in {"shadow", "v2"}:
            try:
                v2_assignments = await self._document_alignment.align(
                    assets=slide_assets,
                    section_layout=topics_sections,
                    srt_content=srt_content,
                    on_usage=on_usage,
                )
            except Exception as error:  # noqa: BLE001 - explicit outer fail-safe
                logger.exception(
                    "document alignment %s failed: %s",
                    self._document_alignment_mode,
                    error,
                )
                if self._document_alignment_mode == "v2":
                    v2_assignments = tuple(
                        SlideAssignment(
                            asset.slide_num,
                            "unmentioned",
                            None,
                            (),
                            None,
                            "unresolved",
                            0.0,
                            "alignment_error",
                        )
                        for asset in slide_assets
                    )

        # ── Этап 3: Slide match (30% → 50%) ────────────────────────
        topic_slide_mapping: list[dict[int, list[int]]] = [{} for _ in topics_data]

        if slide_assets and self._document_alignment_mode != "v2":
            # 3a. Грубый match: темы → слайды (1 вызов)
            rough_prompt = self._read_prompt("slide_match_topics_v1.md")
            topics_json = json.dumps(topics_data, ensure_ascii=False)
            rough_prompt = f"{rough_prompt}\n\nТемы лекции:\n{topics_json}"
            rough_raw = await self._gemini.call(
                prompt=rough_prompt,
                models=self._subsplit_models,
                images=slide_bytes,
                on_usage=on_usage,
                effort=self._effort_subsplit,
            )
            rough_mapping = _parse_json(rough_raw)
            if not isinstance(rough_mapping, dict):
                rough_mapping = {}

            topic_slides: list[list[int]] = []
            for i in range(len(topics_data)):
                raw_list = rough_mapping.get(str(i), rough_mapping.get(i, []))
                if isinstance(raw_list, list):
                    topic_slides.append([int(x) for x in raw_list])
                else:
                    topic_slides.append([])

            await _emit_progress(on_progress, 40)

            # 3b. Точный match: подтемы → слайды (N вызовов)
            fine_tasks = [
                asyncio.create_task(
                    self._match_slides_for_topic(
                        topic_index=i,
                        sections_data=topics_sections[i],
                        slide_bytes=slide_bytes,
                        slide_indices=topic_slides[i],
                        semaphore=subsplit_sem,
                        on_usage=on_usage,
                    )
                )
                for i in range(len(topics_data))
            ]

            fine_done = 0
            for task in asyncio.as_completed(fine_tasks):
                idx, mapping = await task
                topic_slide_mapping[idx] = mapping
                fine_done += 1
                fine_pct = 40 + int((fine_done / max(len(fine_tasks), 1)) * 10)
                await _emit_progress(on_progress, fine_pct)

        await _emit_progress(on_progress, 50)

        if self._document_alignment_mode == "v2":
            for assignment in v2_assignments:
                if assignment.match_status != "discussed" or assignment.global_section_id is None:
                    continue
                global_id = 0
                for topic_index, sections in enumerate(topics_sections):
                    for local_index, _section in enumerate(sections):
                        if global_id == assignment.global_section_id:
                            topic_slide_mapping[topic_index].setdefault(local_index, []).append(
                                assignment.slide_num
                            )
                        global_id += 1

        # ── Этап 4: Render (50% → 100%) ────────────────────────────
        section_prompt_template = self._read_prompt("section_v1.md")
        render_sem = asyncio.Semaphore(self._concurrency_render)

        render_tasks = []
        global_idx = 0
        index_map: list[tuple[int, int]] = []

        for topic_idx, sections_list in enumerate(topics_sections):
            slide_map = topic_slide_mapping[topic_idx]
            for section_idx, section_data in enumerate(sections_list):
                section_slides = slide_map.get(section_idx, [])
                render_tasks.append(
                    asyncio.create_task(
                        self._render_section(
                            global_index=global_idx,
                            section_data=section_data,
                            srt_content=srt_content,
                            section_prompt_template=section_prompt_template,
                            slide_indices=section_slides,
                            slide_bytes=slide_bytes
                            if self._document_alignment_mode != "v2"
                            else [],
                            semaphore=render_sem,
                            on_usage=on_usage,
                        )
                    )
                )
                index_map.append((topic_idx, section_idx))
                global_idx += 1

        total_renders = len(render_tasks)
        rendered: dict[int, Section] = {}
        render_done = 0
        for task in asyncio.as_completed(render_tasks):
            gidx, section = await task
            rendered[gidx] = section
            render_done += 1
            await _emit_progress(on_progress, 50 + int((render_done / max(total_renders, 1)) * 50))

        # Собираем результат: list[Topic]
        result: list[Topic] = []
        for topic_idx, topic_data in enumerate(topics_data):
            topic_sections: list[Section] = []
            topic_slide_idxs: list[int] = []
            for gidx, (tidx, _sidx) in enumerate(index_map):
                if tidx == topic_idx:
                    section = rendered[gidx]
                    topic_sections.append(section)
                    topic_slide_idxs.extend(section.slide_indices)

            seen: set[int] = set()
            unique_slides: list[int] = []
            for s in topic_slide_idxs:
                if s not in seen:
                    seen.add(s)
                    unique_slides.append(s)
            unique_slides.sort()

            result.append(
                Topic(
                    title=str(topic_data["title"]),
                    # Нормализуем таймкод до ЧЧ:ММ:СС (см. Section выше).
                    start=format_time(str(topic_data["start"])),
                    end=format_time(str(topic_data["end"])),
                    sections=topic_sections,
                    slide_indices=unique_slides,
                )
            )

        # ── Страховка: вставляем слайды, потерянные LLM ──────
        if slide_assets and self._document_alignment_mode != "v2":
            backfill_missing_slides(result, len(slide_assets))

        await _emit_progress(on_progress, 100)
        if self._document_alignment_mode == "v2":
            placements: list[SlidePlacement] = []
            assets_by_num = {asset.slide_num: asset for asset in slide_assets}
            flat_sections = [section for topic in result for section in topic.sections]
            for assignment in v2_assignments:
                if assignment.match_status == "duplicate":
                    placements.append(
                        SlidePlacement(
                            assignment.slide_num,
                            "suppressed",
                            None,
                            anchor_confidence="none",
                            fallback_reason=assignment.reason_code,
                        )
                    )
                    continue
                entry_result = native_text_fallback(assets_by_num[assignment.slide_num])
                if assignment.global_section_id is not None and assignment.global_section_id < len(
                    flat_sections
                ):
                    section = flat_sections[assignment.global_section_id]
                    section.content, placement = anchor_assignment(
                        assignment,
                        entry_result.entry,
                        section.content,
                    )
                else:
                    placement = SlidePlacement(
                        assignment.slide_num,
                        "appendix",
                        None,
                        anchor_confidence="none",
                        fallback_reason=assignment.reason_code,
                    )
                placements.append(placement)
            for section_id, section in enumerate(flat_sections):
                section.slide_indices = sorted(
                    placement.slide_num
                    for placement in placements
                    if placement.global_section_id == section_id
                    and placement.output_kind in {"inline", "section_gallery"}
                )
            for topic in result:
                topic.slide_indices = sorted(
                    {slide for section in topic.sections for slide in section.slide_indices}
                )
            try:
                write_diagnostic(
                    output_dir / "document-slide-alignment.json",
                    mode=self._document_alignment_mode,
                    assignments=v2_assignments,
                    placements=tuple(placements),
                    prompt_versions={"catalog": "native-text-v1", "alignment": "dp-v1"},
                )
            except Exception as error:  # noqa: BLE001 - diagnostics must never fail processing
                logger.warning("document alignment diagnostics write failed: %s", error)
            return StructurizeResult(result, v2_assignments, tuple(placements))

        assignments: list[SlideAssignment] = []
        placements: list[SlidePlacement] = []
        by_slide = {
            slide_num: global_section_id
            for global_section_id, section in enumerate(
                section for topic in result for section in topic.sections
            )
            for slide_num in section.slide_indices
        }
        for asset in slide_assets:
            global_section_id = by_slide.get(asset.slide_num)
            if global_section_id is None:
                assignments.append(
                    SlideAssignment(
                        asset.slide_num,
                        "unmentioned",
                        None,
                        (),
                        None,
                        "unresolved",
                        0.0,
                        "legacy_unassigned",
                    )
                )
                placements.append(
                    SlidePlacement(
                        asset.slide_num,
                        "appendix",
                        None,
                        anchor_confidence="none",
                        fallback_reason="legacy_unassigned",
                    )
                )
            else:
                assignments.append(
                    SlideAssignment(
                        asset.slide_num,
                        "discussed",
                        global_section_id,
                        (),
                        None,
                        "probable",
                        0.0,
                        "legacy_mapping",
                    )
                )
                placements.append(
                    SlidePlacement(
                        asset.slide_num,
                        "section_gallery",
                        global_section_id,
                        gallery_position="before_content",
                        anchor_confidence="fallback",
                        fallback_reason="legacy_mapping",
                    )
                )
        if self._document_alignment_mode == "shadow":
            try:
                write_diagnostic(
                    output_dir / "document-slide-alignment.json",
                    mode=self._document_alignment_mode,
                    assignments=v2_assignments,
                    placements=tuple(placements),
                    prompt_versions={"catalog": "native-text-v1", "alignment": "dp-v1"},
                )
            except Exception as error:  # noqa: BLE001 - diagnostics must never fail processing
                logger.warning("document alignment diagnostics write failed: %s", error)
        return StructurizeResult(result, tuple(assignments), tuple(placements))
