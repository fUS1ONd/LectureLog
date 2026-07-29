"""Краевые случаи выравнивания слайдов, прогон 2.

Здесь только тихие отказы: слайд встаёт в правдоподобном, но неверном месте,
либо молча исчезает из конспекта. Ни один из этих случаев не падает с
исключением и не виден без сверки с транскриптом.
"""

import json
from pathlib import Path

import pytest

from lecturelog.domain.slides import (
    SectionRef,
    SlideAsset,
    SlideAssignment,
    SlideCatalogEntry,
    SlideRelation,
)
from lecturelog.infrastructure.slides.alignment.retrieval import generate_candidates
from lecturelog.infrastructure.slides.alignment.service import DocumentAlignmentService
from lecturelog.infrastructure.srt import parse_srt_blocks


class ScriptedLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _assignment(*, slide_num: int, block_ids: tuple[int, ...], confidence: str) -> SlideAssignment:
    return SlideAssignment(
        slide_num, "discussed", 1, block_ids, 10.0, confidence, 12.0, "semantic_strong"
    )


# ── Коллизия доказательств ─────────────────────────────────────────


def test_коллизия_понижает_несвязанный_слайд_потому_что_связь_бывает_только_попарной() -> None:
    """Связь с третьей страницей нельзя предъявлять как оправдание коллизии с четвёртой.

    Решение владельца продукта (план, Task 3): пара слайдов на одном
    доказательном блоке остаётся `verified` только если слайды связаны между
    собой как `progressive_build` или `exact_duplicate`. Множество `related` в
    `_downgrade_evidence_collisions` собрано по слайдам, а не по парам: слайд 3,
    связанный с шагом сборки 2, вообще не попадает в карту коллизий, поэтому его
    коллизия с несвязанным слайдом 9 не обнаруживается — и, поскольку в карте
    остаётся один номер, вместе с ней теряется и понижение слайда 9.

    Что тихо ломается: две разные страницы предъявлены как «доказанно
    обсуждаемые» на одной и той же реплике, обе с `verified`; anchoring
    пропускает `verified` мимо проверки специфичности абзаца, и в конспект встают
    inline две картинки на один абзац, одна из которых точно не про него.
    """
    assignments = (
        _assignment(slide_num=2, block_ids=(120,), confidence="verified"),
        _assignment(slide_num=3, block_ids=(120,), confidence="verified"),
        _assignment(slide_num=9, block_ids=(120,), confidence="verified"),
    )
    relations = (
        SlideRelation(slide_num=3, kind="progressive_build", group_id="g1", canonical_slide_num=2),
    )

    result = DocumentAlignmentService._downgrade_evidence_collisions(assignments, relations)
    confidence_by_slide = {item.slide_num: item.assignment_confidence for item in result}

    assert confidence_by_slide[9] == "probable", (
        "слайд 9 ни с чем не связан и делит блок 120 с чужой страницей, "
        f"но остался verified: {confidence_by_slide}"
    )
    assert confidence_by_slide[3] == "probable", (
        "слайд 3 связан со слайдом 2, но не со слайдом 9 — эта пара не является "
        f"одной страницей в двух видах: {confidence_by_slide}"
    )


# ── Независимая перепроверка strong-вердикта ───────────────────────


def _judge_fixture(tmp_path: Path):
    """Три раздела: модель называет первый, судья может уехать в третий.

    Третий раздел лексически чужой слайду (`lexical_score` 0.118), поэтому
    запасной путь `_global_recovery` его выбрать не может — если слайд оказался
    там, это именно принятый вердикт судьи, а не лексическая догадка.
    """
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_semantic_match_v1.md").write_text("semantic", encoding="utf-8")
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:04,000\n"
        "Здесь важен процесс управления рисками на каждом новом витке\n\n"
        "2\n00:00:05,000 --> 00:00:09,000\n"
        "Дальше по плану спиральная модель управления рисками\n\n"
        "3\n00:00:10,000 --> 00:00:14,000\n"
        "А теперь организация практики и сдача домашних заданий\n"
    )
    sections = (
        SectionRef(0, 0, 0, 0, 4.9),
        SectionRef(1, 0, 1, 5, 9.9),
        SectionRef(2, 0, 2, 10, 14),
    )
    entry = SlideCatalogEntry(
        1,
        "content",
        "Спиральная модель",
        "спиральная модель управления рисками",
    )
    return prompts, entry, generate_candidates(entry, sections, blocks), blocks, sections


