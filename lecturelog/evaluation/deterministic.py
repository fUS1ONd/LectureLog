from __future__ import annotations

import re
from collections import Counter, defaultdict

from lecturelog.evaluation.language import language_findings
from lecturelog.evaluation.models import EvaluationArtifacts, Finding, Severity

_SLIDE_LINK_RE = re.compile(r"!\[[^\]]*]\(([^)\s]+)")
_SLIDE_NUM_RE = re.compile(r"(?:^|/)slides/slide-(\d+)\.[A-Za-z0-9]+$")


def _fence_findings(artifacts: EvaluationArtifacts) -> list[Finding]:
    stack: list[str] = []
    for line in artifacts.note_markdown.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker not in {"```", "~~~"}:
            continue
        if stack and stack[-1] == marker:
            stack.pop()
        elif not stack:
            stack.append(marker)
    if not stack:
        return []
    return [
        Finding(
            "unclosed_markdown_fence",
            Severity.CRITICAL,
            "The note contains an unclosed fenced block.",
            "конспект.md",
        )
    ]


def _markdown_findings(artifacts: EvaluationArtifacts) -> list[Finding]:
    findings: list[Finding] = []
    headings = [block for block in artifacts.blocks if block.kind == "heading"]
    heading_counts = Counter(block.text.strip().casefold() for block in headings)
    for block in headings:
        if heading_counts[block.text.strip().casefold()] > 1:
            findings.append(
                Finding(
                    "repeated_heading",
                    Severity.WARNING,
                    f"Heading is repeated: {block.text.strip()}",
                    "конспект.md",
                    block_id=block.block_id,
                    section_id=block.section_id,
                )
            )
    for index, block in enumerate(artifacts.blocks):
        if block.kind != "heading":
            continue
        next_block = artifacts.blocks[index + 1] if index + 1 < len(artifacts.blocks) else None
        current_level = len(block.text) - len(block.text.lstrip("#"))
        next_level = (
            len(next_block.text) - len(next_block.text.lstrip("#"))
            if next_block is not None and next_block.kind == "heading"
            else None
        )
        if next_block is None or (
            next_level is not None and next_level <= current_level
        ):
            findings.append(
                Finding(
                    "empty_heading",
                    Severity.MAJOR,
                    f"Heading has no content: {block.text.strip()}",
                    "конспект.md",
                    block_id=block.block_id,
                    section_id=block.section_id,
                )
            )
    normalized: defaultdict[str, list[int]] = defaultdict(list)
    for block in artifacts.blocks:
        if block.kind not in {"paragraph", "list", "quote"}:
            continue
        text = re.sub(r"\W+", " ", block.text.casefold()).strip()
        if len(text) >= 80:
            normalized[text].append(block.block_id)
    for ids in normalized.values():
        if len(ids) > 1:
            findings.append(
                Finding(
                    "duplicate_content_block",
                    Severity.MAJOR,
                    f"Identical content appears in blocks {ids}.",
                    "конспект.md",
                    block_id=ids[0],
                    evidence=tuple(str(value) for value in ids),
                )
            )
    return findings


