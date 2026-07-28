from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from openai import AsyncOpenAI

from lecturelog.application.factories import (
    storage_factory,
    transcriber_factory,
    webhook_notifier_factory,
)
from lecturelog.application.pipeline_service import PipelineService
from lecturelog.application.progress_plan import ProgressPlan
from lecturelog.application.worker import PipelineWorker
from lecturelog.config.settings import get_config
from lecturelog.infrastructure.export.obsidian_exporter import ObsidianExporter
from lecturelog.infrastructure.frames.provider import VideoFrameProvider
from lecturelog.infrastructure.llm.llm_client import LlmClient
from lecturelog.infrastructure.llm.model_cooldown import ModelCooldown
from lecturelog.infrastructure.media.audio_cutter import FfmpegAudioCutter
from lecturelog.infrastructure.media.video_cutter import FfmpegVideoCutter
from lecturelog.infrastructure.media.video_ingestor import VideoIngestor
from lecturelog.infrastructure.persistence.engine import make_engine, make_session_factory
from lecturelog.infrastructure.persistence.task_repository import PostgresTaskRepository
from lecturelog.infrastructure.slides.alignment.service import AlignmentTuning
from lecturelog.infrastructure.structurize.gemini_structurizer import GeminiStructurizer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()

    engine = make_engine(cfg.database.url)
    session_factory = make_session_factory(engine)
    repo = PostgresTaskRepository(session_factory=session_factory)

    from lecturelog.infrastructure.youtube.pg_cookie_store import PgCookieStore

    cookie_store = PgCookieStore(session_factory=session_factory)

    interrupted = await repo.mark_stale_as_interrupted()
    if interrupted:
        logger.warning("Помечено INTERRUPTED задач после рестарта: %d", interrupted)

    # Транспорт LLM: OpenRouter (BYOK) через openai SDK вместо пула ключей Gemini.
    openai_client = AsyncOpenAI(base_url=cfg.llm.base_url, api_key=cfg.llm.openrouter_key)
    cooldown = ModelCooldown()
    llm = LlmClient(openai_client, cooldown, max_tokens=cfg.llm.max_tokens)

    transcriber = transcriber_factory(cfg.transcribe)
    transcribe_model = (
        "whisper-large-v3" if cfg.transcribe.provider == "groq" else cfg.transcribe.deepgram_model
    )
    transcribe_language = "auto"
    if cfg.transcribe.provider == "deepgram" and not cfg.transcribe.deepgram_detect_language:
        transcribe_language = cfg.transcribe.deepgram_language
    logger.info(
        "STT включён: provider=%s model=%s language=%s",
        cfg.transcribe.provider,
        transcribe_model,
        transcribe_language,
    )
    structurizer = GeminiStructurizer(
        gemini_client=llm,
        split_models=cfg.llm.split_models,
        subsplit_models=cfg.llm.subsplit_models,
        render_models=cfg.llm.render_models,
        concurrency_subsplit=cfg.llm.concurrency_subsplit,
        concurrency_render=cfg.llm.concurrency_render,
        prompts_dir=Path("prompts"),
        effort_split=cfg.llm.effort_split,
        effort_subsplit=cfg.llm.effort_subsplit,
        effort_render=cfg.llm.effort_render,
        effort_slide_match=cfg.llm.effort_slide_match,
        slide_match_models=cfg.llm.slide_match_models,
        document_alignment_mode=cfg.document_slides.alignment_mode,
        document_alignment_tuning=AlignmentTuning(
            candidate_limit=cfg.document_slides.candidate_limit,
            neighbor_radius=cfg.document_slides.neighbor_radius,
            deck_min_supported_ratio=cfg.document_slides.deck_min_supported_ratio,
        ),
    )
    # Опциональный вебхук: включается только при заданных URL и секрете.
    notifier = webhook_notifier_factory(cfg.webhook.callback_url, cfg.webhook.secret)
    if cfg.webhook.callback_url and not cfg.webhook.secret:
        logger.warning(
            "PLATFORM_CALLBACK_URL задан, но LECTURELOG_WEBHOOK_SECRET нет — вебхук выключен"
        )
    if notifier is not None:
        # Логируем сам факт включения, без секрета.
        logger.info("Вебхук включён, callback_url=%s", cfg.webhook.callback_url)

    # Хранилище лекций (S3/MinIO). presigned наружу доступен только при public endpoint.
    storage = storage_factory(cfg.s3)
    if cfg.s3.public_endpoint:
        logger.info("S3 presigned включён (public endpoint задан)")
    else:
        logger.info("S3 presigned выключен: /uploads и /result-url отдадут 409, работает стрим")

    # Фабрика провайдера кадров из видео: (video_path, srt_path) -> SlideProvider.
    # None при FRAMES_ENABLED=false — тогда видео идёт как аудио-лекция без кадров.
    frames_factory = None
    if cfg.frames.enabled:

        def frames_factory(video_path: Path, srt_path: Path) -> VideoFrameProvider:
            return VideoFrameProvider(
                video_path=video_path,
                srt_path=srt_path,
                llm=llm,
                models=cfg.frames.models,
                effort=cfg.frames.effort,
                classify_models=cfg.frames.classify_models,
                classify_effort=cfg.frames.classify_effort,
            )

    app.state.frames_provider_factory = frames_factory

    service = PipelineService(
        repository=repo,
        transcriber=transcriber,
        structurizer=structurizer,
        audio_cutter=FfmpegAudioCutter(),
        video_cutter=FfmpegVideoCutter(),
        ingestor=VideoIngestor(
            cookie_store=cookie_store,
            target_resolution=cfg.media.target_resolution,
        ),
        exporter=ObsidianExporter(),
        progress_plan_factory=ProgressPlan.for_audio,
        webhook_notifier=notifier,
        storage=storage,
    )
    worker = PipelineWorker(service=service, concurrency=cfg.worker.max_concurrent_tasks)
    await worker.start()

    app.state.config = cfg
    app.state.repository = repo
    app.state.worker = worker
    app.state.storage = storage
    app.state.cookie_store = cookie_store
    app.state.presign_expiry = cfg.s3.presign_expiry
    # Локальный эфемерный scratch для внутренних стадий пайплайна (не S3).
    app.state.work_dir = Path(os.getenv("WORK_DIR", "/app/data"))
    app.state.llm = llm
    app.state.prompts_dir = Path("prompts")
    try:
        yield
    finally:
        await worker.stop()
        # Закрываем HTTP-клиент OpenRouter, чтобы не оставлять открытые сокеты.
        await openai_client.close()
        await engine.dispose()
