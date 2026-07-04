from __future__ import annotations

from pathlib import Path

from fastapi import Request

from lecturelog.application.worker import PipelineWorker
from lecturelog.domain.ports import CookieStore, Storage, TaskRepository


def get_repository(request: Request) -> TaskRepository:
    return request.app.state.repository


def get_worker(request: Request) -> PipelineWorker:
    return request.app.state.worker


def get_work_dir(request: Request) -> Path:
    # Локальный эфемерный scratch для внутренних стадий пайплайна (не S3).
    return request.app.state.work_dir


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_presign_expiry(request: Request) -> int:
    # Срок жизни presigned-ссылок; в тестах app.state может не содержать config.
    return getattr(request.app.state, "presign_expiry", 3600)


def get_cookie_store(request: Request) -> CookieStore:
    return request.app.state.cookie_store