def _slide_findings(artifacts: EvaluationArtifacts) -> list[Finding]:
    findings: list[Finding] = []
    slide_paths = {slide.path for slide in artifacts.slides if slide.path}
    referenced: list[int] = []
    for match in _SLIDE_LINK_RE.finditer(artifacts.note_markdown):
        path = match.group(1)
        normalized = path.removeprefix("./")
        if not any(
            member == normalized or member.endswith(f"/{normalized}")
            for member in artifacts.members
        ):
            findings.append(
                Finding(
                    "broken_markdown_reference",
                    Severity.CRITICAL,
                    f"Referenced file is absent from ZIP: {path}",
                    "конспект.md",
                    evidence=(path,),
                )
            )
        number_match = _SLIDE_NUM_RE.search(normalized)
        if number_match:
            referenced.append(int(number_match.group(1)))
    for number, count in Counter(referenced).items():
        if count > 1:
            findings.append(
                Finding(
                    "duplicate_slide_reference",
                    Severity.MAJOR,
                    f"Slide {number} is embedded {count} times.",
                    "конспект.md",
                    slide_num=number,
                )
            )
    if artifacts.pdf_page_count is not None and artifacts.pdf_page_count != len(slide_paths):
        findings.append(
            Finding(
                "pdf_exported_slide_count_mismatch",
                Severity.CRITICAL,
                f"PDF has {artifacts.pdf_page_count} pages, ZIP has "
                f"{len(slide_paths)} slide images.",
                "slides.pdf",
            )
        )
    structured = [number for section in artifacts.sections for number in section.slide_nums]
    for number in sorted(set(structured) - set(referenced)):
        findings.append(
            Finding(
                "structure_slide_missing_from_markdown",
                Severity.MAJOR,
                f"Structure references slide {number}, but Markdown does not.",
                "structure.json",
                slide_num=number,
            )
        )
    if artifacts.alignment:
        assignments_by_slide = {
            item.get("slide_num"): item
            for item in artifacts.alignment.assignments
            if isinstance(item.get("slide_num"), int)
        }
        assignment_nums = [
            item.get("slide_num")
            for item in artifacts.alignment.assignments
            if isinstance(item.get("slide_num"), int)
        ]
        placement_nums = [
            item.get("slide_num")
            for item in artifacts.alignment.placements
            if isinstance(item.get("slide_num"), int)
        ]
        for number in set(assignment_nums) - set(placement_nums):
            findings.append(
                Finding(
                    "assignment_without_placement",
                    Severity.CRITICAL,
                    f"Slide {number} has an assignment but no placement.",
                    "document-slide-alignment.json",
                    slide_num=number,
                )
            )
        discussed_nums = {
            number
            for number, assignment in assignments_by_slide.items()
            if assignment.get("match_status") == "discussed"
            and not _is_progressive_or_duplicate(assignment)
        }
        for number in sorted(discussed_nums - set(referenced)):
            findings.append(
                Finding(
                    "assignment_without_rendered_reference",
                    Severity.MAJOR,
                    f"Discussed slide {number} is not rendered in Markdown.",
                    "document-slide-alignment.json",
                    slide_num=number,
                )
            )
        for placement in artifacts.alignment.placements:
            section_id = placement.get("global_section_id")
            if section_id is not None and (
                not isinstance(section_id, int) or not 0 <= section_id < len(artifacts.sections)
            ):
                findings.append(
                    Finding(
                        "placement_section_out_of_range",
                        Severity.CRITICAL,
                        f"Slide {placement.get('slide_num')} placement references section "
                        f"{section_id}.",
                        "document-slide-alignment.json",
                        slide_num=placement.get("slide_num")
                        if isinstance(placement.get("slide_num"), int)
                        else None,
                    )
                )
            if (
                placement.get("anchor_confidence") == "verified"
                and placement.get("output_kind") != "inline"
            ):
                findings.append(
                    Finding(
                        "verified_non_inline_placement",
                        Severity.WARNING,
                        f"Slide {placement.get('slide_num')} is verified but not inline.",
                        "document-slide-alignment.json",
                        slide_num=placement.get("slide_num")
                        if isinstance(placement.get("slide_num"), int)
                        else None,
                    )
                )
        findings.extend(
            _alignment_anomaly_findings(
                artifacts,
                assignments_by_slide=assignments_by_slide,
                discussed_nums=discussed_nums,
            )
        )
    return findings


def _is_progressive_or_duplicate(item: dict[str, object]) -> bool:
    if item.get("match_status") == "duplicate":
        return True
    reason = str(item.get("reason_code") or item.get("fallback_reason") or "").casefold()
    return "progressive" in reason or "duplicate" in reason


