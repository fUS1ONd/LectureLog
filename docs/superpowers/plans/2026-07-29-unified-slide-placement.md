# Единое размещение слайдов: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Кадры из видеоряда проходят через тот же evidence-конвейер, что документные слайды, поэтому улучшения матчинга действуют на оба режима.

**Architecture:** Единственное различие режимов — временнóе окно слайда, выводимое из `SlideAsset.timestamp`. Кадр каталогизируется тем же `document_slide_catalog_v3` (каталогизатор работает по изображениям), затем идёт общим путём: retrieval внутри окна → семантическая проверка → уровни доверия → sequence → anchoring → маркеры. При любом сбое кадр встаёт по времени внутри своей секции, то есть как сегодня.

**Tech Stack:** Python 3.12, pytest, pydantic, OpenRouter (Gemini), ffmpeg.

## Global Constraints

- Схема API и формат вывода не меняются: `structure.json` строится из `section.slide_indices` и маркеров `<!-- slide:N -->` в `content_md` (`export/structure.py:60-90`).
- Худший случай для видео равен текущему поведению: сбой каталогизации, отсутствие доказательств или исключение сервиса → кадр размещается по времени внутри своей секции.
- Документный режим не должен менять поведение нигде, кроме трёх решений из `OPEN-QUESTIONS.md` (задачи 1–3).
- Комментарии и докстринги — на русском языке.
- Тесты запускаются `.venv/bin/python -m pytest`; линтер `.venv/bin/python -m ruff check lecturelog tests`.
- Известное локальное падение `tests/unit/test_settings_llm.py::test_llm_config_effort_per_stage_defaults` вызвано локальным `.env` с `LLM_EFFORT_SPLIT=low` и к работе не относится.
- Каждая задача завершается коммитом.

---

### Task 1: Deck guard отдаёт пустой каталог

Решение пользователя: если колода признана посторонней, её написания не должны попадать в текст конспекта вообще.

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py:132-150`
- Test: `tests/unit/slides/test_alignment_service.py`

**Interfaces:**
- Consumes: `AlignmentResult(assignments, catalog)` из `alignment/service.py:51-60`.
- Produces: при сработавшем deck guard `AlignmentResult.catalog` — пустой словарь. Потребитель — `_slide_context_block` в `gemini_structurizer.py`, который строит справочник написаний.

- [ ] **Step 1: Написать падающий тест**

```python
@pytest.mark.asyncio
async def test_deck_guard_returns_empty_catalog(tmp_path):
    """Посторонняя колода не должна снабжать рендер написаниями своих имён.

    Иначе имена с чужих слайдов подставляются в конспект и выглядят как
    уверенное исправление опечатки распознавания речи.
    """
    service = DocumentAlignmentService()
    assets = [
        SlideAsset(
            slide_num=1,
            path=_png(tmp_path, "slide-01.png"),
            origin="document",
            extracted_text="Совершенно посторонний текст про кулинарию",
            native_text_quality="good",
        )
    ]
    result = await service.align(
        assets=assets,
        section_layout=[[{"title": "Раздел", "start": "00:00:00", "end": "00:05:00"}]],
        srt_content="1\n00:00:00,000 --> 00:00:05,000\nречь совсем о другом\n",
    )

    assert all(item.match_status == "deck_mismatch" for item in result.assignments)
    assert result.catalog == {}
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest tests/unit/slides/test_alignment_service.py::test_deck_guard_returns_empty_catalog -v`
Expected: FAIL — `result.catalog` содержит запись слайда 1.

- [ ] **Step 3: Реализовать**

В `service.py` в ветке deck guard заменить второй аргумент `AlignmentResult` на пустой словарь:

```python
        if content_count and supported < required:
            # Колода признана посторонней: её написания не должны попадать в
            # справочник рендера, иначе чужие имена собственные подставляются
            # в конспект как исправление опечатки ASR.
            return AlignmentResult(
                tuple(
                    item
                    if item.match_status == "duplicate"
                    else SlideAssignment(
                        item.slide_num,
                        "deck_mismatch",
                        None,
                        (),
                        None,
                        "unresolved",
                        item.score,
                        "deck_guard_insufficient_grounded_coverage",
                    )
                    for item in assignments
                ),
                {},
            )
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: новый тест PASS, остальные без новых падений.

- [ ] **Step 5: Убрать запись из OPEN-QUESTIONS.md**

Удалить из `OPEN-QUESTIONS.md` пункт про `DocumentAlignmentService.align, ветка deck guard`.

- [ ] **Step 6: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py tests/unit/slides/test_alignment_service.py OPEN-QUESTIONS.md
git commit -m "fix(slides): посторонняя колода не даёт написаний рендеру"
```

---

### Task 2: Пустая запись каталога трактуется как пустая страница

Решение пользователя: верить модели. Сейчас `selected = entry or fallback.entry` уже предпочитает ответ модели (датакласс всегда истинен), но пустая запись остаётся в роли `content` и висит непривязываемой. Делаем это явным: роль `blank`.

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py:198-203`
- Test: `tests/unit/slides/test_alignment_service.py`

**Interfaces:**
- Consumes: `SlideCatalogEntry` (`domain/slides.py:41-50`), роль `blank` уже входит в `Literal`.
- Produces: запись с пустыми `title` и `visible_text` получает `role="blank"`; `_NON_MATCHABLE_ROLES` уводит такой слайд в приложение без попытки матчинга.

- [ ] **Step 1: Написать падающий тест**

```python
def test_empty_model_entry_becomes_blank_role():
    """Пустая запись модели — утверждение «на странице ничего нет».

    Раньше такая страница оставалась content и не могла быть привязана ничем:
    матчинг шёл по пустому payload и молча не находил ничего.
    """
    entry = SlideCatalogEntry(slide_num=3, role="content", title=None, visible_text="   ")

    assert normalize_empty_entry(entry).role == "blank"


def test_non_empty_model_entry_keeps_role():
    entry = SlideCatalogEntry(slide_num=4, role="content", title="Бэклог", visible_text="пункты")

    assert normalize_empty_entry(entry).role == "content"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest tests/unit/slides/test_alignment_service.py -k normalize_empty -v`
Expected: FAIL — `normalize_empty_entry` не существует.

- [ ] **Step 3: Реализовать**

В `service.py` рядом с `_catalog` добавить функцию модуля и применить её к выбранной записи:

```python
def normalize_empty_entry(entry: SlideCatalogEntry) -> SlideCatalogEntry:
    """Пустую запись модели считаем утверждением «на странице ничего нет».

    Роль blank уводит слайд в приложение сразу, вместо того чтобы держать его
    в content и безрезультатно искать доказательства по пустому payload.
    """
    if entry.title or entry.visible_text.strip():
        return entry
    return replace(entry, role="blank")
```

В цикле выбора записи:

```python
            for asset, entry in zip(batch, parsed or [None] * len(batch), strict=True):
                fallback = native_text_fallback(asset, boilerplate=boilerplate)
                selected = entry or fallback.entry
                if selected is not None:
                    result[asset.slide_num] = normalize_empty_entry(selected)
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: новые тесты PASS, прочие без новых падений.

- [ ] **Step 5: Убрать запись из OPEN-QUESTIONS.md**

Удалить пункт про `DocumentAlignmentService._catalog, выбор между ответом модели и нативным текстом`.

- [ ] **Step 6: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py tests/unit/slides/test_alignment_service.py OPEN-QUESTIONS.md
git commit -m "feat(slides): пустая запись каталога получает роль blank"
```

