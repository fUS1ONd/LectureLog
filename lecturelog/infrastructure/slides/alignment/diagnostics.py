from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from lecturelog.domain.slides import SlideAssignment, SlidePlacement

SCHEMA_VERSION = 1


def write_diagnostic(
    path: Path,
    *,
    mode: str,
    assignments: tuple[SlideAssignment, ...],
    placements: tuple[SlidePlacement, ...] = (),
    prompt_versions: dict[str, str] | None = None,
) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "prompt_versions": prompt_versions or {},
        "assignments": [asdict(assignment) for assignment in assignments],
        "placements": [asdict(placement) for placement in placements],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
