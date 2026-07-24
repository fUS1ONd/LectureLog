from __future__ import annotations

import re
from dataclasses import dataclass

_MARKER_RE = re.compile(r"<!--\s*slide:\d+\s*-->")


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
        if stripped.startswith(("- ", "* ", "+ ", "> ", "1. ")):
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
    blocks = list(parse_markdown_blocks(strip_slide_markers(markdown)))
    if not blocks or not 0 <= block_index < len(blocks):
        raise ValueError("block_index вне Markdown")
    if side not in {"before", "after"}:
        raise ValueError("side должен быть before/after")
    marker = MarkdownBlock(f"<!-- slide:{slide_num} -->", False)
    position = block_index if side == "before" else block_index + 1
    blocks.insert(position, marker)
    result = "\n\n".join(block.text for block in blocks).strip()
    if result.count(f"<!-- slide:{slide_num} -->") != 1:
        raise ValueError("Нарушена уникальность marker")
    return result