@pytest.mark.asyncio
async def test_судья_назвавший_другой_раздел_не_подтверждает_strong_вердикт(
    tmp_path: Path,
) -> None:
    """Перепроверка обязана подтверждать раздел, а не выдавать новый без перепроверки.

    В `_verify` записано правило: «Strong evidence is accepted only after an
    independent second pass». Второй проход валидируется с
    `strong_judge_agrees=True`, но само согласие ни с чем не сверяется:
    проверяется только сила вердикта (`_tier_at_least(..., "strong")`). Если
    судья вернул strong для другого раздела, это несогласие с первым проходом,
    а код принимает его как подтверждение — и раздел, который назвал ровно один
    проход, попадает в результат вообще без независимой проверки.

    Что тихо ломается: слайд встаёт в разделе, который первый проход не выбирал,
    с `semantic_tier="strong"` — то есть с той же уверенностью, что и дважды
    подтверждённое совпадение. В конспекте это выглядит как обычная привязка.
    """
    prompts, entry, candidates, blocks, sections = _judge_fixture(tmp_path)
    first_pass = {
        "slide_num": 1,
        "global_section_id": 0,
        "evidence_block_ids": [1],
        "evidence_quote": "процесс управления рисками на каждом новом витке",
        "semantic_tier": "strong",
    }
    judge_moves_away = {
        "slide_num": 1,
        "global_section_id": 2,
        "evidence_block_ids": [3],
        "evidence_quote": "организация практики",
        "semantic_tier": "strong",
    }
    llm = ScriptedLlm([json.dumps(first_pass), json.dumps(judge_moves_away)])
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts)

    result = await service._verify(entry, candidates, blocks, sections, None, catalog_verified=True)

    assert all(item.global_section_id != 2 for item in result), (
        "раздел 2 назвал только один из двух проходов, независимой проверки у него нет: "
        f"{[(item.global_section_id, item.semantic_tier) for item in result]}"
    )


# ── Пустая запись каталога ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_страница_без_видимого_текста_но_с_концептами_остаётся_привязываемой(
    tmp_path: Path,
) -> None:
    """Страница-иллюстрация не пуста: её содержание лежит в concepts, terms и formulas.

    Решение владельца продукта (план, Task 2) — верить модели: запись без
    заголовка и без содержания считается утверждением «на странице ничего нет».
    Здесь модель утверждает обратное: `role="content"`, заполнены
    `source_concepts`, `transcript_language_terms`, `visual_summary`,
    `proper_nouns`. Пустой оказался лишь `visible_text` — ровно то, что и бывает
    у страницы с одной фотографией или схемой (промпт каталога называет это поле
    текстом страницы). `normalize_empty_entry` смотрит только на `title` и
    `visible_text`, ставит `blank` поверх вердикта модели, и `_NON_MATCHABLE_ROLES`
    отменяет матчинг — хотя и retrieval, и grounding строят запрос и claim в том
    числе из `source_concepts`, `transcript_language_terms` и `formulas`.

    Что тихо ломается: страница, которую лектор разбирал вслух, не получает ни
    одного кандидата (второй вызов LLM даже не делается) и уходит в приложение с
    причиной `service_role:blank`. В конспекте её просто нет.
    """
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "document_slide_catalog_v3.md").write_text("catalog", encoding="utf-8")
    (prompts / "document_slide_semantic_match_v1.md").write_text("semantic", encoding="utf-8")
    image = tmp_path / "slide.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    catalog_response = json.dumps(
        {
            "slides": [
                {
                    "slide_num": 1,
                    "role": "content",
                    "title": None,
                    "visible_text": "",
                    "source_concepts": ["архитектура ENIAC"],
                    "transcript_language_terms": ["ЭНИАК"],
                    "visual_summary": "фотография машины ENIAC в машинном зале",
                    "formulas": [],
                    "proper_nouns": ["ENIAC"],
                }
            ]
        }
    )
    semantic_response = json.dumps(
        {
            "slide_num": 1,
            "global_section_id": 0,
            "evidence_block_ids": [1],
            "evidence_quote": "перейдём к архитектуре ENIAC",
            "semantic_tier": "explicit",
        }
    )
    llm = ScriptedLlm([catalog_response, semantic_response])
    service = DocumentAlignmentService(llm=llm, models=["m"], prompts_dir=prompts, effort="low")

    result = await service.align(
        assets=[
            SlideAsset(1, image, "document", extracted_text="", native_text_quality="none"),
        ],
        section_layout=[[{"title": "Первые ЭВМ", "start": "0:00", "end": "0:10"}]],
        srt_content="1\n00:00:00,000 --> 00:00:10,000\nТеперь перейдём к архитектуре ENIAC\n",
    )

    assert result.catalog[1].role == "content", (
        "роль content от модели переопределена, хотя содержание страницы описано "
        f"в других полях: {result.catalog[1]}"
    )
    assert result.assignments[0].match_status == "discussed", (
        "страница-иллюстрация молча ушла в приложение: "
        f"{result.assignments[0].match_status}/{result.assignments[0].reason_code}"
    )