def _alignment_anomaly_findings(
    artifacts: EvaluationArtifacts,
    *,
    assignments_by_slide: dict[int, dict[str, object]],
    discussed_nums: set[int],
) -> list[Finding]:
    alignment = artifacts.alignment
    if alignment is None:
        return []
    findings: list[Finding] = []
    collapse_groups: defaultdict[tuple[object, ...], set[int]] = defaultdict(set)
    for number in discussed_nums:
        assignment = assignments_by_slide[number]
        evidence_ids = assignment.get("evidence_block_ids")
        if isinstance(evidence_ids, list | tuple) and len(evidence_ids) == 1:
            collapse_groups[("evidence", evidence_ids[0])].add(number)
        anchor = assignment.get("anchor_s")
        if isinstance(anchor, int | float) and not isinstance(anchor, bool):
            collapse_groups[("anchor", round(float(anchor), 3))].add(number)
    section_counts: defaultdict[int, set[int]] = defaultdict(set)
    for placement in alignment.placements:
        number = placement.get("slide_num")
        if not isinstance(number, int) or number not in discussed_nums:
            continue
        if _is_progressive_or_duplicate(placement):
            continue
        section_id = placement.get("global_section_id")
        if not isinstance(section_id, int):
            continue
        if placement.get("output_kind") in {"inline", "section_gallery"}:
            section_counts[section_id].add(number)
        block_index = placement.get("block_index")
        if placement.get("output_kind") == "inline" and isinstance(block_index, int):
            collapse_groups[("placement", section_id, block_index)].add(number)
    emitted_slides: set[frozenset[int]] = set()
    for key, slide_nums in collapse_groups.items():
        frozen = frozenset(slide_nums)
        if len(slide_nums) < 4 or frozen in emitted_slides:
            continue
        emitted_slides.add(frozen)
        location = ":".join(str(part) for part in key)
        findings.append(
            Finding(
                "slide_anchor_collapse",
                Severity.MAJOR,
                f"Slides {sorted(slide_nums)} collapse onto one alignment anchor ({location}).",
                "document-slide-alignment.json",
                evidence=tuple(str(number) for number in sorted(slide_nums)),
            )
        )
    deck_size = max(
        len(artifacts.slides),
        len(assignments_by_slide),
        max(assignments_by_slide, default=0),
    )
    if deck_size >= 12:
        denominator = max(1, len(discussed_nums))
        for section_id, slide_nums in sorted(section_counts.items()):
            share = len(slide_nums) / denominator
            if len(slide_nums) < 6 or share < 0.3:
                continue
            severity = (
                Severity.MAJOR if len(slide_nums) >= 10 or share >= 0.5 else Severity.WARNING
            )
            findings.append(
                Finding(
                    "slide_section_concentration",
                    severity,
                    f"Section {section_id} contains {len(slide_nums)} of "
                    f"{denominator} discussed slide placements ({share:.0%}).",
                    "document-slide-alignment.json",
                    section_id=section_id,
                    evidence=tuple(str(number) for number in sorted(slide_nums)),
                )
            )
    return findings


def _timeline_findings(artifacts: EvaluationArtifacts) -> list[Finding]:
    findings: list[Finding] = []
    previous_end = -1.0
    for cue in artifacts.transcript:
        if cue.end_s < cue.start_s or cue.start_s < previous_end:
            findings.append(
                Finding(
                    "invalid_transcript_timeline",
                    Severity.MAJOR,
                    f"Transcript cue {cue.block_id} overlaps or has invalid timestamps.",
                    "transcript.srt",
                    evidence=(f"{cue.start_s:.3f}-{cue.end_s:.3f}",),
                )
            )
        previous_end = max(previous_end, cue.end_s)
    for section in artifacts.sections:
        if (
            section.start_s is not None
            and section.end_s is not None
            and section.end_s < section.start_s
        ):
            findings.append(
                Finding(
                    "invalid_section_timeline",
                    Severity.CRITICAL,
                    f"Section {section.section_id} ends before it starts.",
                    "structure.json",
                    section_id=section.section_id,
                )
            )
    return findings


def run_deterministic_checks(artifacts: EvaluationArtifacts) -> tuple[Finding, ...]:
    findings = list(artifacts.load_findings)
    findings.extend(_fence_findings(artifacts))
    findings.extend(_markdown_findings(artifacts))
    findings.extend(_slide_findings(artifacts))
    findings.extend(_timeline_findings(artifacts))
    findings.extend(language_findings(artifacts.blocks))
    return tuple(findings)