---

### Task 3: Пара слайдов на одном доказательстве допускается только для связанных

Решение пользователя: два слайда на одном блоке остаются `verified` только если связаны как `progressive_build` или `exact_duplicate`; иначе понижаются.

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py:366-396`
- Test: `tests/unit/slides/test_alignment_service.py`

**Interfaces:**
- Consumes: `SlideRelation.kind` — `Literal["exact_duplicate", "progressive_build"]` (`domain/slides.py:69`).
- Produces: `_downgrade_evidence_collisions` понижает начиная с двух несвязанных слайдов на одном `evidence_block_id`.

- [ ] **Step 1: Написать падающий тест**

```python
def test_two_unrelated_slides_on_one_block_are_downgraded():
    """Два несвязанных слайда на одной реплике: минимум один из них не про неё."""
    assignments = (
        _assignment(slide_num=5, block_ids=(120,), confidence="verified"),
        _assignment(slide_num=9, block_ids=(120,), confidence="verified"),
    )

    result = DocumentAlignmentService._downgrade_evidence_collisions(assignments, ())

    assert [item.assignment_confidence for item in result] == ["probable", "probable"]


def test_two_related_slides_on_one_block_keep_verified():
    """Progressive build — законная пара: одна и та же страница в двух состояниях."""
    assignments = (
        _assignment(slide_num=5, block_ids=(120,), confidence="verified"),
        _assignment(slide_num=6, block_ids=(120,), confidence="verified"),
    )
    relations = (SlideRelation(slide_num=6, canonical_slide_num=5, kind="progressive_build"),)

    result = DocumentAlignmentService._downgrade_evidence_collisions(assignments, relations)

    assert [item.assignment_confidence for item in result] == ["verified", "verified"]


def test_exact_duplicates_on_one_block_keep_verified():
    """Дубль страницы — тоже законная пара."""
    assignments = (
        _assignment(slide_num=2, block_ids=(77,), confidence="verified"),
        _assignment(slide_num=8, block_ids=(77,), confidence="verified"),
    )
    relations = (SlideRelation(slide_num=8, canonical_slide_num=2, kind="exact_duplicate"),)

    result = DocumentAlignmentService._downgrade_evidence_collisions(assignments, relations)

    assert [item.assignment_confidence for item in result] == ["verified", "verified"]
```

Хелпер `_assignment` добавить в тот же файл, если его там нет:

```python
def _assignment(*, slide_num: int, block_ids: tuple[int, ...], confidence: str) -> SlideAssignment:
    return SlideAssignment(
        slide_num, "discussed", 1, block_ids, 10.0, confidence, 12.0, "semantic_strong"
    )
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/unit/slides/test_alignment_service.py -k collision -v`
Expected: FAIL на первом тесте — при пороге `> 2` пара не понижается.

- [ ] **Step 3: Реализовать**

В `_downgrade_evidence_collisions` включить в множество связанных оба вида связей и понижать начиная с двух:

```python
    @staticmethod
    def _downgrade_evidence_collisions(assignments, relations):
        # Пара слайдов на одном доказательстве законна только если это одна и
        # та же страница в двух видах: progressive build или дубль. Любая другая
        # пара означает, что как минимум один слайд не про эту реплику.
        related = {
            slide_num
            for relation in relations
            for slide_num in (relation.slide_num, relation.canonical_slide_num)
        }
        by_evidence: dict[int, list[int]] = {}
        for item in assignments:
            if item.match_status != "discussed":
                continue
            for block_id in item.evidence_block_ids:
                if item.slide_num not in related:
                    by_evidence.setdefault(block_id, []).append(item.slide_num)
        conflicted = {
            slide_num
            for slide_nums in by_evidence.values()
            if len(slide_nums) > 1
            for slide_num in slide_nums
        }
        return tuple(
            replace(
                item,
                assignment_confidence="probable",
                reason_code=f"{item.reason_code}:evidence_collision",
            )
            if item.slide_num in conflicted and item.assignment_confidence == "verified"
            else item
            for item in assignments
        )
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: новые тесты PASS. Если падают существующие тесты коллизий — они фиксировали старый порог, обновить их ожидания и указать это в сообщении коммита.

- [ ] **Step 5: Убрать запись из OPEN-QUESTIONS.md**

Удалить пункт про `_downgrade_evidence_collisions, порог len(slide_nums) > 2`. Файл должен остаться пустым по списку — оставить заголовок и пояснение о назначении файла.

- [ ] **Step 6: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py tests/unit/slides/test_alignment_service.py OPEN-QUESTIONS.md
git commit -m "fix(slides): понижать пару несвязанных слайдов на одном доказательстве"
```

---

### Task 4: Характеризационные тесты текущего размещения кадров

Фиксируем нынешнее поведение видео-режима до любых изменений. Без этого «не ухудшили ли» проверять нечем.

**Files:**
- Create: `tests/unit/frames/test_placement_characterization.py`
- Test: тот же файл

**Interfaces:**
- Consumes: `place_slides_in_sections(items: list[SlideImage], topics: list[Topic]) -> None` (`frames/placement.py:48`), `bind_frames_to_sections` (`frames/binding.py`).
- Produces: набор эталонных ожиданий, на который опираются задачи 5 и 8.

- [ ] **Step 1: Написать тесты, фиксирующие текущее поведение**

```python
"""Эталон текущего размещения кадров.

Эти тесты намеренно описывают поведение «как есть» на момент слияния режимов:
позиция кадра внутри секции считается пропорцией длины абзацев в символах.
Задачи слияния не должны менять результат там, где доказательств нет.
"""

from lecturelog.domain.models import Section, Topic
from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.frames.placement import place_slides_in_sections


def _topic(content: str, *, start: str, end: str, slides: list[int]) -> Topic:
    section = Section(
        title="Раздел", start=start, end=end, content=content, slide_indices=slides
    )
    return Topic(title="Тема", start=start, end=end, sections=[section])


def test_marker_lands_after_paragraph_matching_timestamp_share():
    """Три равных абзаца, кадр в середине секции — маркер после второго абзаца."""
    content = "первый абзац\n\nвторой абзац\n\nтретий абзац"
    topics = [_topic(content, start="00:00:00", end="00:03:00", slides=[1])]
    frames = [SlideImage(path=None, timestamp=90.0)]

    place_slides_in_sections(frames, topics)

    assert topics[0].sections[0].content == (
        "первый абзац\n\nвторой абзац\n\n<!-- slide:1 -->\n\nтретий абзац"
    )


def test_timestamp_past_section_end_lands_after_last_paragraph():
    """Кадр со временем за концом секции прижимается к последнему абзацу."""
    content = "первый абзац\n\nвторой абзац"
    topics = [_topic(content, start="00:00:00", end="00:01:00", slides=[1])]
    frames = [SlideImage(path=None, timestamp=600.0)]

    place_slides_in_sections(frames, topics)

    assert topics[0].sections[0].content.endswith("второй абзац\n\n<!-- slide:1 -->")


def test_two_frames_keep_order_inside_section():
    """Два кадра в одной секции сохраняют порядок по времени."""
    content = "а\n\nб\n\nв\n\nг"
    topics = [_topic(content, start="00:00:00", end="00:04:00", slides=[1, 2])]
    frames = [SlideImage(path=None, timestamp=30.0), SlideImage(path=None, timestamp=210.0)]

    place_slides_in_sections(frames, topics)

    content_after = topics[0].sections[0].content
    assert content_after.index("<!-- slide:1 -->") < content_after.index("<!-- slide:2 -->")
