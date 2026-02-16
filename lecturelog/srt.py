from __future__ import annotations

import re


def extract_plain_text(srt: str) -> str:
    """Извлекает чистый текст из SRT, убирая нумерацию и таймкоды."""
    lines: list[str] = []
    for line in srt.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->", line):
            continue
        lines.append(line)
    return " ".join(lines)


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

    blocks = re.split(r"\n\n+", srt.strip())
    result: list[str] = []

    for block in blocks:
        time_match = re.search(
            r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})",
            block,
        )
        if not time_match:
            continue

        block_start = parse_srt_time(time_match.group(1))
        block_end = parse_srt_time(time_match.group(2))
        if block_end >= start_sec and block_start <= end_sec:
            result.append(block)

    return "\n\n".join(result)
