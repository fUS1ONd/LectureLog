from __future__ import annotations

import re

from lecturelog.domain.slides import TranscriptBlock

_TIMELINE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{3})"
)


def parse_srt_blocks(srt: str) -> list[TranscriptBlock]:
    """Parse SRT once into stable, 1-based evidence blocks."""
    normalized = srt.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    result: list[TranscriptBlock] = []
    for raw_block in re.split(r"\n\s*\n+", normalized):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        timeline_pos = next((i for i, line in enumerate(lines) if _TIMELINE_RE.search(line)), None)
        if timeline_pos is None:
            continue
        match = _TIMELINE_RE.search(lines[timeline_pos])
        assert match is not None
        text = " ".join(lines[timeline_pos + 1 :]).strip()
        if not text:
            continue
        result.append(
            TranscriptBlock(
                block_id=len(result) + 1,
                start_s=parse_srt_time(match.group("start")),
                end_s=parse_srt_time(match.group("end")),
                text=text,
            )
        )
    return result


def extract_plain_text(srt: str) -> str:
    """Извлекает чистый текст из SRT, убирая нумерацию и таймкоды."""
    return " ".join(block.text for block in parse_srt_blocks(srt))


def srt_to_plain_text(srt: str) -> str:
    """Преобразует SRT в plain-text: одна строка на блок, без номеров и таймкодов.

    Многострочные подписи внутри блока склеиваются через пробел.
    Между блоками — перевод строки.
    """
    return "\n".join(block.text for block in parse_srt_blocks(srt))


def parse_srt_time(time_str: str) -> float:
    """Переводит таймкод SRT (ЧЧ:ММ:СС,МСС или ММ:СС,МСС) в секунды."""
    time_str = time_str.replace(",", ".")
    parts = time_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def format_time(time_str: str) -> str:
    """Нормализует формат таймкода до ЧЧ:ММ:СС."""
    return time_str.split(",")[0].split(".")[0]


def extract_srt_fragment(srt: str, start: str, end: str) -> str:
    """Вырезает фрагмент SRT по таймкодам."""
    start_sec = parse_srt_time(start.replace(".", ",") if "," not in start else start)
    end_sec = parse_srt_time(end.replace(".", ",") if "," not in end else end)

    result = []
    for block in parse_srt_blocks(srt):
        if block.end_s >= start_sec and block.start_s <= end_sec:
            result.append(
                f"{block.block_id}\n"
                f"{_format_srt_seconds(block.start_s)} --> {_format_srt_seconds(block.end_s)}\n"
                f"{block.text}"
            )
    return "\n\n".join(result)


def _format_srt_seconds(value: float) -> str:
    milliseconds = round(value * 1000)
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
