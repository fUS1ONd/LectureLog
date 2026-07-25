from pathlib import Path

from lecturelog.evaluation.artifacts import parse_markdown
from lecturelog.evaluation.deterministic import run_deterministic_checks
from lecturelog.evaluation.models import (
    AlignmentData,
    EvaluationArtifacts,
    SectionArtifact,
    SlideArtifact,
    TranscriptCue,
)


def _artifacts(markdown, **changes):
    base = {
        "source_zip": Path("result.zip"),
        "note_markdown": markdown,
        "structure": {"sections": []},
        "blocks": parse_markdown(markdown),
        "sections": (),
        "transcript": (),
        "slides": (),
        "alignment": None,
        "members": ("output/конспект.md", "output/structure.json"),
    }
    base.update(changes)
    return EvaluationArtifacts(**base)


def test_reports_broken_reference_duplicate_slide_and_pdf_count():
    markdown = (
        "![Слайд](slides/slide-01.png)\n\n"
        "![Слайд снова](slides/slide-01.png)\n\n"
        "![Потерянный](slides/slide-02.png)"
    )
    artifacts = _artifacts(
        markdown,
        slides=(SlideArtifact(1, "output/slides/slide-01.png"),),
        members=(
            "output/конспект.md",
            "output/structure.json",
            "output/slides/slide-01.png",
        ),
        pdf_page_count=2,
    )

    codes = {finding.code for finding in run_deterministic_checks(artifacts)}

    assert "broken_markdown_reference" in codes
    assert "duplicate_slide_reference" in codes
    assert "pdf_exported_slide_count_mismatch" in codes


def test_reports_structure_alignment_and_timeline_invariants():
    section = SectionArtifact(0, "Тема", "Раздел", "text", 20, 10, (1,))
    alignment = AlignmentData(
        1,
        "active",
        ({"slide_num": 1},),
        (
            {
                "slide_num": 2,
                "global_section_id": 9,
                "output_kind": "section_gallery",
                "anchor_confidence": "verified",
            },
        ),
    )
    artifacts = _artifacts(
        "# Тема\n\n## Раздел\n\nСодержательный русский текст раздела для проверки.",
        sections=(section,),
        transcript=(TranscriptCue(1, 5, 3, "ошибка"),),
        alignment=alignment,
    )

    codes = {finding.code for finding in run_deterministic_checks(artifacts)}

    assert {
        "structure_slide_missing_from_markdown",
        "assignment_without_placement",
        "placement_section_out_of_range",
        "verified_non_inline_placement",
        "invalid_transcript_timeline",
        "invalid_section_timeline",
    } <= codes


def test_reports_unclosed_fence_empty_heading_and_duplicate_content():
    repeated = (
        "Это очень длинный повторяющийся блок с одинаковым содержанием, который должен быть "
        "обнаружен статической проверкой без использования языковой модели."
    )
    markdown = (
        f"# Тема\n\n## Пустой\n\n## Следующий\n\n{repeated}\n\n{repeated}\n\n```python\nx = 1"
    )

    codes = {finding.code for finding in run_deterministic_checks(_artifacts(markdown))}

    assert "empty_heading" in codes
    assert "duplicate_content_block" in codes
    assert "unclosed_markdown_fence" in codes


def test_reports_anchor_collapse_and_missing_discussed_render():
    assignments = tuple(
        {
            "slide_num": number,
            "match_status": "discussed",
            "evidence_block_ids": [77],
            "anchor_s": 42.0,
            "reason_code": "semantic_match",
        }
        for number in range(1, 6)
    )
    placements = tuple(
        {
            "slide_num": number,
            "global_section_id": 0,
            "output_kind": "inline",
            "block_index": 3,
        }
        for number in range(1, 6)
    )
    markdown = "\n\n".join(
        f"![Слайд {number}](slides/slide-{number:02d}.png)" for number in range(1, 5)
    )
    artifacts = _artifacts(
        markdown,
        alignment=AlignmentData(1, "active", assignments, placements),
        members=tuple(
            ["output/конспект.md", "output/structure.json"]
            + [f"output/slides/slide-{number:02d}.png" for number in range(1, 6)]
        ),
    )

    findings = run_deterministic_checks(artifacts)
    codes = [finding.code for finding in findings]

    assert codes.count("slide_anchor_collapse") == 1
    missing = next(
        finding for finding in findings if finding.code == "assignment_without_rendered_reference"
    )
    assert missing.slide_num == 5


def test_reports_large_section_concentration_for_real_deck_size():
    assignments = tuple(
        {
            "slide_num": number,
            "match_status": "discussed",
            "evidence_block_ids": [number],
            "anchor_s": float(number),
        }
        for number in range(1, 13)
    )
    placements = tuple(
        {
            "slide_num": number,
            "global_section_id": 0 if number <= 6 else 1,
            "output_kind": "section_gallery",
        }
        for number in range(1, 13)
    )
    markdown = "\n\n".join(
        f"![Слайд {number}](slides/slide-{number:02d}.png)" for number in range(1, 13)
    )
    members = tuple(
        ["output/конспект.md", "output/structure.json"]
        + [f"output/slides/slide-{number:02d}.png" for number in range(1, 13)]
    )

    findings = run_deterministic_checks(
        _artifacts(
            markdown,
            alignment=AlignmentData(1, "active", assignments, placements),
            members=members,
        )
    )
    concentrated = [
        finding for finding in findings if finding.code == "slide_section_concentration"
    ]

    assert len(concentrated) == 2
    assert all(finding.severity == "major" for finding in concentrated)


def test_progressive_and_duplicate_slides_do_not_create_false_collapse():
    assignments = (
        {
            "slide_num": 1,
            "match_status": "discussed",
            "evidence_block_ids": [9],
            "anchor_s": 10.0,
        },
        *(
            {
                "slide_num": number,
                "match_status": "duplicate",
                "evidence_block_ids": [9],
                "anchor_s": 10.0,
                "reason_code": "progressive_build",
            }
            for number in range(2, 7)
        ),
    )
    placements = tuple(
        {
            "slide_num": number,
            "global_section_id": 0,
            "output_kind": "inline",
            "block_index": 1,
        }
        for number in range(1, 7)
    )

    codes = {
        finding.code
        for finding in run_deterministic_checks(
            _artifacts(
                "![Слайд 1](slides/slide-01.png)",
                alignment=AlignmentData(1, "active", assignments, placements),
                members=(
                    "output/конспект.md",
                    "output/structure.json",
                    "output/slides/slide-01.png",
                ),
            )
        )
    }

    assert "slide_anchor_collapse" not in codes
    assert "assignment_without_rendered_reference" not in codes
