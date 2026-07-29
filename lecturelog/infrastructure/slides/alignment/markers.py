from __future__ import annotations

import re
from dataclasses import dataclass

_MARKER_RE = re.compile(r"<!--\s*slide:\d+\s*-->")
# Префикс пункта списка любого вида: маркеры "-"/"*"/"+", нумерация произвольным
# числом с разделителем "." или ")", а также цитата "> ". Литерал "1. " покрывал
# только первый пункт нумерованного списка — пункты "2.", "3." и далее считались
# обычными абзацами и годились под якорь маркера слайда.
_LIST_PREFIX_RE = re.compile(r"^(?:[-*+]\s|\d+[.)]\s|>\s)")


@dataclass(frozen=True)
class MarkdownBlock:
    text: str
    atomic: bool


def parse_markdown_blocks(markdown: str) -> tuple[MarkdownBlock, ...]:
    """Split only at safe blank-line boundaries; fenced/list/callout blocks stay atomic."""
    lines = markdown.splitlines()
    blocks: list[MarkdownBlock] = []
    current: list[str] = []
    in_fence = False
    atomic = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            atomic = True
        if _LIST_PREFIX_RE.match(stripped):
            atomic = True
        if not line.strip() and not in_fence:
            if current:
                blocks.append(MarkdownBlock("\n".join(current), atomic))
                current, atomic = [], False
            continue
        current.append(line)
    if current:
        blocks.append(MarkdownBlock("\n".join(current), atomic or in_fence))
    return tuple(blocks)


def strip_slide_markers(markdown: str) -> str:
    return _MARKER_RE.sub("", markdown).strip()


def inject_marker(markdown: str, *, slide_num: int, block_index: int, side: str) -> str:
    blocks = list(parse_markdown_blocks(markdown))
    marker_text = f"<!-- slide:{slide_num} -->"
    existing = [block for block in blocks if block.text.strip() == marker_text]
    if len(existing) == 1:
        return markdown.strip()
    if existing:
        raise ValueError("Нарушена уникальность marker")

    content_positions = [
        index for index, block in enumerate(blocks) if not _MARKER_RE.fullmatch(block.text.strip())
    ]
    if not content_positions or not 0 <= block_index < len(content_positions):
        raise ValueError("block_index вне Markdown")
    if side not in {"before", "after"}:
        raise ValueError("side должен быть before/after")
    marker = MarkdownBlock(marker_text, False)
    content_position = content_positions[block_index]
    position = content_position if side == "before" else content_position + 1
    if side == "after":
        while position < len(blocks) and _MARKER_RE.fullmatch(blocks[position].text.strip()):
            position += 1
    blocks.insert(position, marker)
    result = "\n\n".join(block.text for block in blocks).strip()
    if result.count(marker_text) != 1:
        raise ValueError("Нарушена уникальность marker")
    return result
