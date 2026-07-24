from __future__ import annotations

import math
import re
from collections import Counter

from lecturelog.domain.slides import SectionRef, SlideCandidate, SlideCatalogEntry, TranscriptBlock
from lecturelog.infrastructure.slides.alignment.transcript import blocks_for_section

_TOKEN_RE = re.compile(r"[\w+-]+", re.UNICODE)


def normalize_tokens(text: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN_RE.findall(text) if len(token) > 1)


def _char_ngrams(text: str, size: int = 3) -> set[str]:
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    return {normalized[i : i + size] for i in range(max(0, len(normalized) - size + 1))}


def generate_candidates(
    entry: SlideCatalogEntry,
    sections: tuple[SectionRef, ...],
    blocks: list[TranscriptBlock],
    *,
    limit: int = 5,
    neighbor_radius: int = 1,
) -> tuple[SlideCandidate, ...]:
    query = " ".join(
        filter(
            None,
            [
                entry.title or "",
                entry.visible_text,
                " ".join(entry.source_concepts),
                " ".join(entry.transcript_language_terms),
                " ".join(entry.formulas),
            ],
        )
    )
    query_tokens = Counter(normalize_tokens(query))
    query_ngrams = _char_ngrams(query)
    scored: list[tuple[float, SectionRef, tuple[TranscriptBlock, ...]]] = []
    for section in sections:
        evidence = blocks_for_section(blocks, section)
        text = " ".join(block.text for block in evidence)
        doc_tokens = Counter(normalize_tokens(text))
        lexical = sum(
            min(count, doc_tokens[token]) * (1.0 + math.log1p(len(token)))
            for token, count in query_tokens.items()
        )
        ngrams = _char_ngrams(text)
        char_score = len(query_ngrams & ngrams) / max(len(query_ngrams), 1)
        score = lexical + char_score * 8.0
        scored.append((score, section, evidence))
    ranked = sorted(scored, key=lambda item: (item[0], -item[1].global_section_id), reverse=True)
    top_ids = {section.global_section_id for _, section, _ in ranked[:limit]}
    expanded = {
        section.global_section_id
        for section in sections
        if any(abs(section.global_section_id - top) <= neighbor_radius for top in top_ids)
    }
    result = []
    for score, section, evidence in ranked:
        if section.global_section_id not in expanded:
            continue
        ids = tuple(block.block_id for block in evidence)
        result.append(
            SlideCandidate(
                slide_num=entry.slide_num,
                global_section_id=section.global_section_id,
                evidence_block_ids=ids,
                evidence_quote=None,
                anchor_start_s=evidence[0].start_s if evidence else section.start_s,
                anchor_end_s=evidence[-1].end_s if evidence else section.end_s,
                lexical_score=score,
            )
        )
    return tuple(result[: limit + 2 * neighbor_radius])
