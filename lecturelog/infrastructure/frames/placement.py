"""Расстановка маркеров слайдов внутри текста секций (дизайн 2026-07-07).

Позиция кадра в content вычисляется детерминированно, без LLM: интервал
секции распределяется по абзацам пропорционально их длине в символах
(время абзаца ~ его доля текста), кадр по своему timestamp попадает
в абзац, после которого вставляется маркер ``<!-- slide:N -->``.

N — тот же глобальный 1-based номер кадра, что и в Section.slide_indices.
Web режет content_md по маркерам; старые рендеры их не видят (HTML-коммент).
"""

from __future__ import annotations

import bisect

from lecturelog.domain.models import Topic
from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.srt import parse_srt_time

MARKER_TEMPLATE = "<!-- slide:{n} -->"


def split_paragraphs(content: str) -> list[str]:
    """Разбить markdown на блоки верхнего уровня по пустым строкам.

    Код-фенсы (```...```) не рвутся, даже если внутри пустые строки.
    Единственная реализация сегментации в системе — web о ней не знает,
    он режет готовые маркеры."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in content.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def place_slides_in_sections(items: list[SlideImage], topics: list[Topic]) -> None:
    """Вставить маркеры кадров в content секций по slide_indices и timestamp.

    Вызывается после bind_frames_to_sections: slide_indices уже проставлены,
    номер N = позиция кадра в items, отсортированных по timestamp (та же
    нумерация, что в binding)."""
    ordered = sorted(items, key=lambda x: x.timestamp or 0.0)
    for topic in topics:
        for section in topic.sections:
            if not section.slide_indices:
                continue
            paragraphs = split_paragraphs(section.content)
            # Кумулятивные границы абзацев на временной шкале секции,
            # взвешенные длиной абзаца в символах.
            start = parse_srt_time(section.start)
            end = parse_srt_time(section.end)
            total_chars = sum(len(p) for p in paragraphs)
            bounds: list[float] = []  # конец интервала каждого абзаца
            if paragraphs and total_chars > 0 and end > start:
                acc = 0
                for p in paragraphs:
                    acc += len(p)
                    bounds.append(start + (end - start) * acc / total_chars)

            # Маркеры после абзаца: after[i] — номера кадров после i-го абзаца
            after: dict[int, list[int]] = {}
            for n in section.slide_indices:
                ts = ordered[n - 1].timestamp or 0.0
                # Первый абзац, чей интервал ещё не закончился к ts;
                # ts за концом секции (монотонизация) -> последний абзац.
                idx = bisect.bisect_right(bounds, ts) if bounds else 0
                idx = min(idx, len(paragraphs) - 1) if paragraphs else -1
                after.setdefault(idx, []).append(n)

            pieces: list[str] = []
            if not paragraphs:
                pieces = [MARKER_TEMPLATE.format(n=n) for n in after.get(-1, [])]
            else:
                for i, p in enumerate(paragraphs):
                    pieces.append(p)
                    pieces.extend(MARKER_TEMPLATE.format(n=n) for n in after.get(i, []))
            section.content = "\n\n".join(pieces)
