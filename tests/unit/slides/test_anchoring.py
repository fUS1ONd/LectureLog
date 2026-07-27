from lecturelog.domain.slides import SlideAssignment, SlideCatalogEntry
from lecturelog.infrastructure.slides.alignment import anchoring


def test_marker_injection_failure_falls_back_to_section_gallery(monkeypatch) -> None:
    assignment = SlideAssignment(1, "discussed", 0, (1,), 1.0, "verified", 1.0, "matched")
    entry = SlideCatalogEntry(1, "content", "Дерево", "Бинарное дерево")

    def fail(*_args, **_kwargs):
        raise ValueError("broken markdown")

    monkeypatch.setattr(anchoring, "inject_marker", fail)
    markdown, placement = anchoring.anchor_assignment(
        assignment, entry, "Обсуждаем бинарное дерево."
    )

    assert markdown == "Обсуждаем бинарное дерево."
    assert placement.output_kind == "section_gallery"
    assert placement.fallback_reason == "anchor_injection_failed"


def test_generic_single_token_does_not_create_inline_anchor() -> None:
    assignment = SlideAssignment(1, "discussed", 0, (1,), 1.0, "verified", 10.0, "test")
    entry = SlideCatalogEntry(
        1,
        "content",
        "Спиральная модель",
        "Управление рисками на каждом витке",
    )

    _, placement = anchoring.anchor_assignment(
        assignment,
        entry,
        "## Раздел\n\nЭта модель применяется в проекте.",
    )

    assert placement.output_kind == "section_gallery"
    assert placement.fallback_reason == "no_safe_semantic_block"


def test_sequential_anchors_use_content_block_indices() -> None:
    entry = SlideCatalogEntry(1, "content", None, "бинарное дерево")
    first = SlideAssignment(1, "discussed", 0, (1,), 1.0, "verified", 1.0, "matched")
    second = SlideAssignment(2, "discussed", 0, (1,), 1.0, "verified", 1.0, "matched")

    markdown, first_placement = anchoring.anchor_assignment(
        first, entry, "Введение.\n\nОбсуждаем бинарное дерево."
    )
    markdown, second_placement = anchoring.anchor_assignment(second, entry, markdown)

    assert first_placement.block_index == 1
    assert second_placement.block_index == 1
    assert markdown.count("<!-- slide:1 -->") == 1
    assert markdown.count("<!-- slide:2 -->") == 1