```

Если конструктор `SlideImage` требует иных полей — посмотреть его определение в `lecturelog/domain/ports.py` и передать минимально необходимые, сохранив `timestamp`.

- [ ] **Step 2: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit/frames/test_placement_characterization.py -v`
Expected: все PASS. Если какой-то падает — значит эталон записан неверно: исправить ожидание под фактическое поведение, а не менять код.

- [ ] **Step 3: Коммит**

```bash
git add tests/unit/frames/test_placement_characterization.py
git commit -m "test(frames): зафиксировать текущее размещение кадров как эталон"
```

---

### Task 5: Одна реализация сегментации и вставки маркеров

`frames/placement.py` оставляет только расчёт позиции по времени; сегментация абзацев и вставка маркера уходят в `alignment/markers.py`.

**Files:**
- Modify: `lecturelog/infrastructure/frames/placement.py`
- Modify: `lecturelog/infrastructure/slides/alignment/markers.py`
- Test: `tests/unit/frames/test_placement_characterization.py` (не менять ожидания), `tests/unit/slides/test_markers.py`

**Interfaces:**
- Consumes: `parse_markdown_blocks(markdown) -> tuple[MarkdownBlock, ...]` и `inject_marker(markdown, *, slide_num, block_index, side) -> str` (`markers.py:15,44`).
- Produces: `paragraph_index_for_time(markdown: str, *, section_start_s: float, section_end_s: float, timestamp_s: float) -> int` в `frames/placement.py` — индекс блока, после которого встаёт маркер; `-1`, если блоков нет.

- [ ] **Step 1: Написать падающий тест на новую функцию**

```python
def test_paragraph_index_for_time_matches_char_share():
    """Позиция по времени считается пропорцией длины абзацев в символах."""
    markdown = "первый абзац\n\nвторой абзац\n\nтретий абзац"

    index = paragraph_index_for_time(
        markdown, section_start_s=0.0, section_end_s=180.0, timestamp_s=90.0
    )

    assert index == 1


def test_paragraph_index_for_time_past_end_returns_last():
    markdown = "первый абзац\n\nвторой абзац"

    index = paragraph_index_for_time(
        markdown, section_start_s=0.0, section_end_s=60.0, timestamp_s=600.0
    )

    assert index == 1


def test_paragraph_index_for_time_without_blocks_returns_minus_one():
    index = paragraph_index_for_time(
        "", section_start_s=0.0, section_end_s=60.0, timestamp_s=30.0
    )

    assert index == -1
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/unit/frames/test_placement.py -k paragraph_index -v`
Expected: FAIL — функции нет.

- [ ] **Step 3: Реализовать `paragraph_index_for_time` и переписать `place_slides_in_sections` через общие примитивы**

```python
def paragraph_index_for_time(
    markdown: str,
    *,
    section_start_s: float,
    section_end_s: float,
    timestamp_s: float,
) -> int:
    """Индекс блока, после которого встаёт маркер кадра.

    Интервал секции распределяется по блокам пропорционально их длине в
    символах: время блока считается пропорциональным доле его текста.
    """
    blocks = parse_markdown_blocks(markdown)
    if not blocks:
        return -1
    total_chars = sum(len(block.text) for block in blocks)
    if total_chars <= 0 or section_end_s <= section_start_s:
        return 0
    bounds: list[float] = []
    accumulated = 0
    for block in blocks:
        accumulated += len(block.text)
        bounds.append(
            section_start_s + (section_end_s - section_start_s) * accumulated / total_chars
        )
    return min(bisect.bisect_right(bounds, timestamp_s), len(blocks) - 1)
```

`place_slides_in_sections` переписать так, чтобы позиция считалась этой функцией, а вставка выполнялась `inject_marker` из `markers.py`. Собственные `split_paragraphs` и `MARKER_TEMPLATE` удалить.

**Про тесты удаляемой функции.** `split_paragraphs` покрыт пятью проверками в `tests/unit/frames/test_placement.py` (строки 9-30: разбиение по пустым строкам, сохранение код-фенсов, пустая строка, только переводы строк). Их нельзя просто удалить: они описывают требования к сегментации, которые теперь обязана выполнять `parse_markdown_blocks`. Порядок действий:

1. перенести каждую из этих проверок на `parse_markdown_blocks`, сравнивая `tuple(block.text for block in parse_markdown_blocks(md))` с прежним ожидаемым списком;
2. прогнать перенесённые тесты **до** удаления `split_paragraphs`;
3. если какая-то граница разошлась — остановиться и сообщить контроллеру. Расхождение означает, что две реализации сегментировали текст по-разному, и тогда замена меняет позиции маркеров в существующих конспектах. Это не повод «поправить ожидание»;
4. только после зелёных перенесённых тестов удалять `split_paragraphs` и его старые тесты.

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: новые тесты PASS; характеризационные тесты из задачи 4 по-прежнему PASS без изменения ожиданий. Если они падают — поведение изменилось, это ошибка реализации, а не эталона.

- [ ] **Step 5: Проверить, что вторая реализация исчезла**

Run: `grep -rn "MARKER_TEMPLATE\|def split_paragraphs" lecturelog/`
Expected: пусто.

- [ ] **Step 6: Коммит**

```bash
git add lecturelog/infrastructure/frames/placement.py lecturelog/infrastructure/slides/alignment/markers.py tests/unit
git commit -m "refactor(slides): одна реализация сегментации и вставки маркеров"
```

---

### Task 6: Временнóе окно в retrieval

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/retrieval.py:25-111`
- Modify: `lecturelog/infrastructure/slides/alignment/transcript.py:22-29`
- Test: `tests/unit/slides/test_retrieval.py`

**Interfaces:**
- Consumes: `SectionRef(global_section_id, topic_index, local_index, start_s, end_s)`, `blocks_for_section(blocks, section)`.
- Produces: `generate_candidates(entry, sections, blocks, *, limit=5, neighbor_radius=1, window: tuple[float, float] | None = None)`. При `window=None` результат побитово совпадает с прежним.

- [ ] **Step 1: Написать падающий тест**

```python
def test_window_limits_candidates_to_overlapping_sections():
    """Окно отбрасывает секции, не пересекающиеся с ним по времени."""
    entry = SlideCatalogEntry(
        slide_num=1, role="content", title="Бэклог", visible_text="структура задач"
    )
    sections = (
        SectionRef(0, 0, 0, 0.0, 300.0),
        SectionRef(1, 0, 1, 300.0, 600.0),
    )
    blocks = [
        TranscriptBlock(block_id=1, start_s=10.0, end_s=20.0, text="структура задач в бэклоге"),
        TranscriptBlock(block_id=2, start_s=310.0, end_s=320.0, text="структура задач в бэклоге"),
    ]

    candidates = generate_candidates(
        entry, sections, blocks, limit=5, neighbor_radius=0, window=(0.0, 120.0)
    )

    assert {candidate.global_section_id for candidate in candidates} == {0}


