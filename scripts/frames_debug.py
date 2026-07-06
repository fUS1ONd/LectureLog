#!/usr/bin/env python3
"""Прогон стадии кадров на реальном видео с дампом отладки (задача 18, §13).

Использование:
    uv run python scripts/frames_debug.py lecture.mp4 lecture.srt out/
    uv run python scripts/frames_debug.py lecture.mp4 lecture.srt out/ --no-vlm

Пишет в out/:
    thumbs/           — тумбы стадии A (пишет ThumbStore по мере прохода)
    regimes.json       — таймлайн режимов после сегментации B (и VLM C, если включён)
    signals.npz        — кривые mad/motion/edge/shift для построения графиков
    candidates.json     — кандидаты стадии D (до/после cap по бюджету кадров)
    <кадры>.jpg         — финальные кадры стадии E (+ QC F, если включён VLM)

Без --no-vlm LlmClient собирается из окружения (OPENROUTER_API_KEY/
OPENROUTER_BASE_URL, LLM_MODELS_VIDEO_SLIDES, LLM_EFFORT_VIDEO_SLIDES) —
те же переменные, что использует прод (lecturelog.config.settings.FramesConfig).
С --no-vlm стадии C/F пропускаются — чистый CV-прогон без сети и без токенов."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

import numpy as np

from lecturelog.infrastructure.frames.board import board_candidates
from lecturelog.infrastructure.frames.coding import coding_candidates_from_frames
from lecturelog.infrastructure.frames.extract import render_candidates
from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore, decode_gray
from lecturelog.infrastructure.frames.provider import VideoFrameProvider, _parse_srt_blocks
from lecturelog.infrastructure.frames.segmentation import segment_regimes
from lecturelog.infrastructure.frames.signals import compute_signals
from lecturelog.infrastructure.frames.slides_policy import slide_candidates
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning

logger = logging.getLogger("frames_debug")


class _StageTimer:
    """Копит длительности стадий и расход токенов для итоговой сводки."""

    def __init__(self) -> None:
        self.durations: dict[str, float] = {}
        self.usage: list[dict] = []

    def track(self, name: str):
        return _StageContext(self, name)

    def on_usage(self, payload: dict) -> None:
        self.usage.append(payload)


class _StageContext:
    def __init__(self, timer: _StageTimer, name: str) -> None:
        self._timer = timer
        self._name = name

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        self._timer.durations[self._name] = time.monotonic() - self._t0


def _candidate_to_dict(c: Candidate) -> dict:
    return {
        "ts": c.ts,
        "kind": c.kind,
        "source": c.source,
        "score": c.score,
        "pair_ts": c.pair_ts,
        "regime_kind": c.regime.kind if c.regime else None,
    }


def _collect_candidates(
    video: Path, regimes, track, store, srt_blocks, tuning: FramesTuning
) -> list[Candidate]:
    """Дословная копия provider._collect_candidates — тут нужна раздельная
    точка останова для дампа, а не приватный метод экземпляра провайдера."""
    out: list[Candidate] = []
    for regime in regimes:
        if regime.kind == "slides":
            out.extend(slide_candidates(regime, track, store, tuning))
        elif regime.kind == "board":
            out.extend(board_candidates(regime, track, store, tuning))
        elif regime.kind in ("code", "terminal"):
            frames = list(
                decode_gray(
                    video,
                    fps=tuning.code_fps,
                    width=tuning.code_width,
                    start_s=regime.start_s,
                    end_s=regime.end_s,
                )
            )
            out.extend(
                coding_candidates_from_frames(
                    frames, fps=tuning.code_fps, regime=regime, tuning=tuning, srt_blocks=srt_blocks
                )
            )
        elif regime.kind == "camera":
            out.extend(slide_candidates(regime, track, store, tuning))
    return sorted(out, key=lambda c: c.ts)


def _build_llm(models_arg: str | None, effort_arg: str | None):
    """LlmClient из окружения (см. FramesConfig) — так же, как в lifespan.py."""
    from openai import AsyncOpenAI

    from lecturelog.config.settings import get_config
    from lecturelog.infrastructure.llm.llm_client import LlmClient
    from lecturelog.infrastructure.llm.model_cooldown import ModelCooldown

    cfg = get_config()
    openai_client = AsyncOpenAI(base_url=cfg.llm.base_url, api_key=cfg.llm.openrouter_key)
    llm = LlmClient(openai_client, ModelCooldown())
    models = models_arg.split(",") if models_arg else cfg.frames.models
    effort = effort_arg or cfg.frames.effort
    return llm, models, effort


async def run(args: argparse.Namespace) -> None:
    video, srt, out_dir = Path(args.video), Path(args.srt), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tuning = FramesTuning()
    timer = _StageTimer()

    srt_blocks = _parse_srt_blocks(srt.read_text(encoding="utf-8"))

    # A: грубый проход
    store = ThumbStore(out_dir / "thumbs")
    with timer.track("A_signals"):
        track = compute_signals(
            decode_gray(video, fps=tuning.analysis_fps, width=tuning.analysis_width),
            fps=tuning.analysis_fps,
            thumbs=store,
            ignore_bottom_frac=tuning.subtitle_band_frac,
        )
    np.savez(
        out_dir / "signals.npz",
        mad=track.mad,
        motion_frac=track.motion_frac,
        edge=track.edge,
        shift=track.shift,
        fps=track.fps,
    )

    # B: сегментация
    with timer.track("B_segmentation"):
        regimes = segment_regimes(track, tuning)

    llm = models = effort = None
    if not args.no_vlm:
        llm, models, effort = _build_llm(args.models, args.effort)

        # C: VLM-классификация режимов
        from lecturelog.infrastructure.frames import vlm

        with timer.track("C_vlm_classify"):
            reps, micro = VideoFrameProvider._representatives(
                VideoFrameProvider(video, srt, llm, models, effort, tuning),
                regimes,
                track,
                store,
            )
            try:
                regimes = await vlm.classify_regimes(
                    llm,
                    models,
                    effort,
                    regimes,
                    reps,
                    micro,
                    tuning,
                    on_usage=timer.on_usage,
                )
            except Exception as error:  # noqa: BLE001 — деградация как в provider
                logger.warning("VLM-классификация недоступна (%s): типы из сигнатур", error)

    (out_dir / "regimes.json").write_text(
        json.dumps(
            [
                {
                    "start_s": r.start_s,
                    "end_s": r.end_s,
                    "kind": r.kind,
                    "bbox": r.bbox,
                    "board_kind": r.board_kind,
                }
                for r in regimes
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # D: пер-режимные политики
    with timer.track("D_candidates"):
        candidates = _collect_candidates(video, regimes, track, store, srt_blocks, tuning)
    candidates_before_cap = len(candidates)
    if len(candidates) > tuning.max_candidates:
        candidates = sorted(candidates, key=lambda c: c.score, reverse=True)
        candidates = sorted(candidates[: tuning.max_candidates], key=lambda c: c.ts)
    candidates = VideoFrameProvider._cap_by_frames(candidates, tuning.max_frames)
    (out_dir / "candidates.json").write_text(
        json.dumps(
            {
                "before_cap": candidates_before_cap,
                "after_cap": len(candidates),
                "items": [_candidate_to_dict(c) for c in candidates],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if not candidates:
        _print_summary(regimes, [], timer, args.no_vlm)
        return

    # E: качественная выемка
    with timer.track("E_render"):
        items = render_candidates(video, candidates, out_dir, tuning)

    # F: QC + подписи
    if not args.no_vlm:
        from lecturelog.infrastructure.frames import vlm

        def srt_text_at(ts: float) -> str:
            if not srt_blocks:
                return ""
            return min(srt_blocks, key=lambda b: abs(b[0] - ts))[1]

        with timer.track("F_vlm_qc"):
            try:
                items = await vlm.qc_frames(
                    llm,
                    models,
                    effort,
                    items,
                    srt_text_at=srt_text_at,
                    tuning=tuning,
                    on_usage=timer.on_usage,
                )
            except Exception as error:  # noqa: BLE001 — деградация как в provider
                logger.warning("VLM QC недоступен (%s): кадры без подписей", error)

    items = sorted(items, key=lambda i: i.timestamp)
    _print_summary(regimes, items, timer, args.no_vlm)


def _print_summary(regimes, items, timer: _StageTimer, no_vlm: bool) -> None:
    print("\n=== Режимы (сегментация B/VLM C) ===")
    for r in regimes:
        print(f"  {r.start_s:8.1f} .. {r.end_s:8.1f}  {r.kind:8s}  board_kind={r.board_kind}")

    print("\n=== Кадры ===")
    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item.path.suffix] = by_kind.get(item.path.suffix, 0) + 1
    print(f"  всего: {len(items)}")
    for ext, n in sorted(by_kind.items()):
        print(f"  {ext}: {n}")

    print("\n=== Тайминги стадий ===")
    for name, dur in timer.durations.items():
        print(f"  {name:20s} {dur:8.2f}с")
    print(f"  {'итого':20s} {sum(timer.durations.values()):8.2f}с")

    if not no_vlm:
        total_prompt = sum(u.get("prompt", 0) for u in timer.usage)
        total_output = sum(u.get("output", 0) for u in timer.usage)
        print("\n=== Расход токенов VLM ===")
        print(f"  вызовов: {len(timer.usage)}, prompt: {total_prompt}, output: {total_output}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video", type=Path, help="путь к видео лекции")
    parser.add_argument("srt", type=Path, help="путь к SRT-транскрипту")
    parser.add_argument("out_dir", type=Path, help="каталог для дампа отладки")
    parser.add_argument(
        "--no-vlm",
        action="store_true",
        help="пропустить стадии C/F (VLM) — чистый CV-прогон без сети и без токенов",
    )
    parser.add_argument(
        "--models",
        default=None,
        help="список моделей VLM через запятую (по умолчанию — env LLM_MODELS_VIDEO_SLIDES)",
    )
    parser.add_argument(
        "--effort",
        default=None,
        help="reasoning effort для VLM-вызовов (по умолчанию — LLM_EFFORT_VIDEO_SLIDES)",
    )
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
