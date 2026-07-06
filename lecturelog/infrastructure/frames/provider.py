"""VideoFrameProvider — реализация SlideProvider для видеокадров.

Оркеструет воронку A–F (дизайн §4); привязку G делает pipeline после
structurize. CPU-стадии выполняются в to_thread, VLM-сбои деградируют
(дизайн §10), но исключения инфраструктуры (ffmpeg) пробрасываются —
их гасит стадия в pipeline (философия no_slides)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from lecturelog.domain.ports import ProgressCallback, SlideImage, SlideProvider, UsageCallback
from lecturelog.infrastructure.frames import vlm
from lecturelog.infrastructure.frames.board import board_candidates
from lecturelog.infrastructure.frames.coding import coding_candidates_from_frames
from lecturelog.infrastructure.frames.dedup import dedup_candidates
from lecturelog.infrastructure.frames.extract import render_candidates
from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore, decode_gray
from lecturelog.infrastructure.frames.segmentation import segment_regimes
from lecturelog.infrastructure.frames.signals import compute_signals
from lecturelog.infrastructure.frames.slides_policy import slide_candidates
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime, SignalTrack
from lecturelog.infrastructure.srt import parse_srt_time

logger = logging.getLogger(__name__)

_SRT_TIME = re.compile(r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->")


def _parse_srt_blocks(srt_text: str) -> list[tuple[float, str]]:
    """[(start_sec, text)] — реплики для оракула D2 и легенды QC."""
    blocks: list[tuple[float, str]] = []
    for block in re.split(r"\n\s*\n+", srt_text.strip()):
        m = _SRT_TIME.search(block)
        if not m:
            continue
        lines = [
            line.strip()
            for line in block.splitlines()
            if line.strip() and not line.strip().isdigit() and "-->" not in line
        ]
        blocks.append((parse_srt_time(m.group(1)), " ".join(lines)))
    return blocks


class VideoFrameProvider(SlideProvider):
    def __init__(
        self,
        video_path: Path,
        srt_path: Path,
        llm,
        models: list[str],
        effort: str,
        classify_models: list[str] | None = None,
        classify_effort: str | None = None,
        tuning: FramesTuning | None = None,
        prompts_dir: Path = Path("prompts"),
    ) -> None:
        self._video = Path(video_path)
        self._srt = Path(srt_path)
        self._llm = llm
        self._models = models
        self._effort = effort
        # Классификация может идти на более тяжёлой модели, чем QC (1-2 вызова)
        self._classify_models = classify_models or models
        self._classify_effort = classify_effort or effort
        self._tuning = tuning or FramesTuning()
        self._prompts_dir = prompts_dir

    async def get_slides(
        self,
        output_dir: Path,
        on_progress: ProgressCallback | None = None,
        on_usage: UsageCallback | None = None,
    ) -> list[SlideImage]:
        t = self._tuning
        output_dir.mkdir(parents=True, exist_ok=True)
        srt_blocks = _parse_srt_blocks(self._srt.read_text(encoding="utf-8"))

        # A: грубый проход — сигналы + тумбы
        store = ThumbStore(output_dir / "thumbs")
        track: SignalTrack = await asyncio.to_thread(
            lambda: compute_signals(
                decode_gray(self._video, fps=t.analysis_fps, width=t.analysis_width),
                fps=t.analysis_fps,
                thumbs=store,
                ignore_bottom_frac=t.subtitle_band_frac,
            )
        )
        # B: сегментация
        regimes = segment_regimes(track, t)

        # C: VLM-классификация; сбой → остаёмся на сигнатурах B (деградация)
        try:
            reps, micro = self._representatives(regimes, track, store)
            regimes = await vlm.classify_regimes(
                self._llm,
                self._classify_models,
                self._classify_effort,
                regimes,
                reps,
                micro,
                t,
                on_usage=on_usage,
                prompts_dir=self._prompts_dir,
            )
        except Exception as error:  # noqa: BLE001 — деградация по дизайну §10
            logger.warning("VLM-классификация недоступна (%s): типы из сигнатур", error)

        # D: пер-режимные политики
        candidates = await asyncio.to_thread(
            self._collect_candidates, regimes, track, store, srt_blocks
        )
        # Глобальный дедуп до VLM: повторы слайдов и дубли «врезка vs общий
        # план» между режимами/батчами QC не видит (они в разных вызовах)
        candidates = dedup_candidates(candidates, store, track, t)
        if len(candidates) > t.max_candidates:
            candidates = sorted(candidates, key=lambda c: c.score, reverse=True)
            candidates = sorted(candidates[: t.max_candidates], key=lambda c: c.ts)
        # Cap по бюджету кадров ДО render — pair-aware: пара «код+вывод»
        # рендерится в 2 кадра и либо влезает целиком, либо пропускается.
        # Финальный срез после QC рвал бы половину пары (QC кадры только
        # убирает, так что бюджет max_frames после этого не превышается).
        candidates = self._cap_by_frames(candidates, t.max_frames)
        if not candidates:
            return []

        # E: качественная выемка
        items = await asyncio.to_thread(render_candidates, self._video, candidates, output_dir, t)

        # F: QC + подписи; сбой → кадры без QC (чуть грязнее, но стадия работает)
        try:
            items = await vlm.qc_frames(
                self._llm,
                self._models,
                self._effort,
                items,
                srt_text_at=lambda ts: self._nearest_text(srt_blocks, ts),
                tuning=t,
                on_usage=on_usage,
                prompts_dir=self._prompts_dir,
            )
        except Exception as error:  # noqa: BLE001 — деградация по дизайну §10
            logger.warning("VLM QC недоступен (%s): кадры без подписей", error)

        return sorted(items, key=lambda i: i.timestamp)

    def _representatives(self, regimes: list[Regime], track: SignalTrack, store: ThumbStore):
        """Репрезентативный кадр режима — самый резкий из 3 сэмплов середины."""
        import cv2

        reps, micro = [], []
        for r in regimes:
            mid = int((r.start_s + r.end_s) / 2 * track.fps)
            idxs = [max(0, min(track.n_frames - 1, mid + d)) for d in (-2, 0, 2)]
            frames = [store.get(i) for i in idxs]
            reps.append(max(frames, key=lambda f: float(cv2.Laplacian(f, cv2.CV_64F).var())))
            i0, i1 = int(r.start_s * track.fps), max(int(r.end_s * track.fps), 1)
            seg_mad = track.mad[i0:i1]
            seg_motion = track.motion_frac[i0:i1]
            micro_mask = (seg_mad > 0.05) & (seg_motion < self._tuning.micro_area_max)
            micro.append(float(micro_mask.mean()) if len(seg_mad) else 0.0)
        return reps, micro

    def _collect_candidates(self, regimes, track, store, srt_blocks) -> list[Candidate]:
        t = self._tuning
        out: list[Candidate] = []
        for regime in regimes:
            if regime.kind == "slides":
                out.extend(slide_candidates(regime, track, store, t))
            elif regime.kind == "board":
                out.extend(board_candidates(regime, track, store, t))
            elif regime.kind in ("code", "terminal"):
                # Передекод отрезка на code_fps: 1 fps печать не видит
                frames = list(
                    decode_gray(
                        self._video,
                        fps=t.code_fps,
                        width=t.code_width,
                        start_s=regime.start_s,
                        end_s=regime.end_s,
                    )
                )
                out.extend(
                    coding_candidates_from_frames(
                        frames, fps=t.code_fps, regime=regime, tuning=t, srt_blocks=srt_blocks
                    )
                )
            elif regime.kind == "camera":
                # Ручная камера: разреженный отбор — плато-политика, всё решит QC
                out.extend(slide_candidates(regime, track, store, t))
            # speaker / other → 0 кандидатов (это норма, дизайн §1)
        return sorted(out, key=lambda c: c.ts)

    @staticmethod
    def _cap_by_frames(candidates: list[Candidate], max_frames: int) -> list[Candidate]:
        """Обрезка по бюджету кадров с учётом пар: кандидат с pair_ts даёт
        2 кадра и берётся только целиком — рвать пару «код+вывод» нельзя."""
        out: list[Candidate] = []
        budget = max_frames
        for cand in candidates:
            cost = 2 if cand.pair_ts is not None else 1
            if cost > budget:
                continue  # пара не влезает — пропускаем, одиночный ещё может влезть
            out.append(cand)
            budget -= cost
        return out

    @staticmethod
    def _nearest_text(srt_blocks: list[tuple[float, str]], ts: float) -> str:
        if not srt_blocks:
            return ""
        return min(srt_blocks, key=lambda b: abs(b[0] - ts))[1]