def test_without_window_behaviour_is_unchanged():
    """Без окна кандидаты те же, что и до появления параметра."""
    entry = SlideCatalogEntry(
        slide_num=1, role="content", title="Бэклог", visible_text="структура задач"
    )
    sections = (
        SectionRef(0, 0, 0, 0.0, 300.0),
        SectionRef(1, 0, 1, 300.0, 600.0),
    )
    blocks = [
        TranscriptBlock(block_id=1, start_s=10.0, end_s=20.0, text="структура задач в бэклоге"),
        TranscriptBlock(block_id=2, start_s=310.0, end_s=320.0, text="совсем другая тема"),
    ]

    with_default = generate_candidates(entry, sections, blocks, limit=5, neighbor_radius=0)
    with_none = generate_candidates(
        entry, sections, blocks, limit=5, neighbor_radius=0, window=None
    )

    assert with_default == with_none
    assert {candidate.global_section_id for candidate in with_default} == {0}
```

Точные имена полей `TranscriptBlock` и `SectionRef` взять из `alignment/transcript.py`; если конструктор позиционный, передавать позиционно.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest tests/unit/slides/test_retrieval.py -k window -v`
Expected: FAIL — `generate_candidates` не принимает `window`.

- [ ] **Step 3: Реализовать**

В `transcript.py` добавить необязательное окно в выборку блоков:

```python
def blocks_for_section(
    blocks: list[TranscriptBlock],
    section: SectionRef,
    window: tuple[float, float] | None = None,
) -> tuple[TranscriptBlock, ...]:
    start_s = section.start_s if window is None else max(section.start_s, window[0])
    end_s = section.end_s if window is None else min(section.end_s, window[1])
    if end_s < start_s:
        return ()
    return tuple(
        block for block in blocks if block.end_s >= start_s and block.start_s <= end_s
    )
```

В `retrieval.py` в `generate_candidates` добавить параметр `window` и:

1. отбросить секции, не пересекающиеся с окном:

```python
    if window is not None:
        sections = tuple(
            section
            for section in sections
            if section.end_s >= window[0] and section.start_s <= window[1]
        )
        if not sections:
            return ()
```

2. передать окно в `blocks_for_section`:

```python
    section_documents = [
        (section, blocks_for_section(blocks, section, window)) for section in sections
    ]
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: новые тесты PASS; существующие тесты retrieval и alignment без новых падений.

- [ ] **Step 5: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/retrieval.py lecturelog/infrastructure/slides/alignment/transcript.py tests/unit/slides/test_retrieval.py
git commit -m "feat(slides): временное окно кандидатов в retrieval"
```

---

### Task 7: Кадры проходят каталогизацию и выравнивание

**Files:**
- Modify: `lecturelog/domain/slides.py:31-35`
- Modify: `lecturelog/infrastructure/slides/alignment/service.py:80-151`
- Test: `tests/unit/slides/test_domain_contracts.py`, `tests/unit/slides/test_alignment_service.py`

**Interfaces:**
- Consumes: `SlideAsset(slide_num, path, origin, timestamp, caption, extracted_text, native_text_quality)`; `generate_candidates(..., window=...)` из задачи 6; `AlignmentTuning` (`service.py:43-48`).
- Produces: `AlignmentTuning.frame_window_margin_s: float = 30.0`; окно кадра `(timestamp - margin, следующий timestamp + margin)`, для последнего кадра правая граница — конец его секции; deck guard не применяется, если все ассеты имеют `origin="video"`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_video_asset_accepts_catalog_metadata():
    """Кадру теперь положен каталог: запрет на текстовые метаданные снят."""
    asset = SlideAsset(
        slide_num=1,
        path=Path("frame-01.png"),
        origin="video",
        timestamp=42.0,
        extracted_text="Бэклог задач",
        native_text_quality="none",
    )

    assert asset.extracted_text == "Бэклог задач"


def test_document_asset_still_rejects_timestamp():
    """Инвариант документного слайда сохраняется."""
    with pytest.raises(ValueError):
        SlideAsset(
            slide_num=1,
            path=Path("slide-01.png"),
            origin="document",
            timestamp=42.0,
            extracted_text="текст",
            native_text_quality="good",
        )


def test_video_asset_still_requires_timestamp():
    with pytest.raises(ValueError):
        SlideAsset(slide_num=1, path=Path("frame-01.png"), origin="video")
```

```python
@pytest.mark.asyncio
async def test_deck_guard_does_not_apply_to_video_assets(tmp_path):
    """Кадры извлечены из этой же записи — посторонней колодой быть не могут."""
    service = DocumentAlignmentService()
    assets = [
        SlideAsset(
            slide_num=1,
            path=_png(tmp_path, "frame-01.png"),
            origin="video",
            timestamp=1.0,
        )
    ]

    result = await service.align(
        assets=assets,
        section_layout=[[{"title": "Раздел", "start": "00:00:00", "end": "00:05:00"}]],
        srt_content="1\n00:00:00,000 --> 00:00:05,000\nречь совсем о другом\n",
    )

    assert all(item.match_status != "deck_mismatch" for item in result.assignments)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/unit/slides/test_domain_contracts.py tests/unit/slides/test_alignment_service.py -k "video or timestamp" -v`
Expected: FAIL — доменный инвариант запрещает метаданные у видео; deck guard срабатывает.

- [ ] **Step 3: Реализовать**

В `domain/slides.py` в ветке `elif self.origin == "video":` убрать запрет на `extracted_text` и `native_text_quality`, оставив обязательность `timestamp`:

```python
        elif self.origin == "video":
            if self.timestamp is None:
                raise ValueError("video slide требует timestamp")
```

В `AlignmentTuning` добавить поле:

```python
    frame_window_margin_s: float = 30.0
```

В `align` вычислить окна кадров до цикла и передать окно в retrieval:

```python
        frame_windows = self._frame_windows(assets, sections)
        ...
            retrieved = generate_candidates(
                entry,
                sections,
                blocks,
                limit=self._tuning.candidate_limit,
                neighbor_radius=self._tuning.neighbor_radius,
                window=frame_windows.get(asset.slide_num),
            )
```

Метод окон:

```python
    def _frame_windows(
        self, assets: list[SlideAsset], sections: tuple[SectionRef, ...]
    ) -> dict[int, tuple[float, float]]:
        """Окна кадров: от появления кадра до появления следующего.

        Кадр висит на экране от своего timestamp до следующего кадра, речь о нём
        идёт в этом интервале. Запас нужен потому, что лектор начинает говорить
        о слайде за несколько секунд до переключения и продолжает после.
        """
        frames = sorted(
            (asset for asset in assets if asset.origin == "video" and asset.timestamp is not None),
            key=lambda asset: asset.timestamp,
        )
        if not frames:
            return {}
        margin = self._tuning.frame_window_margin_s
        transcript_end = max((section.end_s for section in sections), default=0.0)
        windows: dict[int, tuple[float, float]] = {}
        for index, asset in enumerate(frames):
            next_start = (
                frames[index + 1].timestamp if index + 1 < len(frames) else transcript_end
            )
            windows[asset.slide_num] = (
                max(asset.timestamp - margin, 0.0),
                next_start + margin,
            )
        return windows
```

Deck guard — не применять, если кадров нет среди документных ассетов:

```python
        video_only = bool(assets) and all(asset.origin == "video" for asset in assets)
        if content_count and supported < required and not video_only:
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: новые тесты PASS; тест deck guard из задачи 1 (документные ассеты) по-прежнему PASS.

- [ ] **Step 5: Коммит**

