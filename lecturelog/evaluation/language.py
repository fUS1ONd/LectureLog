from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from lecturelog.evaluation.models import Finding, LanguageAnalysis, NoteBlock, Severity

_CODE_FENCE_RE = re.compile(r"(?ms)^\s*(```|~~~).*?^\s*\1\s*$")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_WIKI_RE = re.compile(r"!?\[\[[^\]]+]]")
_MARKDOWN_LINK_TARGET_RE = re.compile(r"\]\([^)\s]+(?:\s+\"[^\"]*\")?\)")
_FORMULA_RE = re.compile(r"\${1,2}.*?\${1,2}", re.DOTALL)
_IDENTIFIER_RE = re.compile(r"\b(?:[A-Za-z]+[_./:-])+[A-Za-z0-9_.:/-]*\b")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]*")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _natural_text(text: str) -> str:
    for pattern in (
        _CODE_FENCE_RE,
        _INLINE_CODE_RE,
        _URL_RE,
        _WIKI_RE,
        _MARKDOWN_LINK_TARGET_RE,
        _FORMULA_RE,
        _IDENTIFIER_RE,
    ):
        text = pattern.sub(" ", text)
    return re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)


def analyze_language(
    text: str,
    expected_language: str | None = None,
    kind: str = "paragraph",
) -> LanguageAnalysis:
    if kind in {"code", "image", "metadata", "table"}:
        return LanguageAnalysis(None, 0.0, 0, 0, 0, ignored=True, reason=f"{kind}_block")
    natural = _natural_text(text)
    words = _WORD_RE.findall(natural)
    cyrillic_words = sum(bool(_CYRILLIC_RE.search(word)) for word in words)
    latin_words = sum(bool(_LATIN_RE.search(word)) for word in words)
    cyrillic = len(_CYRILLIC_RE.findall(natural))
    latin = len(_LATIN_RE.findall(natural))
    letters = cyrillic + latin
    # A heading or a fragment such as "Feature Creep" is not enough evidence for a
    # language switch. Longer headings are allowed to participate.
    minimum_words = 4 if kind == "heading" else 5
    minimum_letters = 18 if kind == "heading" else 24
    if len(words) < minimum_words or letters < minimum_letters:
        return LanguageAnalysis(
            None,
            0.0,
            cyrillic,
            latin,
            len(words),
            ignored=True,
            reason="short_fragment",
        )
    dominant = max(cyrillic, latin)
    confidence = dominant / letters if letters else 0.0
    detected = "ru" if cyrillic >= latin else "en"
    minority = min(cyrillic, latin)
    minority_words = min(cyrillic_words, latin_words)
    # A handful of API/product names is expected in technical Russian. Mixed prose
    # requires several natural-language words in both scripts.
    is_mixed = minority >= 12 and minority / letters >= 0.22 and minority_words >= 5
    # Expected language does not change detection; it is accepted to make the
    # function directly usable by callers constructing evaluation packets.
    _ = expected_language
    return LanguageAnalysis(
        detected,
        confidence,
        cyrillic,
        latin,
        len(words),
        is_mixed=is_mixed,
    )


def detect_document_language(blocks: tuple[NoteBlock, ...]) -> str | None:
    weights: Counter[str] = Counter()
    votes: Counter[str] = Counter()
    for block in blocks:
        analysis = block.language or analyze_language(block.text, kind=block.kind)
        if analysis.detected and not analysis.ignored and not analysis.is_mixed:
            weights[analysis.detected] += analysis.cyrillic_letters + analysis.latin_letters
            votes[analysis.detected] += 1
    if not weights:
        return None
    language, vote_count = votes.most_common(1)[0]
    if len(votes) > 1 and vote_count == votes.most_common(2)[1][1]:
        language = weights.most_common(1)[0][0]
    if votes[language] < sum(votes.values()) * 0.5:
        return None
    return language


def attach_languages(blocks: tuple[NoteBlock, ...]) -> tuple[NoteBlock, ...]:
    return tuple(
        replace(block, language=analyze_language(block.text, kind=block.kind)) for block in blocks
    )


def language_findings(
    blocks: tuple[NoteBlock, ...],
    expected_language: str | None = None,
) -> tuple[Finding, ...]:
    expected = expected_language or detect_document_language(blocks)
    if expected is None:
        return ()
    findings: list[Finding] = []
    content = [block for block in blocks if block.kind not in {"code", "image", "metadata"}]
    for position, block in enumerate(content):
        analysis = block.language or analyze_language(block.text, kind=block.kind)
        if analysis.ignored or analysis.detected is None:
            continue
        if analysis.is_mixed:
            findings.append(
                Finding(
                    "mixed_language_prose",
                    Severity.WARNING,
                    f"Block {block.block_id} contains substantial {expected}/"
                    f"{analysis.detected} mixed prose.",
                    artifact="конспект.md",
                    block_id=block.block_id,
                    section_id=block.section_id,
                    evidence=(block.text[:240],),
                )
            )
        if analysis.detected != expected and analysis.confidence >= 0.72:
            neighbors = (
                content[max(0, position - 1) : position]
                + content[position + 1 : position + 2]
            )
            isolated = any(
                (neighbor.language or analyze_language(neighbor.text, kind=neighbor.kind)).detected
                == expected
                for neighbor in neighbors
            )
            code = (
                "heading_body_language_mismatch"
                if block.kind == "heading"
                else "unexpected_full_block_language"
            )
            findings.append(
                Finding(
                    code,
                    Severity.MAJOR if isolated or block.kind != "heading" else Severity.WARNING,
                    f"Expected {expected}, detected {analysis.detected} in block "
                    f"{block.block_id} ({analysis.confidence:.0%} confidence).",
                    artifact="конспект.md",
                    block_id=block.block_id,
                    section_id=block.section_id,
                    evidence=(block.text[:240],),
                )
            )
    return tuple(findings)
