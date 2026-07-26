from __future__ import annotations

import re

from lecturelog.domain.slides import SlideCatalogEntry

_TOKEN_RE = re.compile(r"[\w+-]{3,}", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def evidence_matches_entry(evidence: str, entry: SlideCatalogEntry) -> bool:
    """Require one coherent slide claim, not arbitrary deck-wide token overlap."""
    normalized_evidence = _normalize(evidence)
    evidence_tokens = set(_tokens(evidence))
    for claim in _claims(entry):
        normalized_claim = _normalize(claim)
        claim_tokens = set(_tokens(claim))
        if not claim_tokens:
            continue
        if (
            len(normalized_claim) >= 5
            and normalized_claim in normalized_evidence
            and (
                len(claim_tokens) >= 2
                or _is_distinctive_singleton(
                    next(iter(claim_tokens)), claim, entry
                )
            )
        ):
            return True
        overlap = evidence_tokens & claim_tokens
        if len(overlap) >= 2:
            return True
        if len(overlap) == 1 and _is_distinctive_singleton(
            next(iter(overlap)), claim, entry
        ):
            return True
    return False


def evidence_specificity(
    evidence: str, entry: SlideCatalogEntry
) -> tuple[int, int, float]:
    """Return a stable ranking key for already-grounded evidence."""
    evidence_tokens = set(_tokens(evidence))
    best = (0, 0, 0.0)
    for claim in _claims(entry):
        claim_tokens = set(_tokens(claim))
        if not claim_tokens:
            continue
        shared = evidence_tokens & claim_tokens
        overlap = len(shared)
        distinctive = (
            overlap == 1
            and _is_distinctive_singleton(next(iter(shared)), claim, entry)
        )
        exact_phrase = (
            len(claim_tokens) >= 2
            and _normalize(claim) in _normalize(evidence)
        )
        evidence_class = 2 if distinctive else 1 if exact_phrase else 0
        best = max(
            best,
            (evidence_class, overlap, overlap / len(claim_tokens)),
        )
    return best


def _claims(entry: SlideCatalogEntry) -> tuple[str, ...]:
    visible_claims = tuple(
        part.strip()
        for part in re.split(r"[\n•;]+", entry.visible_text)
        if part.strip()
    )
    return tuple(
        value
        for value in (
            entry.title or "",
            *entry.source_concepts,
            *entry.transcript_language_terms,
            *entry.formulas,
            *visible_claims,
        )
        if value.strip()
    )


def _is_distinctive_singleton(
    token: str, claim: str, entry: SlideCatalogEntry
) -> bool:
    raw_tokens = _TOKEN_RE.findall(claim)
    if any(
        raw.casefold() == token
        and (
            (len(raw) >= 4 and raw.isupper())
            or any(char.isdigit() for char in raw)
            or "+" in raw
            or "-" in raw
        )
        for raw in raw_tokens
    ):
        return True
    return any(
        len(_tokens(value)) == 1 and _tokens(value)[0] == token
        for value in (*entry.transcript_language_terms, *entry.formulas)
    )


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN_RE.findall(text))


def _normalize(text: str) -> str:
    return _SPACE_RE.sub(" ", text.casefold()).strip()