```bash
git add lecturelog/domain/slides.py lecturelog/infrastructure/slides/alignment/service.py tests/unit
git commit -m "feat(slides): кадры идут через каталог и выравнивание с временным окном"
```

---

### Task 8: Фолбэк по времени в anchoring

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/anchoring.py:19-110`
- Test: `tests/unit/slides/test_anchoring.py`

**Interfaces:**
- Consumes: `paragraph_index_for_time(...)` из задачи 5; `inject_marker(...)`.
- Produces: `anchor_assignment(assignment, entry, markdown, *, time_fallback: TimeFallback | None = None)`; `TimeFallback` — датакласс с полями `timestamp_s: float`, `section_start_s: float`, `section_end_s: float`. При переданном `time_fallback` и отсутствии доказательств слайд получает `output_kind="inline"`, `anchor_confidence="fallback"`, `fallback_reason="video_timestamp"`.

- [ ] **Step 1: Написать падающий тест**

```python
def test_frame_without_evidence_is_placed_by_time():
    """У кадра время известно точно: отсутствие слов не повод прятать его в галерею."""
    assignment = SlideAssignment(
        1, "discussed", 0, (), None, "probable", 3.0, "weak_evidence_only"
    )
    markdown = "первый абзац\n\nвторой абзац\n\nтретий абзац"

    updated, placement = anchor_assignment(
        assignment,
        None,
        markdown,
        time_fallback=TimeFallback(timestamp_s=90.0, section_start_s=0.0, section_end_s=180.0),
    )

    assert placement.output_kind == "inline"
    assert placement.anchor_confidence == "fallback"
    assert placement.fallback_reason == "video_timestamp"
    assert updated.index("<!-- slide:1 -->") > updated.index("второй абзац")


def test_document_slide_without_evidence_still_goes_to_gallery():
    """Для документного слайда поведение не меняется: фолбэка по времени нет."""
    assignment = SlideAssignment(
        1, "discussed", 0, (), None, "probable", 3.0, "weak_evidence_only"
    )
    markdown = "первый абзац\n\nвторой абзац"

    _updated, placement = anchor_assignment(assignment, None, markdown)

    assert placement.output_kind == "section_gallery"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest tests/unit/slides/test_anchoring.py -k time -v`
Expected: FAIL — `anchor_assignment` не принимает `time_fallback`.

- [ ] **Step 3: Реализовать**

Добавить в `anchoring.py` датакласс и ветку фолбэка перед возвратом галереи:

```python
@dataclass(frozen=True)
class TimeFallback:
    """Точно известное время слайда: применимо к кадрам из видеоряда."""

    timestamp_s: float
    section_start_s: float
    section_end_s: float
```

В `anchor_assignment` во всех местах, где сейчас возвращается `section_gallery` или `appendix` из-за отсутствия доказательств, сначала проверить `time_fallback`:

```python
    if time_fallback is not None:
        block_index = paragraph_index_for_time(
            markdown,
            section_start_s=time_fallback.section_start_s,
            section_end_s=time_fallback.section_end_s,
            timestamp_s=time_fallback.timestamp_s,
        )
        if block_index >= 0:
            return inject_marker(
                markdown,
                slide_num=assignment.slide_num,
                block_index=block_index,
                side="after",
            ), SlidePlacement(
                assignment.slide_num,
                "inline",
                assignment.global_section_id,
                anchor_confidence="fallback",
                fallback_reason="video_timestamp",
            )
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: новые тесты PASS; тесты документного anchoring без изменений.

- [ ] **Step 5: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/anchoring.py tests/unit/slides/test_anchoring.py
git commit -m "feat(slides): кадр без доказательств размещается по времени"
```

---

### Task 9: Видео-режим идёт общим путём в пайплайне

**Files:**
- Modify: `lecturelog/application/pipeline_service.py:295-360`
- Modify: `lecturelog/infrastructure/structurize/gemini_structurizer.py` (передача `time_fallback` в `anchor_assignment`)
- Test: `tests/unit/test_pipeline_service_video.py`

**Interfaces:**
- Consumes: `StructurizeContext(source_kind, local_video_path)`; `StructurizeResult(topics, slide_assignments, slide_placements)`.
- Produces: при видео без приложенного документа `structurize_kwargs["slide_assets"]` содержит кадры как `SlideAsset(origin="video", timestamp=...)`; ручное построение `SlidePlacement` с `fallback_reason="video_timestamp"` из `pipeline_service` удалено — теперь это делает `anchoring`.

- [ ] **Step 1: Написать падающий интеграционный тест**

```python
@pytest.mark.asyncio
async def test_video_frames_go_through_alignment(tmp_path, monkeypatch):
    """Кадры должны попадать в структуризатор как ассеты, а не размещаться отдельно."""
    seen: dict[str, object] = {}

    class RecordingStructurizer:
        async def structurize(self, *, srt_path, output_dir, on_progress, on_usage, slide_assets, context):
            seen["origins"] = [asset.origin for asset in slide_assets]
            seen["source_kind"] = context.source_kind
            return StructurizeResult(topics=[_topic_with_one_section()])

    # собрать сервис с RecordingStructurizer и видео-источником,
    # прогнать задачу до стадии structurize
    ...
    assert seen["origins"] == ["video"]
    assert seen["source_kind"] == "video"
```

Тест дописать по образцу существующих интеграционных тестов пайплайна из `tests/integration/`: там уже есть фикстуры источника, хранилища и заглушек стадий — переиспользовать их, а не изобретать заново.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest tests/unit/test_pipeline_service_video.py -v`
Expected: FAIL — кадры сейчас передаются не в структуризатор, а размещаются после него.

- [ ] **Step 3: Реализовать**

В `pipeline_service.py`:

1. если есть `video_frames` и нет документных `slide_items`, собрать кадры в `SlideAsset(origin="video", timestamp=...)` **до** вызова структуризатора и передать их как `slide_assets`;
2. удалить блок после структуризации, который вызывает `bind_frames_to_sections`, `place_slides_in_sections` и строит `placement_by_slide` вручную;
3. `slide_items` для экспорта должны остаться кадрами, чтобы `structure.json` ссылался на их PNG.

В `gemini_structurizer.py` при вызове `anchor_assignment` для ассета с `origin="video"` передать `TimeFallback(timestamp_s=asset.timestamp, section_start_s=..., section_end_s=...)`, где границы берутся из секции назначения.

- [ ] **Step 4: Прогнать все тесты**

Run: `.venv/bin/python -m pytest tests/unit tests/integration -q`
Expected: новый тест PASS; характеризационные тесты задачи 4 могут потребовать обновления только в части способа вызова, но не ожидаемых позиций маркеров. Если ожидаемая позиция изменилась — остановиться и разобраться, это регрессия.

- [ ] **Step 5: Линтер**

Run: `.venv/bin/python -m ruff check lecturelog tests`
Expected: All checks passed.

- [ ] **Step 6: Коммит**

```bash
git add lecturelog/application/pipeline_service.py lecturelog/infrastructure/structurize/gemini_structurizer.py tests
git commit -m "feat(slides): видео-режим идёт общим путём выравнивания"
```

---

### Task 10: Гейт по качеству

**Files:**
- Create: `benchmarks/lecture-quality/2026-07-XX-judge-<буква>-<лекция>.md` (отчёты судей)
- Modify: `docs/progress/2026-07-27-matcher-model-experiments.md`

