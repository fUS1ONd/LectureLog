from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lecturelog.domain.enums import TaskStatus
from lecturelog.domain.media_source import MediaSource
from lecturelog.domain.models import Section, Task, Topic

ProgressCallback = Callable[[int], Awaitable[None] | None]
# Нейтральное зерно расхода ресурсов (audio_seconds / tokens). Стадию навешивает оркестратор.
UsageCallback = Callable[[dict], Awaitable[None] | None]


class Transcriber(ABC):
    @abstractmethod
    async def transcribe(
        self,
        audio_path: Path,
        output_dir: Path,
        on_progress: ProgressCallback | None = None,
        on_usage: UsageCallback | None = None,
    ) -> Path:
        """Аудио -> путь к SRT-файлу."""


@dataclass(frozen=True)
class SlideImage:
    """Элемент результата SlideProvider.

    timestamp — секунды от начала видео (None у документных слайдов: у них
    нет таймкода, привязка к секциям делается LLM-матчингом в structurize).
    extracted_text — задел под guide-режим (дизайн §12), в конспекте всегда None."""

    path: Path
    timestamp: float | None = None
    caption: str | None = None
    extracted_text: str | None = None


class SlideProvider(ABC):
    @abstractmethod
    async def get_slides(
        self,
        output_dir: Path,
        on_progress: ProgressCallback | None = None,
        on_usage: UsageCallback | None = None,
    ) -> list[SlideImage]:
        """Вернуть слайды/кадры. Документы: timestamp=None; видеокадры: timestamp обязателен."""


class Structurizer(ABC):
    @abstractmethod
    async def structurize(
        self,
        srt_path: Path,
        slide_images: list[Path],
        output_dir: Path,
        on_progress: ProgressCallback | None = None,
        on_usage: UsageCallback | None = None,
    ) -> list[Topic]:
        """SRT + слайды -> структура тем/подтем с привязкой слайдов."""


class MediaCutter(ABC):
    @abstractmethod
    async def cut(self, source_path: Path, sections: list[Section], output_dir: Path) -> list[Path]:
        """Нарезать медиа по секциям -> список путей фрагментов (по одному на секцию)."""


class MediaIngestor(ABC):
    @abstractmethod
    async def ingest(self, source: MediaSource, output_dir: Path) -> Path:
        """Привести видеоисточник к локальному файлу (скачать URL / принять файл)."""

    @abstractmethod
    async def extract_audio(self, video_path: Path, output_dir: Path) -> Path:
        """Извлечь аудиодорожку из видео."""


@dataclass(frozen=True)
class ExportResult:
    """Итог раскладки exporter: корень output/ и фактические пути медиа/слайдов.

    Единый источник истины путей для заливки объектов и build_structure
    (ключи MinIO считаются из этих путей одной формулой)."""

    output_root: Path
    media_targets: list[Path]
    slide_targets: list[Path]


class Exporter(ABC):
    @abstractmethod
    async def export(
        self,
        topics: list[Topic],
        media_fragments: list[Path],
        slide_images: list[SlideImage],
        output_dir: Path,
        media_kind: str,
    ) -> ExportResult:
        """Разложить конспект.md + медиа + слайды в output_dir/output/.

        НЕ зипует (zip собирается на лету при скачивании). Возвращает ExportResult
        с корнем output/ и фактическими путями медиа/слайдов."""


class TaskRepository(ABC):
    @abstractmethod
    async def create(self, task: Task) -> None: ...

    @abstractmethod
    async def get(self, task_id: str) -> Task | None: ...

    @abstractmethod
    async def update(self, task: Task) -> None: ...

    @abstractmethod
    async def mark_stale_as_interrupted(self) -> int:
        """Пометить все PROCESSING-задачи как INTERRUPTED (при старте). Вернуть кол-во."""

    @abstractmethod
    async def delete(self, task_id: str) -> None:
        """Удалить строку задачи. Идемпотентно: отсутствие строки — не ошибка."""


class Storage(ABC):
    """Порт хранилища лекций. domain/application не знают про boto/minio —
    presigned и override-заголовки реализует инфра-адаптер."""

    @abstractmethod
    async def upload_file(self, local_path: Path, key: str) -> None:
        """Залить локальный файл в бакет под ключом."""

    @abstractmethod
    async def download_file(self, key: str, local_path: Path) -> None:
        """Скачать объект по ключу в локальный файл (создав родительские каталоги)."""

    @abstractmethod
    async def presigned_put(self, key: str, expires_in: int | None = None) -> str | None:
        """Presigned PUT URL для загрузки клиентом в uploads/ (публичный хост).
        None, если публичный endpoint не задан."""

    @abstractmethod
    async def presigned_get(
        self,
        key: str,
        expires_in: int | None = None,
        download_filename: str | None = None,
        content_type: str | None = None,
    ) -> str | None:
        """Presigned GET URL. Если публичный endpoint не задан — None
        (наружу presigned не выдаётся, работает только стрим)."""

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> None:
        """Удалить все объекты бакета с данным ключевым префиксом.
        Идемпотентно: отсутствие объектов под префиксом — не ошибка (no-op)."""

    @abstractmethod
    async def list_keys(self, prefix: str) -> list[str]:
        """Вернуть список ключей объектов бакета с данным префиксом.
        Пустой результат — пустой список (не ошибка). Порядок реализация
        не гарантирует на уровне порта; адаптеры возвращают как есть."""


class WebhookNotifier(ABC):
    @abstractmethod
    async def notify(
        self,
        task_id: str,
        status: TaskStatus,
        error: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Best-effort пуш платформе о терминальном статусе задачи.
        error_code — машинный код ошибки (rate_limit/bad_input/internal) или None.
        Реализация НЕ должна выбрасывать наружу и НЕ должна блокировать дольше своего таймаута."""


@dataclass(frozen=True)
class CookieStatus:
    """Метаданные хранимых cookies (без самого содержимого — это секрет)."""

    exists: bool
    updated_at: datetime | None
    size: int


class CookieStore(ABC):
    """Порт хранилища YouTube-cookies. Singleton: одна актуальная запись."""

    @abstractmethod
    async def save(self, content: bytes) -> CookieStatus:
        """Сохранить (перезаписать) cookies, вернуть актуальный статус."""

    @abstractmethod
    async def get(self) -> bytes | None:
        """Вернуть содержимое cookies или None, если не загружены."""

    @abstractmethod
    async def status(self) -> CookieStatus:
        """Вернуть метаданные без содержимого."""

    @abstractmethod
    async def delete(self) -> None:
        """Удалить cookies (идемпотентно)."""
