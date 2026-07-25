from __future__ import annotations

import base64
import json
import zipfile

import fitz
import pytest

from lecturelog.evaluation.artifacts import (
    ArtifactLoadError,
    load_evaluation_artifacts,
    parse_markdown,
    parse_srt,
)


def _write_result(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_loads_and_cross_links_result_artifacts_without_extracting(tmp_path):
    result = tmp_path / "result.zip"
    structure = {
        "sections": [
            {
                "title": "Тема",
                "subtopics": [
                    {
                        "title": "Раздел",
                        "media": {"start": "0:00", "end": "1:30"},
                        "slide_nums": [1],
                        "content_md": "Русский текст раздела достаточно длинный для анализа.",
                    }
                ],
            }
        ]
    }
    diagnostic = {
        "schema_version": 1,
        "mode": "active",
        "assignments": [{"slide_num": 1}],
        "placements": [{"slide_num": 1, "global_section_id": 0}],
    }
    _write_result(
        result,
        {
            "output/конспект.md": (
                "# Тема\n\n## Раздел\n\nРусский текст раздела достаточно длинный для анализа.\n\n"
                "![Слайд 1](slides/slide-01.png)\n"
            ),
            "output/structure.json": json.dumps(structure, ensure_ascii=False),
            "output/transcript.srt": (
                "17\n00:00:01,000 --> 00:00:03,500\nНачало лекции\n"
            ),
            "output/document-slide-alignment.json": json.dumps(diagnostic),
            "output/slides/slide-01.png": b"png",
        },
    )
    pdf = tmp_path / "slides.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Native slide text that is definitely long enough to be good")
    document.save(pdf)
    document.close()

    artifacts = load_evaluation_artifacts(result, pdf)

    assert artifacts.note_markdown.startswith("# Тема")
    assert artifacts.sections[0].start_s == 0
    assert artifacts.sections[0].end_s == 90
    assert artifacts.transcript[0].block_id == 17
    assert artifacts.transcript[0].end_s == 3.5
    assert artifacts.slides[0].native_text_quality == "good"
    assert artifacts.slides[0].image_data_url is None
    assert artifacts.slides[0].path == "output/slides/slide-01.png"
    assert artifacts.alignment is not None
    assert artifacts.alignment.mode == "active"
    assert any(block.section_id == 0 for block in artifacts.blocks if block.kind == "paragraph")


def test_loads_bounded_slide_image_only_when_native_text_is_sparse(tmp_path):
    result = tmp_path / "result.zip"
    image = b"\x89PNG\r\n\x1a\nsmall"
    _write_result(
        result,
        {
            "конспект.md": "# Note",
            "structure.json": json.dumps({"sections": []}),
            "slides/slide-01.png": image,
        },
    )

    artifacts = load_evaluation_artifacts(result)

    assert artifacts.slides[0].native_text_quality == "none"
    assert artifacts.slides[0].image_data_url == (
        "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    )


def test_does_not_load_oversized_slide_image_into_memory_packet(tmp_path, monkeypatch):
    from lecturelog.evaluation import artifacts as artifact_module

    monkeypatch.setattr(artifact_module, "_MAX_SLIDE_IMAGE_BYTES", 4)
    result = tmp_path / "result.zip"
    _write_result(
        result,
        {
            "конспект.md": "# Note",
            "structure.json": json.dumps({"sections": []}),
            "slides/slide-01.png": b"12345",
        },
    )

    artifacts = load_evaluation_artifacts(result)

    assert artifacts.slides[0].image_data_url is None


@pytest.mark.parametrize("unsafe_name", ["../secret", "/absolute", "output\\evil", "a/../evil"])
def test_rejects_unsafe_zip_member_paths(tmp_path, unsafe_name):
    result = tmp_path / "result.zip"
    _write_result(result, {unsafe_name: "secret"})

    with pytest.raises(ArtifactLoadError, match="Unsafe ZIP member"):
        load_evaluation_artifacts(result)


def test_missing_and_invalid_optional_artifacts_become_findings(tmp_path):
    result = tmp_path / "result.zip"
    _write_result(
        result,
        {
            "конспект.md": "# Только конспект",
            "structure.json": "{not json",
        },
    )

    artifacts = load_evaluation_artifacts(result)

    assert {finding.code for finding in artifacts.load_findings} == {
        "invalid_structure_json",
        "missing_transcript",
    }
    assert artifacts.sections == ()
    assert artifacts.transcript == ()


def test_markdown_parser_keeps_fenced_and_list_blocks_atomic():
    blocks = parse_markdown(
        "# Заголовок\n\n```python\nprint('x')\n\nprint('y')\n```\n\n"
        "- первый элемент\n- второй элемент\n\nОбычный абзац."
    )

    assert [block.kind for block in blocks] == ["heading", "code", "list", "paragraph"]
    assert "print('y')" in blocks[1].text
    assert blocks[2].line_end > blocks[2].line_start


def test_srt_parser_accepts_dot_milliseconds_and_skips_malformed_cues():
    cues = parse_srt(
        "1\n00:00:01.250 --> 00:00:02.500\nValid\n\n"
        "2\nnot a timestamp\nInvalid\n\n"
        "00:00:03,000 --> 00:00:04,000\nNo numeric source id"
    )

    assert [(cue.block_id, cue.start_s, cue.text) for cue in cues] == [
        (1, 1.25, "Valid"),
        (2, 3.0, "No numeric source id"),
    ]