**Interfaces:**
- Consumes: стенд `lecturelog-matcher-v2` (порт 18082), `scripts/submit_task.py`, скилл `skills/lecture-quality-judge/`.
- Produces: решение принять или откатить слияние.

- [ ] **Step 1: Пересобрать стенд**

```bash
docker compose -p lecturelog-matcher-v2 -f docker-compose.yml -f /tmp/lecturelog-matcher-v2.override.yml build api
docker compose -p lecturelog-matcher-v2 -f docker-compose.yml -f /tmp/lecturelog-matcher-v2.override.yml up -d api
```

- [ ] **Step 2: Прогнать видео-лекцию**

Видео-лекцию предоставляет пользователь; положить в `test-data/document-slide-alignment/<дата>/`. Прогон командой `scripts/submit_task.py submit --video <файл>` (проверить точное имя флага в `scripts/submit_task.py`). Прогоны последовательные: параллельные сжигают суточную квоту BYOK.

- [ ] **Step 3: Прогнать 2026-05-07 и 2026-02-12**

```bash
D=test-data/document-slide-alignment/2026-05-07
LECTURELOG_URL=http://127.0.0.1:18082/api/v1 python3 scripts/submit_task.py submit --audio $D/lecture.m4a --slides $D/slides.pdf
```

То же для `2026-02-12`.

- [ ] **Step 4: Оценить каждый прогон судьёй**

Отдельный сабагент на прогон, скилл `skills/lecture-quality-judge/`, Pass A до открытия `document-slide-alignment.json`, запрет читать прошлые отчёты.

- [ ] **Step 5: Проверить условия приёмки**

Принять, только если одновременно: verified precision не снизилась, high-confidence error rate не выросла, не появилось пропущенных или дублированных маркеров, вердикт судьи не стал хуже. Иначе — откатить слияние и разобраться в причине.

- [ ] **Step 6: Обновить прогресс-документ и закоммитить**

```bash
git add benchmarks/lecture-quality docs/progress
git commit -m "docs(slides): гейт слияния режимов на четырёх лекциях"
```

---

---

## Задачи 11–14: краевые случаи выравнивания

**Порядок исполнения:** эти четыре задачи выполняются **после задачи 3 и до задачи 4**. Причина: они чинят дефекты в том же коде, который затрагивает слияние, и задача 11 пересекается с задачей 5 (обе про сегментацию Markdown).

Красные тесты уже написаны и лежат в `tests/unit/slides/test_alignment_edge_cases.py`. Для каждой задачи RED-фаза уже готова: тест падает на текущем коде. Работа исполнителя — привести код в соответствие.

**Общее требование ко всем четырём задачам:** тест менять нельзя, кроме случая, когда он содержит фактическую ошибку — тогда остановиться и сообщить контроллеру, а не править ожидание. Ослаблять утверждения запрещено.

---

### Task 11: Маркер не попадает внутрь нумерованного списка

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/markers.py:20-29`
- Test: `tests/unit/slides/test_alignment_edge_cases.py::test_marker_never_lands_between_list_items_so_slide_image_does_not_split_the_list`

**Interfaces:**
- Consumes: `parse_markdown_blocks(markdown) -> tuple[MarkdownBlock, ...]`, поле `MarkdownBlock.atomic`.
- Produces: блок, начинающийся с пункта списка любого номера, помечается `atomic=True`.

Первопричина: в `parse_markdown_blocks` атомарность определяется префиксами `("- ", "* ", "+ ", "> ", "1. ")`. Литерал `"1. "` покрывает только первый пункт нумерованного списка — пункты `2.`, `3.` и далее считаются обычными абзацами и годятся под якорь. Экспортёр заменяет маркер на строку `![...](...)`, поэтому картинка разрезает список надвое.

- [ ] **Step 1: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest "tests/unit/slides/test_alignment_edge_cases.py::test_marker_never_lands_between_list_items_so_slide_image_does_not_split_the_list" -v`
Expected: FAIL с сообщением «маркер вставлен внутрь списка».

- [ ] **Step 2: Реализовать**

Заменить проверку префикса на регулярное выражение, покрывающее маркеры списка любого вида, включая нумерацию произвольным числом и оба разделителя (`.` и `)`):

```python
_LIST_PREFIX_RE = re.compile(r"^(?:[-*+]\s|\d+[.)]\s|>\s)")
```

и в цикле:

```python
        if _LIST_PREFIX_RE.match(stripped):
            atomic = True
```

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS. Тесты `tests/unit/slides/test_markers.py` и `tests/unit/frames/test_placement.py` — без новых падений. Если падает тест, фиксировавший прежнее поведение сегментации, разобраться и объяснить в отчёте, а не подгонять ожидание.

- [ ] **Step 4: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/markers.py tests/unit/slides/test_alignment_edge_cases.py
git commit -m "fix(slides): не ставить маркер внутрь нумерованного списка"
```

---

### Task 12: Страница progressive build сохраняет каталожную запись

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/catalog.py`
- Test: `tests/unit/slides/test_alignment_edge_cases.py::test_page_keeps_catalog_entry_when_boilerplate_filter_would_erase_all_its_lines`

**Interfaces:**
- Consumes: `detect_boilerplate_lines(...)`, `native_text_fallback(asset, boilerplate=...)`.
- Produces: у страницы, все строки которой сочтены колонтитулом, каталожная запись всё равно существует.

Первопричина: строки промежуточного шага сборки повторяются на всех последующих шагах, поэтому `detect_boilerplate_lines` принимает их за колонтитул колоды. У промежуточной страницы своих строк не остаётся, `native_text_fallback` возвращает `unresolved`, записи в каталоге нет — и страница получает `unmentioned` с причиной `no_supported_evidence`, хотя лектор её обсуждал.

Направление решения: фильтр колонтитулов не должен обнулять страницу целиком. Если после фильтрации у страницы не осталось ни одной строки, брать её нативный текст без фильтрации. Прочитай `catalog.py` целиком перед правкой и выбери минимальное изменение, сохраняющее исходное назначение фильтра — отсечение повторяющихся колонтитулов у страниц, где есть и собственный текст.

- [ ] **Step 1: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest "tests/unit/slides/test_alignment_edge_cases.py::test_page_keeps_catalog_entry_when_boilerplate_filter_would_erase_all_its_lines" -v`
Expected: FAIL — «страница 2 осталась без каталожной записи».

- [ ] **Step 2: Реализовать минимальную правку в `catalog.py`**

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS; `tests/unit/slides/test_catalog.py` без новых падений. Фильтр колонтитулов обязан по-прежнему отсекать повторяющиеся строки у страниц, где есть собственный текст — если такой тест сломался, правка слишком широкая.

- [ ] **Step 4: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/catalog.py tests/unit/slides/test_alignment_edge_cases.py
git commit -m "fix(slides): фильтр колонтитулов не обнуляет страницу целиком"
```

---

### Task 13: Вердикт в форме массива доходит до независимой проверки

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py`, метод `_verify`
- Test: `tests/unit/slides/test_alignment_edge_cases.py::test_array_shaped_strong_verdict_is_still_sent_to_the_independent_judge`

**Interfaces:**
- Consumes: `validate_semantic_response(...)` из `alignment/semantic.py` — принимает как объект, так и массив из одного объекта.
- Produces: `_verify` читает `semantic_tier` через тот же валидатор, а не через `json.loads(raw).get(...)`.

Первопричина: `_verify` достаёт tier выражением `json.loads(raw).get(...)`. Когда модель отвечает поддержанной формой `[{...}]`, `.get` вызывается на списке, возникает `AttributeError`, его глотает общий `except`, и strong-вердикт вместо независимой перепроверки уходит в слепой лексический подбор раздела.

- [ ] **Step 1: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest "tests/unit/slides/test_alignment_edge_cases.py::test_array_shaped_strong_verdict_is_still_sent_to_the_independent_judge" -v`
Expected: FAIL — «strong-вердикт принят без независимой перепроверки» (сделан один вызов вместо двух).

