import pytest

from lecturelog.infrastructure.slides.alignment.markers import inject_marker, parse_markdown_blocks


def test_marker_is_inserted_only_between_atomic_blocks() -> None:
    markdown = "Абзац один.\n\n```python\nprint('x')\n```\n\nАбзац два."
    blocks = parse_markdown_blocks(markdown)
    assert blocks[1].atomic
    result = inject_marker(markdown, slide_num=3, block_index=0, side="after")
    assert result.count("<!-- slide:3 -->") == 1
    assert "```python\nprint('x')\n```" in result


def test_marker_rejects_invalid_anchor() -> None:
    with pytest.raises(ValueError):
        inject_marker("text", slide_num=1, block_index=4, side="after")


def test_multiple_markers_are_preserved_across_sequential_injections() -> None:
    markdown = "Первый абзац.\n\nВторой абзац."

    with_first = inject_marker(markdown, slide_num=1, block_index=0, side="after")
    result = inject_marker(with_first, slide_num=2, block_index=0, side="after")

    assert result.count("<!-- slide:1 -->") == 1
    assert result.count("<!-- slide:2 -->") == 1
    assert result.index("<!-- slide:1 -->") < result.index("<!-- slide:2 -->")
    assert result.endswith("Второй абзац.")


def test_repeated_injection_of_same_marker_is_idempotent() -> None:
    once = inject_marker("Абзац.", slide_num=1, block_index=0, side="after")

    assert inject_marker(once, slide_num=1, block_index=0, side="after") == once
