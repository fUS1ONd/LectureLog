"""Краевые случаи подсистемы выравнивания слайдов.

Каждый тест здесь описывает отказ, который не кричит: результат выглядит
правдоподобно, а ошибка обнаруживается только при сверке с транскриптом.
"""

import json
import re
from pathlib import Path

import pytest

from lecturelog.domain.slides import (
    SectionRef,
    SlideAsset,
    SlideAssignment,
    SlideCatalogEntry,
)
from lecturelog.infrastructure.slides.alignment import anchoring
from lecturelog.infrastructure.slides.alignment.retrieval import generate_candidates
from lecturelog.infrastructure.slides.alignment.service import DocumentAlignmentService
from lecturelog.infrastructure.srt import parse_srt_blocks

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>\s)")


class ScriptedLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


# ── Разметка Markdown ──────────────────────────────────────────────


def test_marker_never_lands_between_list_items_so_slide_image_does_not_split_the_list() -> None:
    """Экспортёр заменяет маркер на строку ![...](...), и картинка посреди списка режет его надвое.

    `parse_markdown_blocks` считает атомарным только пункт, начинающийся с «1. »,
    поэтому в списке с пустыми строками между пунктами все пункты кроме первого
    выглядят обычными абзацами и годятся под якорь. План требует обратного:
    «Маркер вставляется только между Markdown-блоками, никогда внутрь fenced code,
    callout или списка», release gate — «ни одного маркера внутри списка: 100%».
    """
    markdown = (
        "Разберём модели процесса.\n\n"
        "1. Водопадная модель\n\n"
        "2. Спиральная модель управления рисками\n\n"
        "3. Итеративная модель\n"
    )
    assignment = SlideAssignment(1, "discussed", 0, (1,), 1.0, "probable", 10.0, "matched")
    entry = SlideCatalogEntry(
        1,
        "content",
        "Спиральная модель",
        "спиральная модель управления рисками",
    )

    result, _placement = anchoring.anchor_assignment(assignment, entry, markdown)

    lines = [line for line in result.splitlines() if line.strip()]
    marker = "<!-- slide:1 -->"
    if marker not in lines:
        return
    position = lines.index(marker)
    previous_is_item = position > 0 and _LIST_ITEM_RE.match(lines[position - 1])
    next_is_item = position + 1 < len(lines) and _LIST_ITEM_RE.match(lines[position + 1])
    assert not (previous_is_item and next_is_item), f"маркер вставлен внутрь списка:\n{result}"


# ── Каталог страниц ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_page_keeps_catalog_entry_when_boilerplate_filter_would_erase_all_its_lines(
    tmp_path: Path,
) -> None:
    """Progressive build съедает промежуточную страницу целиком, и она молча выпадает из конспекта.

    Строки шага сборки повторяются на всех последующих шагах, поэтому
    `detect_boilerplate_lines` принимает их за колонтитул. У промежуточной
    страницы своих строк не остаётся, `native_text_fallback` отдаёт
    `unresolved`, каталожной записи нет — и страница получает `unmentioned`
    с причиной `no_supported_evidence`, хотя лектор её обсуждал.
    """
    texts = [
        "Введение в курс\nОрганизационные вопросы",
        "Модели процесса разработки\nВодопадная модель",
        "Модели процесса разработки\nВодопадная модель\nСпиральная модель",
        "Модели процесса разработки\nВодопадная модель\nСпиральная модель\nИтеративная модель",
        "Заключение\nЧто дальше",
    ]
    assets = []
    for number, text in enumerate(texts, start=1):
        path = tmp_path / f"{number}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + str(number).encode())
        assets.append(
            SlideAsset(number, path, "document", extracted_text=text, native_text_quality="good")
        )

    entries, _verified = await DocumentAlignmentService()._catalog(assets, None)

    assert 2 in entries, f"страница 2 осталась без каталожной записи, есть только {sorted(entries)}"


# ── Семантическая проверка кандидата ───────────────────────────────


def _semantic_fixture(tmp_path: Path):
    """Два раздела: в первом слайд обсуждают своими словами, во втором — читают по плану.

    Лексика сильнее во втором разделе, поэтому запасной лексический поиск
    выберет именно его, а модель — первый. Расхождение делает видимой любую
    тихую подмену вердикта модели лексической догадкой.
    """
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_semantic_match_v1.md").write_text("semantic", encoding="utf-8")
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:04,000\n"
        "Здесь важен процесс управления рисками на каждом новом витке\n\n"
        "2\n00:00:05,000 --> 00:00:10,000\n"
        "Дальше по плану спиральная модель управления рисками\n"
    )
    sections = (SectionRef(0, 0, 0, 0, 4.9), SectionRef(1, 0, 1, 5, 10))
    entry = SlideCatalogEntry(
        1,
        "content",
        "Спиральная модель",
        "спиральная модель управления рисками",
    )
    candidates = generate_candidates(entry, sections, blocks)
    return prompts, entry, candidates, blocks, sections


_STRONG_VERDICT = {
    "slide_num": 1,
    "global_section_id": 0,
    "evidence_block_ids": [1],
    "evidence_quote": "процесс управления рисками на каждом новом витке",
    "semantic_tier": "strong",
}


@pytest.mark.asyncio
async def test_array_shaped_strong_verdict_is_still_sent_to_the_independent_judge(
    tmp_path: Path,
) -> None:
    """Массив из одного объекта — поддержанная транспортная форма, но судья по ней не запускается.

    `_verify` читает tier через `json.loads(raw).get(...)`, хотя
    `validate_semantic_response` принимает и `[{...}]`. На массиве получается
    AttributeError, его глотает общий except, и strong-вердикт вместо
    независимой перепроверки уходит в слепой лексический подбор раздела.
    """
    prompts, entry, candidates, blocks, sections = _semantic_fixture(tmp_path)
    llm = ScriptedLlm([json.dumps([_STRONG_VERDICT]), json.dumps(_STRONG_VERDICT)])
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts)

    result = await service._verify(entry, candidates, blocks, sections, None, catalog_verified=True)

    assert len(llm.calls) == 2, "strong-вердикт принят без независимой перепроверки"
    assert [item.global_section_id for item in result] == [0]


@pytest.mark.asyncio
async def test_judge_upgrading_strong_to_explicit_keeps_the_confirmed_section(
    tmp_path: Path,
) -> None:
    """Судья подтвердил раздел более сильным вердиктом — и назначение уехало в другой раздел.

    `_verify` принимает результат перепроверки только при
    `semantic_tier == "strong"`. Если судья повысил вердикт до `explicit`,
    подтверждённое совпадение выбрасывается, и вместо него берётся результат
    `_global_recovery` — лексической догадки по всей лекции. Слайд молча
    оказывается в разделе, который модель дважды не выбирала.
    """
    prompts, entry, candidates, blocks, sections = _semantic_fixture(tmp_path)
    upgraded = dict(_STRONG_VERDICT, semantic_tier="explicit")
    llm = ScriptedLlm([json.dumps(_STRONG_VERDICT), json.dumps(upgraded)])
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts)

    result = await service._verify(entry, candidates, blocks, sections, None, catalog_verified=True)

    assert [item.global_section_id for item in result] == [0], (
        "подтверждённый судьёй раздел подменён лексической догадкой"
    )
