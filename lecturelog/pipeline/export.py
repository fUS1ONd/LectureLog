from __future__ import annotations

import re
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lecturelog.models import Section


def _slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-zа-я0-9\-]", "", value)
    value = re.sub(r"\-+", "-", value)
    return value.strip("-") or "section"


async def export_result(
    sections: list[Section],
    audio_fragments: list[Path],
    slide_images: list[Path],
    output_dir: Path,
) -> Path:
    output_root = output_dir / "output"
    audio_dir = output_root / "audio"
    slides_dir = output_root / "slides"

    if output_root.exists():
        shutil.rmtree(output_root)

    audio_dir.mkdir(parents=True, exist_ok=True)
    slides_dir.mkdir(parents=True, exist_ok=True)

    audio_targets: list[Path] = []
    for idx, fragment in enumerate(audio_fragments):
        title_slug = _slugify(sections[idx].title if idx < len(sections) else f"section-{idx + 1}")
        target = audio_dir / f"{idx + 1:02d}-{title_slug}.mp3"
        shutil.copy2(fragment, target)
        audio_targets.append(target)

    slide_targets: list[Path] = []
    for idx, slide in enumerate(slide_images):
        target = slides_dir / f"slide-{idx + 1:02d}.png"
        shutil.copy2(slide, target)
        slide_targets.append(target)

    lines: list[str] = []
    for idx, section in enumerate(sections):
        lines.append(f"## {section.title}")
        if idx < len(audio_targets):
            audio_rel = audio_targets[idx].relative_to(output_root).as_posix()
            lines.append(f"[{section.start} - {section.end}] | [Аудио]({audio_rel})")
        else:
            lines.append(f"[{section.start} - {section.end}]")
        lines.append("")

        for slide_idx in section.slide_indices:
            pos = slide_idx - 1
            if 0 <= pos < len(slide_targets):
                rel = slide_targets[pos].relative_to(output_root).as_posix()
                lines.append(f"![Слайд {slide_idx}]({rel})")
                lines.append("")

        lines.append(section.content.strip())
        lines.append("")

    (output_root / "конспект.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    zip_path = output_dir / "result.zip"
    if zip_path.exists():
        zip_path.unlink()

    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zip_file:
        for path in output_root.rglob("*"):
            if path.is_file():
                zip_file.write(path, arcname=path.relative_to(output_dir))

    return zip_path
