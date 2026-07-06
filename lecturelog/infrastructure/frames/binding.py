"""Стадия G: привязка кадров к секциям structurize по таймкодам (дизайн §5.G).

Кадр рождается с таймстемпом; привязка = поиск секции по интервалу +
монотонизация (кадры не скачут назад по секциям). LLM-матчинг не нужен —
он остаётся только документным слайдам."""

from __future__ import annotations

import bisect

from lecturelog.domain.models import Topic
from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.srt import parse_srt_time


def bind_frames_to_sections(items: list[SlideImage], topics: list[Topic]) -> None:
    """Проставить section.slide_indices (1-based, в порядке items по ts).

    items должны быть отсортированы по timestamp (провайдер это гарантирует);
    поэтому монотонность следует из монотонности интервалов секций, отдельный
    прижим prev_section — страховка от пересекающихся интервалов LLM."""
    if any(item.timestamp is None for item in items):
        raise ValueError("привязка по таймкодам требует timestamp у каждого кадра")
    sections = [s for t in topics for s in t.sections]
    if not sections:
        return
    starts = [parse_srt_time(s.start) for s in sections]

    prev_idx = 0
    for order, item in enumerate(sorted(items, key=lambda x: x.timestamp), start=1):
        # Последняя секция, начавшаяся не позже ts; до первой секции → секция 0
        idx = max(0, bisect.bisect_right(starts, item.timestamp) - 1)
        idx = max(idx, prev_idx)  # монотонизация
        idx = min(idx, len(sections) - 1)  # хвост за последней секцией → последняя
        sections[idx].slide_indices.append(order)
        prev_idx = idx

    # Продублировать привязку на уровень тем (как делает structurizer для документов)
    for topic in topics:
        acc: list[int] = []
        for section in topic.sections:
            acc.extend(section.slide_indices)
        topic.slide_indices = sorted(set(acc))
