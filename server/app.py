from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from lecturelog.config import Settings, get_settings
from lecturelog.llm.key_pool import KeyPool
from lecturelog.pipeline.runner import PipelineRunner
from server.routes import router
from server.task_manager import TaskManager


def _build_pool(settings: Settings) -> KeyPool:
    clients = []
    if settings.gemini_api_keys:
        try:
            from google import genai

            clients = [genai.Client(api_key=key) for key in settings.gemini_api_keys]
        except Exception:
            clients = []

    return KeyPool(clients=clients, rpm_per_key=12, model=settings.GEMINI_MODEL)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = _build_pool(cfg)
        runner = PipelineRunner(config=cfg, pool=pool)
        app.state.task_manager = TaskManager(settings=cfg, runner=runner)
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