- [ ] **Step 2: Реализовать**

Читать tier из результата валидации, а не из сырого JSON. Прочитай `semantic.py`, чтобы использовать существующий валидатор, и не добавляй разбор транспортной формы вторым местом в коде.

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS; `tests/unit/slides/test_semantic.py` и `test_alignment_service.py` без новых падений.

- [ ] **Step 4: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py tests/unit/slides/test_alignment_edge_cases.py
git commit -m "fix(slides): вердикт-массив тоже уходит на независимую проверку"
```

---

### Task 14: Повышение вердикта судьёй сохраняет подтверждённый раздел

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py`, метод `_verify`
- Test: `tests/unit/slides/test_alignment_edge_cases.py::test_judge_upgrading_strong_to_explicit_keeps_the_confirmed_section`

**Interfaces:**
- Consumes: значения `semantic_tier` из `alignment/schemas.py`.
- Produces: результат независимой проверки принимается, если её вердикт не слабее `strong`; повышение до `explicit` подтверждает раздел, а не отбрасывает его.

Первопричина: `_verify` принимает результат перепроверки только при строгом равенстве `semantic_tier == "strong"`. Если судья повысил вердикт до `explicit`, подтверждённое совпадение выбрасывается, и вместо него берётся результат `_global_recovery` — лексической догадки по всей лекции. Слайд молча оказывается в разделе, который модель дважды не выбирала.

- [ ] **Step 1: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest "tests/unit/slides/test_alignment_edge_cases.py::test_judge_upgrading_strong_to_explicit_keeps_the_confirmed_section" -v`
Expected: FAIL — «подтверждённый судьёй раздел подменён лексической догадкой».

- [ ] **Step 2: Реализовать**

Сравнивать силу вердикта по порядку уровней, а не литералом. Выясни в `schemas.py` полный перечень значений `semantic_tier` и их порядок; прими вердикты уровня `strong` и выше. Порядок уровней задать одним явным кортежем в модуле, чтобы сравнение не расползлось по коду.

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS; тест из задачи 13 остаётся PASS; `test_semantic.py` без новых падений.

- [ ] **Step 4: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py tests/unit/slides/test_alignment_edge_cases.py
git commit -m "fix(slides): повышенный вердикт судьи подтверждает раздел"
```

---

---

## Задачи 15–19: регрессии от задач 2, 3, 12, 14 и решение из очереди

**Порядок исполнения:** после задачи 14 и **до задачи 4**. Три из четырёх дефектов внесены задачами этого же плана, поэтому чинятся прежде, чем на них наслоится слияние режимов.

Красные тесты лежат в `tests/unit/slides/test_alignment_edge_cases_round2.py` — RED-фаза готова, менять тесты нельзя. Нашёл их отдельный проход охоты на краевые случаи.

---

### Task 15: Коллизия доказательств считается по парам, а не по слайдам

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py`, метод `_downgrade_evidence_collisions`
- Test: `tests/unit/slides/test_alignment_edge_cases_round2.py::test_коллизия_понижает_несвязанный_слайд_потому_что_связь_бывает_только_попарной`

**Interfaces:**
- Consumes: `SlideRelation(slide_num, canonical_slide_num, group_id, kind)` из `domain/slides.py:66-71`.
- Produces: слайд исключается из понижения только относительно тех слайдов, с которыми он связан. Несвязанный сосед по блоку понижается независимо от того, есть ли у соседей связи между собой.

Первопричина (регрессия задачи 3): множество `related` собирается по номерам слайдов, поэтому слайд, связанный хоть с одной страницей, вообще не попадает в карту коллизий. В результате даже полностью несвязанный слайд, делящий блок с такой группой, остаётся `verified`. Связь по смыслу попарная (`progressive_build` и `exact_duplicate` соединяют конкретные страницы), а код трактует её как индульгенцию для слайда целиком.

- [ ] **Step 1: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest "tests/unit/slides/test_alignment_edge_cases_round2.py::test_коллизия_понижает_несвязанный_слайд_потому_что_связь_бывает_только_попарной" -v`
Expected: FAIL — слайд 9 остался `verified`.

- [ ] **Step 2: Реализовать**

Строить отношение связанности как множество пар (или как отображение слайд → множество связанных с ним слайдов, замкнутое по `group_id`). На каждом доказательном блоке понижать те слайды, для которых на этом блоке есть хотя бы один **несвязанный** с ними сосед. Пара связанных слайдов, оказавшаяся на блоке одна, не понижается.

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS; `test_evidence_collision_downgrades_unrelated_verified_assignments` и три теста коллизий из задачи 3 (`tests/unit/slides/test_alignment_service.py`) остаются PASS.

- [ ] **Step 4: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py
git commit -m "fix(slides): коллизия доказательств считается по парам связей"
```

---

### Task 16: Пустой `visible_text` не обнуляет страницу, описанную другими полями

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py`, функция `normalize_empty_entry`
- Test: `tests/unit/slides/test_alignment_edge_cases_round2.py::test_страница_без_видимого_текста_но_с_концептами_остаётся_привязываемой`

**Interfaces:**
- Consumes: `SlideCatalogEntry` (`domain/slides.py:41-50`) — поля `title`, `visible_text`, `source_concepts`, `transcript_language_terms`, `formulas`, `visual_summary`, `proper_nouns`.
- Produces: роль `blank` присваивается только когда пусты **все** содержательные поля записи.

Первопричина (регрессия задачи 2): `normalize_empty_entry` смотрит только `title` и `visible_text`. Но retrieval и grounding строят запрос ещё из `source_concepts`, `transcript_language_terms` и `formulas`. Страница с одной фотографией или схемой закономерно имеет пустой `visible_text` (промпт `document_slide_catalog_v3.md` описывает это поле как текст страницы), при заполненных концептах и именах собственных. Такая страница получает `blank`, кандидатов ноль, второй вызов модели не делается — и она молча уходит в приложение.

Решение владельца продукта «верить модели» этим не нарушается: модель сказала `role="content"` и описала содержание — просто в других полях.

- [ ] **Step 1: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest "tests/unit/slides/test_alignment_edge_cases_round2.py::test_страница_без_видимого_текста_но_с_концептами_остаётся_привязываемой" -v`
Expected: FAIL — роль стала `blank` при заполненных `source_concepts`.

- [ ] **Step 2: Реализовать**

Считать запись пустой, только если пусты все содержательные поля. Перечисли их одним явным списком в функции, чтобы при добавлении поля в модель было видно, где его учесть.

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS; оба теста задачи 2 в `test_alignment_service.py` остаются PASS.

- [ ] **Step 4: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py
git commit -m "fix(slides): blank только когда пусты все поля записи"
```

---

