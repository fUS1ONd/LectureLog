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


def test_probable_with_exact_phrase_is_anchored_inline() -> None:
    """Дословное вхождение фразы слайда — достаточное основание, verified тут не нужен."""
    assignment = SlideAssignment(1, "discussed", 0, (1,), 1.0, "probable", 10.0, "matched")
    entry = SlideCatalogEntry(1, "content", "Кризис ПО", "кризис программного обеспечения")

    markdown, placement = anchoring.anchor_assignment(
        assignment,
        entry,
        "## Раздел\n\nВ шестидесятые начался кризис программного обеспечения.",
    )

    assert placement.output_kind == "inline"
    assert placement.anchor_confidence == "probable"
    assert markdown.count("<!-- slide:1 -->") == 1


def test_probable_with_distinctive_token_is_anchored_inline() -> None:
    """Редкий токен (аббревиатура) различает слайд не хуже целой фразы."""
    assignment = SlideAssignment(1, "discussed", 0, (1,), 1.0, "probable", 10.0, "matched")
    entry = SlideCatalogEntry(
        1,
        "content",
        "SWEBOK",
        "свод знаний",
        transcript_language_terms=("SWEBOK",),
    )

    _, placement = anchoring.anchor_assignment(
        assignment,
        entry,
        "## Раздел\n\nЕсть документ SWEBOK, он описывает области знаний.",
    )

    assert placement.output_kind == "inline"


def test_probable_with_weak_overlap_goes_to_gallery() -> None:
    """Грубого пересечения слов мало: слайд рядом со случайно похожим абзацем хуже галереи."""
    assignment = SlideAssignment(1, "discussed", 0, (1,), 1.0, "probable", 10.0, "matched")
    entry = SlideCatalogEntry(
        1,
        "content",
        "Спиральная модель",
        "управление рисками на каждом витке",
    )

    # Блок проходит grounding по двум общим словам, но целой фразы слайда в нём
    # нет и редких токенов тоже — то есть совпадение может быть случайным.
    _, placement = anchoring.anchor_assignment(
        assignment,
        entry,
        "## Раздел\n\nЗдесь важно управление рисками, а про витке речи не было.",
    )

    assert placement.output_kind == "section_gallery"
    assert placement.fallback_reason == "weak_evidence_only"


def test_verified_keeps_inline_on_weak_evidence() -> None:
    """Защита от регресса: verified-назначения раньше проходили с любым найденным блоком."""
    assignment = SlideAssignment(1, "discussed", 0, (1,), 1.0, "verified", 10.0, "matched")
    entry = SlideCatalogEntry(1, "content", None, "бинарное дерево поиска")

    _, placement = anchoring.anchor_assignment(
        assignment,
        entry,
        "## Раздел\n\nСтроим дерево поиска для задачи.",
    )

    assert placement.output_kind == "inline"
    assert placement.anchor_confidence == "verified"


def test_gallery_is_placed_after_section_content() -> None:
    """Галерея в начале раздела опережала свой материал и вклинивалась перед чужим текстом."""
    assignment = SlideAssignment(1, "discussed", 0, (1,), 1.0, "probable", 10.0, "matched")
    entry = SlideCatalogEntry(1, "content", "Тема", "совершенно посторонний текст")

    _, placement = anchoring.anchor_assignment(assignment, entry, "## Раздел\n\nДругая тема.")

    assert placement.output_kind == "section_gallery"
    assert placement.gallery_position == "after_content"
