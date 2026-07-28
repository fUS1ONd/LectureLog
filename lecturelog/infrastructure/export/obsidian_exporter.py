from __future__ import annotations

import re
import shutil
from pathlib import Path

from lecturelog.domain.models import Topic
from lecturelog.domain.ports import Exporter, ExportResult
from lecturelog.domain.slides import SlideAsset, SlidePlacement


def _slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-zа-яё0-9\-]", "", value)
    value = re.sub(r"\-+", "-", value)
    return value.strip("-") or "section"


def _heading_ref(title: str) -> str:
    # Якорь для wiki-ссылки Obsidian: буквальный текст заголовка без символов
    # [ ] # | ^, которые Obsidian игнорирует при сопоставлении ссылки с заголовком.
    # Регистр и пробелы сохраняются — Obsidian резолвит ссылки именно по тексту,
    # а не по GitHub-слагу (lowercase + дефисы), поэтому слагификация ломает ссылки.
    return re.sub(r"[\[\]#|^]", "", title).strip()


class ObsidianExporter(Exporter):
    """Реализация порта Exporter: раскладывает конспект.md + медиа + слайды в output/.

    НЕ зипует — zip собирается на лету при скачивании. Для media_kind="audio"
    встраивает виджет Audio Player, для "video" — нативный wiki-embed Obsidian.
    """

    async def export(
        self,
        topics: list[Topic],
        media_fragments: list[Path],
        output_dir: Path,
        media_kind: str,
        slide_assets: list[SlideAsset] | None = None,
        slide_placements: tuple[SlidePlacement, ...] = (),
        slide_images: list[object] | None = None,
    ) -> ExportResult:
        if slide_assets is None:
            slide_assets = [
                item
                if isinstance(item, SlideAsset)
                else SlideAsset(
                    slide_num=index,
                    path=item.path,
                    origin="video" if item.timestamp is not None else "document",
                    timestamp=item.timestamp,
                    caption=item.caption,
                    extracted_text="" if item.timestamp is None else None,
                    native_text_quality="none" if item.timestamp is None else None,
                )
                for index, item in enumerate(slide_images or [], start=1)
            ]
        if not slide_placements:
            section_by_slide = {
                slide_num: global_section_id
                for global_section_id, section in enumerate(
                    section for topic in topics for section in topic.sections
                )
                for slide_num in section.slide_indices
            }
            slide_placements = tuple(
                SlidePlacement(
                    asset.slide_num,
                    "inline"
                    if any(
                        f"<!-- slide:{asset.slide_num} -->" in section.content
                        for topic in topics
                        for section in topic.sections
                    )
                    else "section_gallery",
                    section_by_slide[asset.slide_num],
                    gallery_position="before_content",
                    anchor_confidence="fallback",
                    fallback_reason="legacy_export_adapter",
                )
                for asset in slide_assets
                if asset.slide_num in section_by_slide
            )
        output_root = output_dir / "output"
        media_dir = output_root / media_kind
        slides_dir = output_root / "slides"

        if output_root.exists():
            shutil.rmtree(output_root)

        media_dir.mkdir(parents=True, exist_ok=True)
        slides_dir.mkdir(parents=True, exist_ok=True)

        # Плоский список секций для нумерации медиа-фрагментов
        all_sections = [s for t in topics for s in t.sections]

        media_targets: list[Path] = []
        for idx, fragment in enumerate(media_fragments):
            title = all_sections[idx].title if idx < len(all_sections) else f"section-{idx + 1}"
            title_slug = _slugify(title)
            target = media_dir / f"{idx + 1:02d}-{title_slug}{fragment.suffix}"
            shutil.copy2(fragment, target)
            media_targets.append(target)

        slide_nums = [asset.slide_num for asset in slide_assets]
        if slide_nums != list(range(1, len(slide_assets) + 1)):
            raise ValueError("slide assets должны иметь уникальные непрерывные номера 1..N")
        slide_targets: dict[int, Path] = {}
        for item in slide_assets:
            # Суффикс сохраняем как есть: документные слайды — JPEG/PNG от
            # рендера страницы, видеокадры — PNG от извлечения из видео.
            target = slides_dir / f"slide-{item.slide_num:02d}{item.path.suffix}"
            shutil.copy2(item.path, target)
            slide_targets[item.slide_num] = target

        assets_by_num = {asset.slide_num: asset for asset in slide_assets}
        placements_by_section: dict[int, list[SlidePlacement]] = {}
        appendix: list[SlidePlacement] = []
        for placement in slide_placements:
            if placement.output_kind in {"inline", "section_gallery"}:
                if placement.global_section_id is None:
                    raise ValueError("main-text placement требует global_section_id")
                placements_by_section.setdefault(placement.global_section_id, []).append(placement)
            elif placement.output_kind == "appendix":
                appendix.append(placement)

        lines: list[str] = []

        # Двухуровневое оглавление
        if topics:
            lines.append("# Оглавление")
            lines.append("")
            for t_idx, topic in enumerate(topics):
                lines.append(f"{t_idx + 1}. [[#{_heading_ref(topic.title)}]]")
                for s_idx, section in enumerate(topic.sections):
                    lines.append(f"   {s_idx + 1}. [[#{_heading_ref(section.title)}]]")
            lines.append("")

        # Содержимое
        global_section_idx = 0
        for topic in topics:
            lines.append(f"# {topic.title}")
            lines.append("")

            for section in topic.sections:
                lines.append(f"## {section.title}")
                lines.append("")

                if global_section_idx < len(media_targets):
                    media_target = media_targets[global_section_idx]
                    media_rel = media_target.relative_to(output_root).as_posix()
                    lines.append(f"[{section.start} - {section.end}]")
                    lines.append("")
                    if media_kind == "audio":
                        # Плагин Audio Player рендерит виджет из код-блока с wiki-ссылкой
                        lines.append("```audio-player")
                        lines.append(f"[[{media_rel}]]")
                        lines.append("```")
                    else:
                        # Видео: нативный wiki-embed Obsidian рендерит HTML5-плеер.
                        # Код-блок video-player не поддерживает ни один плагин.
                        lines.append(f"![[{media_rel}]]")
                lines.append("")

                # Кадры с маркером <!-- slide:N --> встают инлайн в текст; без
                # маркера — галереей, положение которой задаёт gallery_position:
                # after_content не даёт слайдам опережать свой материал и вставать
                # перед чужим абзацем.
                content = section.content
                gallery_before: list[str] = []
                gallery_after: list[str] = []
                for placement in placements_by_section.get(global_section_idx, []):
                    slide_idx = placement.slide_num
                    target = slide_targets.get(slide_idx)
                    asset = assets_by_num.get(slide_idx)
                    if target is None or asset is None:
                        continue
                    rel = target.relative_to(output_root).as_posix()
                    alt = asset.caption or f"Слайд {slide_idx}"
                    image_line = f"![{alt}]({rel})"
                    marker = f"<!-- slide:{slide_idx} -->"
                    if placement.output_kind == "inline" and marker in content:
                        content = content.replace(marker, image_line)
                    elif placement.gallery_position == "after_content":
                        gallery_after.extend((image_line, ""))
                    else:
                        gallery_before.extend((image_line, ""))

                lines.extend(gallery_before)
                lines.append(content.strip())
                lines.append("")
                lines.extend(gallery_after)

                global_section_idx += 1

        if appendix:
            lines.extend(["# Непривязанные слайды", ""])
            for placement in sorted(appendix, key=lambda item: item.slide_num):
                target = slide_targets.get(placement.slide_num)
                asset = assets_by_num.get(placement.slide_num)
                if target is None or asset is None:
                    continue
                rel = target.relative_to(output_root).as_posix()
                alt = asset.caption or f"Слайд {placement.slide_num}"
                lines.extend(
                    [
                        f"## Слайд {placement.slide_num}",
                        "",
                        f"![{alt}]({rel})",
                        "",
                    ]
                )

        (output_root / "конспект.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

        # Зиповку убрали: возвращаем корень output/ и фактические пути —
        # заливку объектов и сборку zip делает вызывающий код.
        return ExportResult(
            output_root=output_root,
            media_targets=media_targets,
            slide_targets=slide_targets,
        )
