from __future__ import annotations

from enum import StrEnum


class DomainError(Exception):
    """Базовое доменное исключение."""


class TaskNotFound(DomainError):
    def __init__(self, task_id: str):
        super().__init__(f"Задача не найдена: {task_id}")
        self.task_id = task_id


class ResultNotReady(DomainError):
    def __init__(self, task_id: str):
        super().__init__(f"Результат ещё не готов: {task_id}")
        self.task_id = task_id


class TranscribeFailed(DomainError):
    def __init__(self, detail: str):
        super().__init__(f"Транскрибация упала: {detail}")
        self.detail = detail


class InvalidFormat(DomainError):
    def __init__(self, allowed: list[str]):
        super().__init__(f"Недопустимый формат. Разрешены: {allowed}")
        self.allowed = allowed


class InvalidSource(DomainError):
    def __init__(self, message: str = "Передайте ровно один источник: audio, video или video_url"):
        super().__init__(message)


class MediaIngestReason(StrEnum):
    NOT_FOUND = "not_found"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMIT = "rate_limit"
    TOOL_MISSING = "tool_missing"
    LOCAL_IO = "local_io"
    EXTRACTOR = "extractor"
    INVALID_OUTPUT = "invalid_output"


class MediaIngestError(DomainError):
    """Безопасная публичная ошибка ingest с отдельной санитизированной диагностикой."""

    def __init__(
        self,
        *,
        source_kind: str,
        reason: MediaIngestReason,
        public_message: str,
        diagnostic: str = "",
    ):
        super().__init__(public_message)
        self.source_kind = source_kind
        self.reason = reason
        self.public_message = public_message
        self.diagnostic = diagnostic