# ── Восстановление страницы, обнулённой фильтром колонтитулов ──────


@pytest.mark.asyncio
async def test_страница_только_с_колонтитулом_не_привязывается_по_колонтитулу(
    tmp_path: Path,
) -> None:
    """Колонтитул восстановлен как содержание страницы и сработал как доказательство.

    Решение владельца продукта (план, Task 12) — не терять каталожную запись
    страницы, у которой фильтр колонтитулов выел все строки; следствие
    «колонтитул попадёт в `title`» принято сознательно. Но восстановленные строки
    попадают ещё и в `source_concepts` с `visible_text`, а из них grounding
    строит claim. Это прямо противоречит назначению самого фильтра
    (`detect_boilerplate_lines`): «в качестве доказательства она бесполезна:
    совпадает с любой репликой, где лектор произносит название курса».

    Страница-разделитель, на которой напечатан только колонтитул колоды,
    получает здесь `discussed` + `verified` + `semantic_explicit` на реплике
    «Итак, курс разработка программного обеспечения, лекция вторая».

    Что тихо ломается: (1) картинка разделителя встаёт inline рядом с вводным
    абзацем, причём `verified` снимает в anchoring проверку специфичности
    абзаца; (2) такая привязка считается deck guard'ом подтверждением, что
    колода относится к лекции, — то есть посторонняя колода может пройти guard
    на одних колонтитулах.
    """
    header = "Разработка программного обеспечения"
    texts = [
        f"{header}\nЛекция 2: жизненный цикл\nЭтапы разработки",
        f"{header}\nВодопадная модель\nПоследовательные этапы",
        header,  # страница-разделитель: своего текста на ней нет
        f"{header}\nСпиральная модель\nУправление рисками",
        f"{header}\nИтеративная модель\nКороткие циклы",
    ]
    assets = []
    for number, text in enumerate(texts, start=1):
        path = tmp_path / f"{number}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + str(number).encode())
        assets.append(
            SlideAsset(number, path, "document", extracted_text=text, native_text_quality="good")
        )
    srt_content = (
        "1\n00:00:00,000 --> 00:00:09,000\n"
        "Итак, курс разработка программного обеспечения, лекция вторая\n\n"
        "2\n00:00:10,000 --> 00:00:19,000\n"
        "Водопадная модель задаёт последовательные этапы\n\n"
        "3\n00:00:20,000 --> 00:00:29,000\n"
        "Спиральная модель добавляет управление рисками\n"
    )
    section_layout = [
        [
            {"title": "Вступление", "start": "0:00", "end": "0:09"},
            {"title": "Водопад", "start": "0:10", "end": "0:19"},
            {"title": "Спираль", "start": "0:20", "end": "0:29"},
        ]
    ]

    result = await DocumentAlignmentService().align(
        assets=assets,
        section_layout=section_layout,
        srt_content=srt_content,
    )
    divider = next(item for item in result.assignments if item.slide_num == 3)

    # Запись в каталоге у страницы остаётся (это и есть решение Task 12) —
    # проверяем только то, что колонтитул не сработал как доказательство.
    assert 3 in result.catalog
    assert divider.match_status != "discussed", (
        "разделитель привязан по колонтитулу колоды: "
        f"section={divider.global_section_id} conf={divider.assignment_confidence} "
        f"evidence={divider.evidence_block_ids} reason={divider.reason_code}"
    )
