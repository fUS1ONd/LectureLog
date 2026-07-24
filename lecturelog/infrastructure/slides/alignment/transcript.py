from __future__ import annotations

from lecturelog.domain.models import Topic
from lecturelog.domain.slides import SectionRef, TranscriptBlock
from lecturelog.infrastructure.srt import parse_srt_time


def build_section_refs(topics: list[Topic]) -> tuple[SectionRef, ...]:
    refs: list[SectionRef] = []
    previous_start = -1.0
    for topic_index, topic in enumerate(topics):
        for local_index, section in enumerate(topic.sections):
            start = parse_srt_time(section.start)
            end = parse_srt_time(section.end)
            if end < start or start < previous_start:
                raise ValueError("Некорректная или немонотонная шкала sections")
            refs.append(SectionRef(len(refs), topic_index, local_index, start, end))
            previous_start = start
    return tuple(refs)


def blocks_for_section(
    blocks: list[TranscriptBlock], section: SectionRef
) -> tuple[TranscriptBlock, ...]:
    return tuple(
        block
        for block in blocks
        if block.end_s >= section.start_s and block.start_s <= section.end_s
    )

