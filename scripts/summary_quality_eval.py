#!/usr/bin/env python3
"""Оценка соответствия переработанного конспекта исходному тексту."""

from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
TIMECODE_RE = re.compile(r"\[[0-9]{2}:[0-9]{2}:[0-9]{2}\s*-\s*[0-9]{2}:[0-9]{2}:[0-9]{2}\]")
SRT_TIMECODE_LINE_RE = re.compile(
    r"^\s*\d{2}:\d{2}:\d{2}[,.:]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.:]\d{3}.*$",
    flags=re.M,
)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _f1(precision: float, recall: float) -> float:
    if (precision + recall) == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _clean_markdown(text: str) -> str:
    # Удаляем только служебную markdown-разметку, сохраняя основное содержание.
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^---\s*$", " ", text, flags=re.M)
    text = TIMECODE_RE.sub(" ", text)
    text = re.sub(r"[*_`>#]+", " ", text)
    return text


def _clean_source_text(text: str) -> str:
    # Удаляем типовые артефакты SRT и оставляем только текст реплик.
    text = SRT_TIMECODE_LINE_RE.sub(" ", text)
    text = re.sub(r"^\s*\d+\s*$", " ", text, flags=re.M)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokens(text: str) -> List[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def _sentence_set(text: str) -> set[str]:
    sentences = [s.strip().lower() for s in SENT_SPLIT_RE.split(text) if s.strip()]

    def normalize(sentence: str) -> str:
        sentence = re.sub(r"[^A-Za-zА-Яа-яЁё0-9 ]+", " ", sentence)
        sentence = re.sub(r"\s+", " ", sentence)
        return sentence.strip()

    return {normalize(sentence) for sentence in sentences if normalize(sentence)}


def _ngram_counter(tokens: List[str], n: int) -> Counter[Tuple[str, ...]]:
    if len(tokens) < n:
        return Counter()
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def compute_metrics(source_text: str, candidate_text: str) -> Dict[str, float]:
    source_clean = _clean_source_text(source_text)
    source_words = _tokens(source_clean)
    candidate_clean = _clean_markdown(candidate_text)
    candidate_words = _tokens(candidate_clean)

    matcher = difflib.SequenceMatcher(None, source_words, candidate_words, autojunk=False)
    equal_words = 0
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag == "equal":
            equal_words += i2 - i1

    source_vocab = set(source_words)
    candidate_vocab = set(candidate_words)
    vocab_union = source_vocab | candidate_vocab

    source_unigrams = _ngram_counter(source_words, 1)
    candidate_unigrams = _ngram_counter(candidate_words, 1)
    source_bigrams = _ngram_counter(source_words, 2)
    candidate_bigrams = _ngram_counter(candidate_words, 2)

    overlap_uni = sum((source_unigrams & candidate_unigrams).values())
    overlap_bi = sum((source_bigrams & candidate_bigrams).values())

    rouge1_recall = _safe_div(overlap_uni, sum(source_unigrams.values()))
    rouge1_precision = _safe_div(overlap_uni, sum(candidate_unigrams.values()))
    rouge2_recall = _safe_div(overlap_bi, sum(source_bigrams.values()))
    rouge2_precision = _safe_div(overlap_bi, sum(candidate_bigrams.values()))

    source_sentences = _sentence_set(source_clean)
    candidate_sentences = _sentence_set(candidate_clean)
    sentence_overlap = len(source_sentences & candidate_sentences)

    compression_pct = (1 - _safe_div(len(candidate_words), len(source_words))) * 100.0
    compression_penalty_component = max(0.0, 100.0 - abs(compression_pct) * 2.0)

    base_words_preserved_pct = _safe_div(equal_words, len(source_words)) * 100.0
    candidate_words_aligned_pct = _safe_div(equal_words, len(candidate_words)) * 100.0

    word_seq_ratio_pct = matcher.ratio() * 100.0
    fidelity_score = (
        0.30 * base_words_preserved_pct
        + 0.25 * (rouge1_recall * 100.0)
        + 0.20 * (rouge2_recall * 100.0)
        + 0.20 * word_seq_ratio_pct
        + 0.05 * compression_penalty_component
    )

    avg_sentence_words = _safe_div(len(candidate_words), max(1, len(candidate_sentences)))
    readability_score = max(0.0, 100.0 - abs(avg_sentence_words - 16.0) * 4.0)

    rewrite_aggressiveness = min(
        100.0,
        (100.0 - base_words_preserved_pct) * 0.6
        + max(0.0, compression_pct) * 0.3
        + _safe_div(len(candidate_vocab - source_vocab), max(1, len(candidate_vocab))) * 100.0 * 0.1,
    )

    return {
        "chars_source": float(len(source_text)),
        "chars_candidate": float(len(candidate_text)),
        "words_source": float(len(source_words)),
        "words_candidate": float(len(candidate_words)),
        "word_seq_ratio_to_source": matcher.ratio(),
        "base_words_preserved_pct": base_words_preserved_pct,
        "candidate_words_aligned_pct": candidate_words_aligned_pct,
        "compression_vs_source_words_pct": compression_pct,
        "vocab_jaccard_to_source": _safe_div(len(source_vocab & candidate_vocab), len(vocab_union)),
        "novel_vocab_pct": _safe_div(len(candidate_vocab - source_vocab), max(1, len(candidate_vocab))) * 100.0,
        "sentence_exact_overlap_pct": _safe_div(sentence_overlap, max(1, len(candidate_sentences))) * 100.0,
        "rouge1_recall": rouge1_recall,
        "rouge1_precision": rouge1_precision,
        "rouge1_f1": _f1(rouge1_precision, rouge1_recall),
        "rouge2_recall": rouge2_recall,
        "rouge2_precision": rouge2_precision,
        "rouge2_f1": _f1(rouge2_precision, rouge2_recall),
        "fidelity_score": fidelity_score,
        "rewrite_aggressiveness": rewrite_aggressiveness,
        "readability_score": readability_score,
    }


def rank_candidates(source_text: str, candidates: Dict[str, str]) -> List[Dict[str, object]]:
    ranked: List[Dict[str, object]] = []
    for name, text in candidates.items():
        metrics = compute_metrics(source_text, text)
        ranked.append({"name": name, "metrics": metrics})

    ranked.sort(key=lambda item: item["metrics"]["fidelity_score"], reverse=True)
    return ranked


def _format_float(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _build_table(rows: List[Dict[str, object]]) -> str:
    headers = [
        "candidate",
        "fidelity",
        "preserved_%",
        "compression_%",
        "rouge1_recall",
        "rouge2_recall",
        "rewrite_%",
        "readability",
    ]

    lines = [" | ".join(headers), " | ".join(["---"] * len(headers))]
    for row in rows:
        m = row["metrics"]
        lines.append(
            " | ".join(
                [
                    str(row["name"]),
                    _format_float(m["fidelity_score"]),
                    _format_float(m["base_words_preserved_pct"]),
                    _format_float(m["compression_vs_source_words_pct"]),
                    _format_float(m["rouge1_recall"] * 100.0),
                    _format_float(m["rouge2_recall"] * 100.0),
                    _format_float(m["rewrite_aggressiveness"]),
                    _format_float(m["readability_score"]),
                ]
            )
        )
    return "\n".join(lines)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сравнение качества переработанных конспектов относительно исходного текста"
    )
    parser.add_argument("--source", required=True, help="Путь к исходному .txt/.srt/.md")
    parser.add_argument("--candidates", nargs="+", required=True, help="Пути к версиям конспекта")
    parser.add_argument("--json", action="store_true", help="Вывести полный результат в JSON")

    args = parser.parse_args(list(argv) if argv is not None else None)

    source_path = Path(args.source)
    candidate_paths = [Path(p) for p in args.candidates]

    source_text = _read_text(source_path)
    candidates = {str(path): _read_text(path) for path in candidate_paths}

    ranked = rank_candidates(source_text, candidates)

    if args.json:
        print(json.dumps(ranked, ensure_ascii=False, indent=2))
        return 0

    print(_build_table(ranked))
    print()
    print(f"Лидер по fidelity_score: {ranked[0]['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
