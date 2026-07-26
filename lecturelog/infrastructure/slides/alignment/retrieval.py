from __future__ import annotations

import math
import re
from collections import Counter

from lecturelog.domain.slides import SectionRef, SlideCandidate, SlideCatalogEntry, TranscriptBlock
from lecturelog.infrastructure.slides.alignment.transcript import blocks_for_section

_TOKEN_RE = re.compile(r"[\w+-]+", re.UNICODE)
_BM25_K1 = 1.2
_BM25_B = 0.75
_MAX_EVIDENCE_BLOCKS = 6


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
    query_parts = tuple(
        filter(
            None,
            (
                entry.title or "",
                *entry.source_concepts,
                *entry.transcript_language_terms,
                *entry.formulas,
                *(
                    part.strip()
                    for part in re.split(r"[\n•;]+", entry.visible_text)
                    if part.strip()
                ),
            ),
        )
    )
    query_tokens = set(normalize_tokens(" ".join(query_parts)))
    section_documents = [
        (section, blocks_for_section(blocks, section))
        for section in sections
    ]
    section_counters = [
        Counter(normalize_tokens(" ".join(block.text for block in evidence)))
        for _, evidence in section_documents
    ]
    document_count = max(len(section_counters), 1)
    average_length = (
        sum(sum(counter.values()) for counter in section_counters) / document_count
    )
    document_frequency = {
        token: sum(token in counter for counter in section_counters)
        for token in query_tokens
    }
    idf = {
        token: math.log(
            1.0
            + (document_count - frequency + 0.5) / (frequency + 0.5)
        )
        for token, frequency in document_frequency.items()
    }
    scored: list[tuple[float, SectionRef, tuple[TranscriptBlock, ...]]] = []
    for (section, evidence), doc_tokens in zip(
        section_documents, section_counters, strict=True
    ):
        text = " ".join(block.text for block in evidence)
        lexical = _bm25_score(
            doc_tokens,
            query_tokens,
            idf,
            average_length,
        )
        char_score = max(
            (_ngram_overlap(part, text) for part in query_parts),
            default=0.0,
        )
        score = lexical + char_score * 2.0
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
        selected_evidence = _best_evidence_blocks(
            evidence,
            query_tokens,
            query_parts,
            idf,
            average_length,
        )
        ids = tuple(block.block_id for block in selected_evidence)
        result.append(
            SlideCandidate(
                slide_num=entry.slide_num,
                global_section_id=section.global_section_id,
                evidence_block_ids=ids,
                evidence_quote=None,
                anchor_start_s=(
                    selected_evidence[0].start_s
                    if selected_evidence
                    else section.start_s
                ),
                anchor_end_s=(
                    selected_evidence[-1].end_s
                    if selected_evidence
                    else section.end_s
                ),
                lexical_score=score,
            )
        )
    return tuple(result[: limit + 2 * neighbor_radius])


def _bm25_score(
    document: Counter[str],
    query_tokens: set[str],
    idf: dict[str, float],
    average_length: float,
) -> float:
    length = sum(document.values())
    normalization = _BM25_K1 * (
        1.0 - _BM25_B + _BM25_B * length / max(average_length, 1.0)
    )
    return sum(
        idf[token]
        * document[token]
        * (_BM25_K1 + 1.0)
        / (document[token] + normalization)
        for token in query_tokens
        if document[token]
    )


def _ngram_overlap(query: str, document: str) -> float:
    query_ngrams = _char_ngrams(query)
    if not query_ngrams:
        return 0.0
    return len(query_ngrams & _char_ngrams(document)) / len(query_ngrams)


def _best_evidence_blocks(
    evidence: tuple[TranscriptBlock, ...],
    query_tokens: set[str],
    query_parts: tuple[str, ...],
    idf: dict[str, float],
    average_length: float,
) -> tuple[TranscriptBlock, ...]:
    ranked = sorted(
        evidence,
        key=lambda block: (
            _bm25_score(
                Counter(normalize_tokens(block.text)),
                query_tokens,
                idf,
                average_length,
            )
            + max(
                (_ngram_overlap(part, block.text) for part in query_parts),
                default=0.0,
            ),
            -block.start_s,
        ),
        reverse=True,
    )
    selected = ranked[:_MAX_EVIDENCE_BLOCKS]
    return tuple(sorted(selected, key=lambda block: (block.start_s, block.block_id)))
