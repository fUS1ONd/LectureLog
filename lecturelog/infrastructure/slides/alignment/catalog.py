from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from lecturelog.domain.slides import (
    SlideAsset,
    SlideCatalogEntry,
    SlideCatalogResult,
    SlideRelation,
)
from lecturelog.infrastructure.slides.alignment.schemas import CatalogBatchResponse

MAX_CATALOG_BATCH = 2


def catalog_batches(
    assets: list[SlideAsset], max_batch: int = MAX_CATALOG_BATCH
) -> list[list[SlideAsset]]:
    if not 1 <= max_batch <= MAX_CATALOG_BATCH:
        raise ValueError("catalog batch должен содержать 1..6 страниц")
    return [assets[pos : pos + max_batch] for pos in range(0, len(assets), max_batch)]


def parse_catalog_response(raw: str, expected_slide_nums: Iterable[int]) -> list[SlideCatalogEntry]:
    parsed = CatalogBatchResponse.model_validate_json(raw)
    expected = tuple(expected_slide_nums)
    actual = tuple(entry.slide_num for entry in parsed.slides)
    if actual != expected:
        raise ValueError(f"Ожидались слайды {expected}, получены {actual}")
    return [
        SlideCatalogEntry(
            slide_num=item.slide_num,
            role=item.role,
            title=item.title,
            visible_text=item.visible_text,
            source_concepts=tuple(item.source_concepts),
            transcript_language_terms=tuple(item.transcript_language_terms),
            visual_summary=item.visual_summary,
            formulas=tuple(item.formulas),
            proper_nouns=tuple(item.proper_nouns),
        )
        for item in parsed.slides
    ]


def detect_boilerplate_lines(
    assets: list[SlideAsset], *, min_share: float = 0.6, min_pages: int = 3
) -> frozenset[str]:
    """Строки, повторяющиеся на большинстве страниц колоды (колонтитулы).

    Такая строка описывает не содержание конкретной страницы, а всю колоду,
    поэтому в качестве доказательства она бесполезна: совпадает с любой репликой,
    где лектор произносит название курса.
    """
    if len(assets) < min_pages:
        return frozenset()
    pages_with_line: dict[str, int] = {}
    for asset in assets:
        for line in {line.strip() for line in (asset.extracted_text or "").splitlines()}:
            if line:
                pages_with_line[line] = pages_with_line.get(line, 0) + 1
    threshold = max(min_share * len(assets), 2)
    return frozenset(line for line, pages in pages_with_line.items() if pages >= threshold)


# Имена собственные в нативном тексте: латиница, аббревиатуры, токены с цифрами
# и точками (ENIAC, HwProj, SWEBOK, hwproj.ru, C++). Русские имена эвристикой не
# берём — по заглавной букве их не отличить от первого слова строки.
_PROPER_NOUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]{2,}|[A-ZА-ЯЁ]{3,}")


def extract_proper_nouns(text: str, *, limit: int = 40) -> tuple[str, ...]:
    """Написания имён со страницы — запасной словарь, если каталог деградировал."""
    found = dict.fromkeys(match.strip("._-") for match in _PROPER_NOUN_RE.findall(text or ""))
    return tuple(item for item in found if len(item) >= 3)[:limit]


def native_text_fallback(
    asset: SlideAsset, *, boilerplate: frozenset[str] = frozenset()
) -> SlideCatalogResult:
    text = (asset.extracted_text or "").strip()
    if not text:
        return SlideCatalogResult(asset.slide_num, "unresolved", None)
    all_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in all_lines if line not in boilerplate]
    if not lines:
        # Все строки страницы сочтены колонтитулом (например, страница-разделитель,
        # на которой напечатан только заголовок колоды, или промежуточный шаг
        # progressive build, чьи строки повторяются на последующих слайдах).
        #
        # Каталожную запись страница сохраняет: без неё она молча выпадает из
        # конспекта (решение задачи 12). Но восстановленные строки не годятся в
        # доказательство привязки — по определению самой detect_boilerplate_lines
        # они совпадают с любой репликой, где лектор произносит название курса.
        # Поэтому наполняем только title (человеку он говорит, что это за
        # страница) и proper_nouns (справочник написаний для рендера): ни то, ни
        # другое доказательством не станет, потому что роль blank выводит
        # страницу из матчинга целиком. Оставить строки в title при роли content
        # недостаточно: retrieval строит по title запрос, а grounding — claim.
        entry = SlideCatalogEntry(
            slide_num=asset.slide_num,
            role="blank",
            title=all_lines[0][:300],
            visible_text="",
            source_concepts=(),
            proper_nouns=extract_proper_nouns(text),
        )
        return SlideCatalogResult(asset.slide_num, "native_text_fallback", entry)
    entry = SlideCatalogEntry(
        slide_num=asset.slide_num,
        role="content",
        title=lines[0][:300] if lines else None,
        visible_text="\n".join(lines)[:6000],
        source_concepts=tuple(lines[:12]),
        proper_nouns=extract_proper_nouns(text),
    )
    return SlideCatalogResult(asset.slide_num, "native_text_fallback", entry)


def detect_exact_duplicates(assets: list[SlideAsset]) -> tuple[SlideRelation, ...]:
    seen: dict[str, int] = {}
    relations: list[SlideRelation] = []
    for asset in assets:
        digest = hashlib.sha256(asset.path.read_bytes()).hexdigest()
        canonical = seen.setdefault(digest, asset.slide_num)
        if canonical != asset.slide_num:
            relations.append(
                SlideRelation(asset.slide_num, "exact_duplicate", digest[:12], canonical)
            )
    return tuple(relations)


def detect_progressive_builds(
    assets: list[SlideAsset],
    *,
    containment_threshold: float = 0.72,
) -> tuple[SlideRelation, ...]:
    """Detect adjacent pages where the latter adds material to the former.

    This is deliberately conservative: progressive pages are *related*, not
    duplicates, and therefore must remain eligible for placement.
    """
    relations: list[SlideRelation] = []
    for previous, current in zip(assets, assets[1:], strict=False):
        previous_tokens = _catalog_tokens(previous.extracted_text or "")
        current_tokens = _catalog_tokens(current.extracted_text or "")
        if len(previous_tokens) < 4 or len(current_tokens) <= len(previous_tokens):
            continue
        containment = len(previous_tokens & current_tokens) / len(previous_tokens)
        if containment >= containment_threshold:
            relations.append(
                SlideRelation(
                    current.slide_num,
                    "progressive_build",
                    f"progressive:{previous.slide_num}",
                    previous.slide_num,
                )
            )
    return tuple(relations)


def _catalog_tokens(text: str) -> set[str]:
    return {token.casefold() for token in text.replace("\n", " ").split() if len(token) >= 3}
