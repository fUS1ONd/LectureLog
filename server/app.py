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

    return KeyPool(clients=clients, rpm_per_key=12)


def _build_task_manager(settings: Settings) -> TaskManager:
    pool = _build_pool(settings)
    runner = PipelineRunner(config=settings, pool=pool)
    return TaskManager(settings=settings, runner=runner)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Защита для окружений, где lifespan может быть отключён в тестах.
        if getattr(app.state, "task_manager", None) is None:
            app.state.task_manager = _build_task_manager(cfg)
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.task_manager = _build_task_manager(cfg)
    app.include_router(router)
    return app


# Ленивая инициализация: uvicorn вызовет create_app() через "server.app:create_app"
# Для прямого запуска модуля:
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)
