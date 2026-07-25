from __future__ import annotations

import base64
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import fitz

from lecturelog.evaluation.language import attach_languages
from lecturelog.evaluation.models import (
    AlignmentData,
    EvaluationArtifacts,
    Finding,
    NoteBlock,
    SectionArtifact,
    Severity,
    SlideArtifact,
    TranscriptCue,
)

_MAX_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_SLIDE_IMAGE_BYTES = 5 * 1024 * 1024
_SLIDE_PATH_RE = re.compile(r"(?:^|/)slides/slide-(\d+)\.[A-Za-z0-9]+$")
_SLIDE_LINK_RE = re.compile(r"!\[[^\]]*]\(([^)\s]+)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TIMESTAMP_RE = re.compile(
    r"^(?P<h>\d{1,3}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})$"
)
_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class ArtifactLoadError(ValueError):
    """The result archive cannot be inspected safely."""


def _safe_infos(zf: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, ...]:
    infos = tuple(zf.infolist())
    total = 0
    seen: set[str] = set()
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        mode = info.external_attr >> 16
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or stat.S_ISLNK(mode)
        ):
            raise ArtifactLoadError(f"Unsafe ZIP member: {name!r}")
        if name in seen:
            raise ArtifactLoadError(f"Duplicate ZIP member: {name!r}")
        seen.add(name)
        if info.file_size > _MAX_MEMBER_BYTES:
            raise ArtifactLoadError(f"ZIP member is too large: {name!r}")
        total += info.file_size
        if total > _MAX_ARCHIVE_BYTES:
            raise ArtifactLoadError("Uncompressed ZIP content exceeds safety limit")
    return infos


def _find_member(names: tuple[str, ...], basename: str) -> str | None:
    matches = [name for name in names if PurePosixPath(name).name == basename]
    if not matches:
        return None
    matches.sort(key=lambda name: (len(PurePosixPath(name).parts), name))
    return matches[0]


def _decode(zf: zipfile.ZipFile, member: str) -> str:
    try:
        return zf.read(member).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ArtifactLoadError(f"{member!r} is not valid UTF-8") from error