### Task 17: Восстановленные строки не служат доказательством

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/catalog.py`
- Test: `tests/unit/slides/test_alignment_edge_cases_round2.py::test_страница_только_с_колонтитулом_не_привязывается_по_колонтитулу`

**Interfaces:**
- Consumes: `detect_boilerplate_lines(...)`, `native_text_fallback(asset, boilerplate=...)`.
- Produces: страница, все строки которой признаны колонтитулом, сохраняет каталожную запись (решение задачи 12 в силе), но её `source_concepts` и `visible_text` не наполняются колонтитулом, поэтому grounding не может построить по нему claim.

Первопричина (регрессия задачи 12): при полном обнулении страницы берётся нефильтрованный текст, и колонтитул попадает не только в `title` (что принято сознательно), но и в `source_concepts`/`visible_text`. Оттуда grounding строит доказательство — и страница-разделитель, на которой напечатан только колонтитул, получает `verified` с `semantic_explicit` на реплике вида «курс …, лекция вторая». Это противоречит докстрингу самой `detect_boilerplate_lines`: «в качестве доказательства она бесполезна: совпадает с любой репликой, где лектор произносит название курса».

Второе следствие, которое обязательно устранить: такая привязка засчитывается deck guard как подтверждение родства колоды с лекцией, то есть посторонняя колода способна пройти guard на одних колонтитулах.

- [ ] **Step 1: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest "tests/unit/slides/test_alignment_edge_cases_round2.py::test_страница_только_с_колонтитулом_не_привязывается_по_колонтитулу" -v`
Expected: FAIL — страница привязана по колонтитулу с `verified`.

- [ ] **Step 2: Реализовать**

Разделить два назначения текста: `title` для человека (там колонтитул допустим) и содержательные поля для матчинга (там его быть не должно). Минимальная форма — при откате оставлять восстановленные строки только в `title`, а `source_concepts` и `visible_text` не наполнять.

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS; тест задачи 12 (`test_page_keeps_catalog_entry_when_boilerplate_filter_would_erase_all_its_lines`) остаётся PASS — запись по-прежнему существует; шесть тестов `test_catalog.py` без падений.

- [ ] **Step 4: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/catalog.py
git commit -m "fix(slides): колонтитул не становится доказательством привязки"
```

---

### Task 18: Судья подтверждает раздел, а не только силу вердикта

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py`, метод `_verify`
- Test: `tests/unit/slides/test_alignment_edge_cases_round2.py::test_судья_назвавший_другой_раздел_не_подтверждает_strong_вердикт`

**Interfaces:**
- Consumes: результат `parse_semantic_match(raw)` — поля `global_section_id` и `semantic_tier`.
- Produces: результат второго прохода принимается только если он указал **тот же** `global_section_id`, что и первый. Иначе подтверждения нет.

Первопричина: `_verify` заявляет в комментарии «strong принимается только после независимой второй проверки», но согласие проходов не сверяет — проверяется лишь сила вердикта, а `strong_judge_agrees=True` передаётся до всякой проверки. В результате слайд встаёт в разделе, который первый проход не выбирал, с той же уверенностью, что и дважды подтверждённое совпадение.

- [ ] **Step 1: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest "tests/unit/slides/test_alignment_edge_cases_round2.py::test_судья_назвавший_другой_раздел_не_подтверждает_strong_вердикт" -v`
Expected: FAIL — принят раздел, названный только одним проходом.

- [ ] **Step 2: Реализовать**

Сверять `global_section_id` двух проходов. При расхождении подтверждения нет — дальше по существующей логике отказа.

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS; тесты задач 13 и 14 в `test_alignment_edge_cases.py` остаются PASS — в них оба прохода называют один и тот же раздел.

- [ ] **Step 4: Коммит**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py
git commit -m "fix(slides): подтверждением считается только тот же раздел"
```

---

### Task 19: Лексическое доказательство без модели не даёт `explicit`

Решение владельца продукта по записи из очереди вопросов: чисто лексическое совпадение никогда не выдаёт `explicit`, его потолок — `strong`. Два пути (`_lexical_ground` и `_global_recovery`) приводятся к одному правилу.

**Files:**
- Modify: `lecturelog/infrastructure/slides/alignment/service.py`, метод `_lexical_ground`
- Test: `tests/unit/slides/test_alignment_edge_cases_round2.py` — добавить новый тест (RED-фазы для него ещё нет)
- Modify: `OPEN-QUESTIONS.md` — удалить запись после реализации

**Interfaces:**
- Consumes: `SlideCandidate.semantic_tier`.
- Produces: `_lexical_ground` возвращает кандидата с `semantic_tier="strong"`; путь без участия модели больше не приводит к `anchor_confidence="verified"`.

- [ ] **Step 1: Написать падающий тест**

```python
@pytest.mark.asyncio
async def test_лексическое_совпадение_без_модели_не_даёт_explicit(tmp_path: Path) -> None:
    """Модель ничего не подтверждала: потолок такого доказательства — strong.

    Иначе страница с неподтверждённым каталогом получает inline-картинку с
    уверенностью verified на пересечении двух общеупотребительных слов, и
    anchoring пропускает её мимо проверки специфичности абзаца.
    """
```

Тело теста собрать по образцу соседних тестов файла: сервис без LLM (или с `catalog_verified=False`), проверить, что полученный кандидат имеет `semantic_tier == "strong"`, а не `"explicit"`.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest tests/unit/slides/test_alignment_edge_cases_round2.py -k лексическое -v`
Expected: FAIL — tier равен `explicit`.

- [ ] **Step 3: Реализовать**

В `_lexical_ground` выставлять `semantic_tier="strong"`.

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: целевой тест PASS. Тест `test_verified_keeps_inline_on_weak_evidence` может упасть: он фиксировал прежнее поведение (лексический путь даёт `verified`). Разобраться, что он утверждает, и переписать под новое правило, объяснив это в отчёте. Ослаблять его нельзя — если он проверяет что-то ещё помимо tier, эта часть должна остаться.

- [ ] **Step 5: Удалить запись из `OPEN-QUESTIONS.md` и закоммитить**

```bash
git add lecturelog/infrastructure/slides/alignment/service.py tests/unit/slides/test_alignment_edge_cases_round2.py OPEN-QUESTIONS.md
git commit -m "fix(slides): лексический путь не выдаёт explicit"
```

---

## Самопроверка плана

**Покрытие спеки.** Все разделы спеки имеют задачу: OPEN-QUESTIONS → задачи 1–3; характеризационные тесты → 4; единая сегментация и маркеры → 5; окно в retrieval → 6; каталог кадров, доменный инвариант, deck guard мимо видео → 7; фолбэк по времени → 8; пайплайн и удаление ручного размещения → 9; гейт → 10. Не-цели (задача 11 плана v2, улучшение качества видео) задач не имеют сознательно.

**Незакрытая зависимость.** Задача 10 требует видео-лекции от пользователя. Задачи 1–9 от неё не зависят и выполняются раньше.

**Согласованность имён между задачами.** `paragraph_index_for_time` (задача 5) используется в задаче 8; `window` в `generate_candidates` (задача 6) вызывается из `align` в задаче 7; `TimeFallback` (задача 8) передаётся из `gemini_structurizer` в задаче 9; `frame_window_margin_s` объявлен в задаче 7 и больше нигде не переименовывается.

**Риск, требующий внимания исполнителя.** В задаче 9 названия фикстур интеграционных тестов не выписаны: их надо взять из существующих тестов пайплайна. Это единственное место, где план сознательно опирается на чтение соседнего кода вместо готового листинга.
