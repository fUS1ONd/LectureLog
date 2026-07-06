from __future__ import annotations

import re
import shutil
from pathlib import Path

from lecturelog.domain.models import Topic
from lecturelog.domain.ports import Exporter, ExportResult, SlideImage


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
        slide_images: list[SlideImage],
        output_dir: Path,
        media_kind: str,
    ) -> ExportResult:
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

        slide_targets: list[Path] = []
        for idx, item in enumerate(slide_images):
            # Суффикс сохраняем как есть: документные слайды — JPEG/PNG от
            # рендера страницы, видеокадры — PNG от извлечения из видео.
            target = slides_dir / f"slide-{idx + 1:02d}{item.path.suffix}"
            shutil.copy2(item.path, target)
            slide_targets.append(target)

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

                for slide_idx in section.slide_indices:
                    pos = slide_idx - 1
                    if 0 <= pos < len(slide_targets):
                        rel = slide_targets[pos].relative_to(output_root).as_posix()
                        alt = slide_images[pos].caption or f"Слайд {slide_idx}"
                        lines.append(f"![{alt}]({rel})")
                        lines.append("")

                lines.append(section.content.strip())
                lines.append("")

                global_section_idx += 1

        (output_root / "конспект.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

        # Зиповку убрали: возвращаем корень output/ и фактические пути —
        # заливку объектов и сборку zip делает вызывающий код.
        return ExportResult(
            output_root=output_root,
            media_targets=media_targets,
            slide_targets=slide_targets,
        )
