"""Точки касания VLM (дизайн §5.C, §5.F): классификация режимов и QC кадров.

Оба вызова батчевые (≤ vlm_batch картинок), JSON-mode, flash-lite первым в
fallback-списке. Ошибки VLM НЕ пробрасываются политикам — вызывающий код
(provider) деградирует до временных сигнатур / пропуска QC (дизайн §10)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from lecturelog.infrastructure.frames.types import FramesTuning, Regime

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path("prompts")
_MIN_BBOX_AREA = 0.10


def _encode_jpeg(gray_or_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", gray_or_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("не удалось закодировать кадр в JPEG")
    return buf.tobytes()


def _parse_json(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines() if not line.startswith("```")
        ).strip()
    return json.loads(text)


def _valid_bbox(bbox: Any) -> tuple[float, float, float, float] | None:
    """Неправдоподобный bbox (площадь < 10% или выход за кадр) → None (полный кадр)."""
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return None
    try:
        x, y, w, h = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
        return None
    if x + w > 1.001 or y + h > 1.001 or w * h < _MIN_BBOX_AREA:
        return None
    return (x, y, w, h)


async def classify_regimes(
    llm: Any,
    models: list[str],
    effort: str,
    regimes: list[Regime],
    rep_frames: list[np.ndarray],
    micro_rate: list[float],
    tuning: FramesTuning,
    on_usage: Any = None,
    prompts_dir: Path = _PROMPTS_DIR,
) -> list[Regime]:
    """VLM уточняет/перебивает предварительный тип из сегментации.

    Тай-брейкер (дизайн §5.C): «слайды с кодом» не печатают — если VLM сказал
    code, а micro_rate режима низкий, оставляем slides."""
    prompt = (prompts_dir / "frames_classify_v1.md").read_text(encoding="utf-8")
    for start in range(0, len(regimes), tuning.vlm_batch):
        batch = regimes[start : start + tuning.vlm_batch]
        images = [_encode_jpeg(f) for f in rep_frames[start : start + tuning.vlm_batch]]
        raw = await llm.call(
            prompt=prompt, models=models, images=images,
            on_usage=on_usage, response_json=True, effort=effort,
        )
        verdicts = _parse_json(raw)
        if not isinstance(verdicts, list):
            raise ValueError("ответ классификации режимов должен быть JSON-массивом")
        by_idx = {int(v.get("idx", 0)): v for v in verdicts if isinstance(v, dict)}
        for offset, regime in enumerate(batch):
            v = by_idx.get(offset + 1)
            if v is None:
                continue
            vlm_kind = str(v.get("type", regime.kind))
            if vlm_kind in ("code", "terminal") and micro_rate[start + offset] < 0.2:
                vlm_kind = regime.kind  # временнáя сигнатура — тай-брейкер
            regime.kind = vlm_kind
            regime.bbox = _valid_bbox(v.get("content_bbox"))
            bk = str(v.get("board_kind", "none"))
            regime.board_kind = bk if bk in ("chalk", "marker") else "none"
    return regimes