def _slide_image_data_url(
    zf: zipfile.ZipFile,
    member: str | None,
    *,
    native_text_quality: str,
) -> str | None:
    """Read a bounded image only when native text cannot represent the slide."""
    if member is None or native_text_quality == "good":
        return None
    mime = _IMAGE_MIME_TYPES.get(PurePosixPath(member).suffix.casefold())
    if mime is None:
        return None
    info = zf.getinfo(member)
    if info.file_size > _MAX_SLIDE_IMAGE_BYTES:
        return None
    payload = zf.read(member)
    if len(payload) > _MAX_SLIDE_IMAGE_BYTES:
        return None
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _seconds(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    parts = value.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return None


def parse_structure(payload: dict[str, Any] | None) -> tuple[SectionArtifact, ...]:
    if not isinstance(payload, dict):
        return ()
    result: list[SectionArtifact] = []
    for topic in payload.get("sections", []):
        if not isinstance(topic, dict):
            continue
        topic_title = str(topic.get("title") or "")
        for subtopic in topic.get("subtopics", []):
            if not isinstance(subtopic, dict):
                continue
            media = subtopic.get("media")
            media = media if isinstance(media, dict) else {}
            slide_nums = tuple(
                value
                for value in subtopic.get("slide_nums", [])
                if isinstance(value, int) and not isinstance(value, bool) and value > 0
            )
            result.append(
                SectionArtifact(
                    section_id=len(result),
                    topic_title=topic_title,
                    title=str(subtopic.get("title") or ""),
                    content_md=str(subtopic.get("content_md") or ""),
                    start_s=_seconds(media.get("start")),
                    end_s=_seconds(media.get("end")),
                    slide_nums=slide_nums,
                )
            )
    return tuple(result)


def parse_markdown(
    markdown: str,
    sections: tuple[SectionArtifact, ...] = (),
) -> tuple[NoteBlock, ...]:
    lines = markdown.splitlines()
    raw: list[tuple[str, str, tuple[str, ...], int, int, int | None]] = []
    headings: list[str] = []
    current_section: int | None = None
    section_cursor = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        start = index
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            headings = headings[: level - 1]
            headings.append(title)
            if level == 2:
                for candidate in range(section_cursor, len(sections)):
                    if sections[candidate].title.strip() == title:
                        current_section = sections[candidate].section_id
                        section_cursor = candidate + 1
                        break
            raw.append(("heading", line, tuple(headings), start + 1, start + 1, current_section))
            index += 1
            continue
        stripped = line.lstrip()
        fence = stripped[:3] if stripped.startswith(("```", "~~~")) else None
        if fence:
            index += 1
            while index < len(lines):
                if lines[index].lstrip().startswith(fence):
                    index += 1
                    break
                index += 1
            kind = "code"
        else:
            while index + 1 < len(lines) and lines[index + 1].strip():
                if _HEADING_RE.match(lines[index + 1]):
                    break
                index += 1
            index += 1
            text_probe = "\n".join(lines[start:index]).lstrip()
            if _SLIDE_LINK_RE.match(text_probe) or text_probe.startswith("![["):
                kind = "image"
            elif text_probe.startswith(("- ", "* ", "+ ")) or re.match(r"\d+[.)]\s", text_probe):
                kind = "list"
            elif text_probe.startswith(">"):
                kind = "quote"
            elif text_probe.startswith("|") and "|" in text_probe[1:]:
                kind = "table"
            elif re.fullmatch(r"\[[^\]\n]+]\s*", text_probe):
                kind = "metadata"
            else:
                kind = "paragraph"
        raw.append(
            (
                kind,
                "\n".join(lines[start:index]),
                tuple(headings),
                start + 1,
                index,
                current_section,
            )
        )
    return attach_languages(
        tuple(
            NoteBlock(
                block_id=block_id,
                kind=kind,  # type: ignore[arg-type]
                text=text,
                heading_path=path,
                line_start=line_start,
                line_end=line_end,
                section_id=section_id,
            )
            for block_id, (kind, text, path, line_start, line_end, section_id) in enumerate(raw)
        )
    )


def _parse_srt_time(value: str) -> float | None:
    match = _TIMESTAMP_RE.match(value.strip())
    if not match:
        return None
    return (
        int(match["h"]) * 3600
        + int(match["m"]) * 60
        + int(match["s"])
        + int(match["ms"]) / 1000
    )


def parse_srt(srt: str) -> tuple[TranscriptCue, ...]:
    chunks = re.split(r"\r?\n\s*\r?\n", srt.strip())
    cues: list[TranscriptCue] = []
    for chunk in chunks:
        lines = chunk.splitlines()
        if not lines:
            continue
        time_index = next((i for i, line in enumerate(lines) if " --> " in line), None)
        if time_index is None:
            continue
        parts = lines[time_index].split(" --> ", maxsplit=1)
        start = _parse_srt_time(parts[0])
        end = _parse_srt_time(parts[1].split()[0])
        text = " ".join(line.strip() for line in lines[time_index + 1 :] if line.strip())
        if start is None or end is None or not text:
            continue
        try:
            source_id = int(lines[0].strip()) if time_index else len(cues) + 1
        except ValueError:
            source_id = len(cues) + 1
        cues.append(TranscriptCue(source_id, start, end, text))
    return tuple(cues)


def _alignment(payload: dict[str, Any] | None) -> AlignmentData | None:
    if not isinstance(payload, dict):
        return None
    assignments = tuple(item for item in payload.get("assignments", []) if isinstance(item, dict))
    placements = tuple(item for item in payload.get("placements", []) if isinstance(item, dict))
    version = payload.get("schema_version")
    return AlignmentData(
        version if isinstance(version, int) else None,
        str(payload["mode"]) if payload.get("mode") is not None else None,
        assignments,
        placements,
    )


def _pdf_slides(path: Path) -> tuple[tuple[str, ...], int]:
    try:
        with fitz.open(path) as document:
            return tuple(page.get_text("text").strip() for page in document), document.page_count
    except (fitz.FileDataError, OSError) as error:
        raise ArtifactLoadError(f"Cannot read slides PDF: {path}") from error


def load_evaluation_artifacts(
    result_zip: Path,
    slides_pdf: Path | None = None,
) -> EvaluationArtifacts:
    result_zip = Path(result_zip)
    findings: list[Finding] = []
    try:
        archive = zipfile.ZipFile(result_zip)
    except (OSError, zipfile.BadZipFile) as error:
        raise ArtifactLoadError(f"Cannot read result ZIP: {result_zip}") from error
    with archive:
        infos = _safe_infos(archive)
        names = tuple(info.filename for info in infos if not info.is_dir())
        note_member = _find_member(names, "конспект.md")
        structure_member = _find_member(names, "structure.json")
        transcript_member = _find_member(names, "transcript.srt")
        alignment_member = _find_member(names, "document-slide-alignment.json")
        if note_member is None:
            findings.append(
                Finding("missing_note", Severity.CRITICAL, "конспект.md is missing.", "result.zip")
            )
        if structure_member is None:
            findings.append(
                Finding(
                    "missing_structure",
                    Severity.CRITICAL,
                    "structure.json is missing.",
                    "result.zip",
                )
            )
        if transcript_member is None:
            findings.append(
                Finding(
                    "missing_transcript",
                    Severity.MAJOR,
                    "transcript.srt is missing.",
                    "result.zip",
                )
            )
        note = _decode(archive, note_member) if note_member else ""

        def load_json(member: str | None, label: str) -> dict[str, Any] | None:
            if member is None:
                return None
            try:
                value = json.loads(_decode(archive, member))
            except json.JSONDecodeError:
                findings.append(
                    Finding(
                        f"invalid_{label}_json",
                        Severity.CRITICAL,
                        f"{PurePosixPath(member).name} is not valid JSON.",
                        member,
                    )
                )
                return None
            if not isinstance(value, dict):
                findings.append(
                    Finding(
                        f"invalid_{label}_shape",
                        Severity.CRITICAL,
                        f"{PurePosixPath(member).name} must contain an object.",
                        member,
                    )
                )
                return None
            return value

        structure = load_json(structure_member, "structure")
        alignment_payload = load_json(alignment_member, "alignment")
        transcript = parse_srt(_decode(archive, transcript_member)) if transcript_member else ()
        sections = parse_structure(structure)
        blocks = parse_markdown(note, sections)
        image_paths: dict[int, str] = {}
        for name in names:
            match = _SLIDE_PATH_RE.search(name)
            if match:
                image_paths[int(match.group(1))] = name
        pdf_texts: tuple[str, ...] = ()
        pdf_page_count = None
        if slides_pdf is not None:
            pdf_texts, pdf_page_count = _pdf_slides(Path(slides_pdf))
        slide_numbers = set(image_paths)
        slide_numbers.update(range(1, len(pdf_texts) + 1))
        slide_values: list[SlideArtifact] = []
        for slide_num in sorted(slide_numbers):
            native_text = pdf_texts[slide_num - 1] if slide_num <= len(pdf_texts) else ""
            native_text_quality = (
                "good"
                if len(native_text) >= 40
                else "sparse"
                if native_text
                else "none"
            )
            path = image_paths.get(slide_num)
            slide_values.append(
                SlideArtifact(
                    slide_num=slide_num,
                    path=path,
                    native_text=native_text,
                    native_text_quality=native_text_quality,
                    image_data_url=_slide_image_data_url(
                        archive,
                        path,
                        native_text_quality=native_text_quality,
                    ),
                )
            )
        slides = tuple(slide_values)
    return EvaluationArtifacts(
        source_zip=result_zip,
        note_markdown=note,
        structure=structure,
        blocks=blocks,
        sections=sections,
        transcript=transcript,
        slides=slides,
        alignment=_alignment(alignment_payload),
        members=names,
        pdf_page_count=pdf_page_count,
        load_findings=tuple(findings),
    )
