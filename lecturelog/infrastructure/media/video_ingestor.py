from __future__ import annotations

import asyncio
import re
import shutil
import sys
import tempfile
from pathlib import Path

from lecturelog.domain.exceptions import MediaIngestError, MediaIngestReason
from lecturelog.domain.media_source import MediaSource, VideoFileSource, VideoUrlSource
from lecturelog.domain.ports import CookieStore, MediaIngestor
from lecturelog.infrastructure.media.url_utils import VideoUrlKind, classify_video_url

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
_MAX_DIAGNOSTIC_LENGTH = 2000
_URL_RE = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?i)(authorization|cookie|set-cookie|bearer)(\s*[:=]\s*|\s+)[^\s,;]+")
_COOKIE_PATH_RE = re.compile(r"(?i)(--cookies(?:=|\s+))\S+")


class VideoIngestor(MediaIngestor):
    """yt-dlp для URL и нормализация локального видео в output_dir/video.*."""

    def __init__(
        self,
        cookie_store: CookieStore | None = None,
        target_resolution: str = "720",
    ):
        self._cookie_store = cookie_store
        self._target_resolution = target_resolution

    async def ingest(self, source: MediaSource, output_dir: Path) -> Path:
        """Привести видеоисточник к локальному файлу output_dir/video.*."""
        output_dir.mkdir(parents=True, exist_ok=True)

        if isinstance(source, VideoUrlSource):
            return await self._download_url(source.url, output_dir)

        if isinstance(source, VideoFileSource):
            src_path = source.path
            if not src_path.exists():
                raise FileNotFoundError(f"Видеофайл не найден: {src_path}")
            suffix = (
                src_path.suffix.lower() if src_path.suffix.lower() in VIDEO_EXTENSIONS else ".mp4"
            )
            target = output_dir / f"video{suffix}"
            if src_path.resolve() != target.resolve():
                shutil.copy2(src_path, target)
            return target

        raise ValueError(f"VideoIngestor не принимает источник вида {source.kind!r}")

    async def extract_audio(self, video_path: Path, output_dir: Path) -> Path:
        """Извлекает звуковую дорожку из видео в mp3 (128 kbps моно)."""
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / "audio.mp3"

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-ac",
            "1",
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg не смог извлечь аудио: {stderr.decode('utf-8', errors='ignore')}"
            )
        return target

    @staticmethod
    def _yt_dlp_bin() -> str:
        candidate = Path(sys.executable).parent / "yt-dlp"
        if candidate.exists():
            return str(candidate)
        return "yt-dlp"

    def _format_sort(self) -> str:
        if self._target_resolution == "best":
            return "res,proto:https"
        return f"res:{self._target_resolution},proto:https"

    def _base_args(self, kind: VideoUrlKind) -> list[str]:
        args = [
            self._yt_dlp_bin(),
            "-f",
            "bv*+ba/b",
            "--no-playlist",
            "--playlist-items",
            "1",
            "--no-progress",
            "-S",
            self._format_sort(),
        ]
        if kind is VideoUrlKind.YOUTUBE:
            args += [
                "--js-runtimes",
                "deno",
                "--remote-components",
                "ejs:github",
            ]
        return args

    def _x_backend_args(self, backend: str) -> list[str]:
        return ["--extractor-args", f"twitter:api={backend}"]

    def _preflight_args(self, url: str, backend: str) -> list[str]:
        return [
            *self._base_args(VideoUrlKind.X),
            *self._x_backend_args(backend),
            "--simulate",
            url,
        ]

    def _download_args(
        self,
        *,
        url: str,
        kind: VideoUrlKind,
        attempt_dir: Path,
        cookies_path: Path | None,
        x_backend: str | None,
    ) -> list[str]:
        args = self._base_args(kind)
        if x_backend is not None:
            args += self._x_backend_args(x_backend)
        if cookies_path is not None:
            args += ["--cookies", str(cookies_path)]
        args += [
            "--merge-output-format",
            "mp4",
            "--print",
            "after_move:filepath",
            "-o",
            str(attempt_dir / "video.%(ext)s"),
            url,
        ]
        return args

    async def _run_yt_dlp(self, args: list[str], source_kind: str) -> tuple[int, bytes, bytes]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise MediaIngestError(
                source_kind=source_kind,
                reason=MediaIngestReason.TOOL_MISSING,
                public_message="Сервис скачивания видео временно недоступен.",
                diagnostic="yt-dlp executable not found",
            ) from exc
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout, stderr

    async def _select_x_backend(self, url: str) -> str:
        failures: list[MediaIngestError] = []
        for backend in ("graphql", "syndication"):
            returncode, _, stderr = await self._run_yt_dlp(
                self._preflight_args(url, backend),
                VideoUrlKind.X.value,
            )
            if returncode == 0:
                return backend
            failures.append(
                self._error_from_failure(
                    kind=VideoUrlKind.X,
                    stderr=stderr,
                    url=url,
                    phase=f"{backend} preflight",
                )
            )

        reason = self._combined_reason(failures)
        diagnostic = " | ".join(error.diagnostic for error in failures)
        raise MediaIngestError(
            source_kind=VideoUrlKind.X.value,
            reason=reason,
            public_message=self._public_message(VideoUrlKind.X, reason),
            diagnostic=diagnostic[:_MAX_DIAGNOSTIC_LENGTH],
        )

    async def _download_url(self, url: str, output_dir: Path) -> Path:
        kind = classify_video_url(url)
        x_backend = await self._select_x_backend(url) if kind is VideoUrlKind.X else None
        cookies_path: Path | None = None
        cookies_dir: str | None = None
        attempt_dir = Path(tempfile.mkdtemp(prefix=".yt-dlp-", dir=output_dir))

        try:
            if kind is VideoUrlKind.YOUTUBE and self._cookie_store is not None:
                content = await self._cookie_store.get()
                if content:
                    # Cookies живут вне расшаренного output и доступны только владельцу.
                    cookies_dir = tempfile.mkdtemp(prefix="yt-cookies-")
                    cookies_path = Path(cookies_dir) / "cookies.txt"
                    cookies_path.write_bytes(content)
                    cookies_path.chmod(0o600)

            args = self._download_args(
                url=url,
                kind=kind,
                attempt_dir=attempt_dir,
                cookies_path=cookies_path,
                x_backend=x_backend,
            )
            returncode, stdout, stderr = await self._run_yt_dlp(args, kind.value)
            if returncode != 0:
                raise self._error_from_failure(
                    kind=kind,
                    stderr=stderr,
                    url=url,
                    phase="download",
                )
            return self._normalize_download(stdout, attempt_dir, output_dir, kind)
        finally:
            shutil.rmtree(attempt_dir, ignore_errors=True)
            if cookies_dir is not None:
                shutil.rmtree(cookies_dir, ignore_errors=True)

    def _normalize_download(
        self,
        stdout: bytes,
        attempt_dir: Path,
        output_dir: Path,
        kind: VideoUrlKind,
    ) -> Path:
        printed = [
            Path(line.strip())
            for line in stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        if len(printed) != 1:
            raise self._invalid_output(
                kind,
                f"expected one after_move filepath, got {len(printed)}",
            )

        candidate = printed[0]
        if not candidate.is_absolute():
            candidate = attempt_dir / candidate
        candidate = candidate.resolve()
        resolved_attempt = attempt_dir.resolve()
        if candidate == resolved_attempt or resolved_attempt not in candidate.parents:
            raise self._invalid_output(kind, "after_move filepath escapes attempt directory")
        if not candidate.is_file():
            raise self._invalid_output(kind, "after_move filepath does not exist")

        final_files = [
            path.resolve()
            for path in attempt_dir.rglob("*")
            if path.is_file() and path.suffix.lower() not in {".part", ".ytdl"}
        ]
        if final_files != [candidate]:
            raise self._invalid_output(
                kind,
                f"attempt directory contains {len(final_files)} final files",
            )

        suffix = candidate.suffix.lower()
        if not suffix:
            raise self._invalid_output(kind, "downloaded file has no extension")
        target = output_dir / f"video{suffix}"
        if target.exists():
            raise self._invalid_output(kind, "target video already exists")
        candidate.replace(target)
        return target

    def _invalid_output(self, kind: VideoUrlKind, diagnostic: str) -> MediaIngestError:
        return MediaIngestError(
            source_kind=kind.value,
            reason=MediaIngestReason.INVALID_OUTPUT,
            public_message="Сервис скачивания вернул некорректный результат.",
            diagnostic=diagnostic,
        )

    def _error_from_failure(
        self,
        *,
        kind: VideoUrlKind,
        stderr: bytes,
        url: str,
        phase: str,
    ) -> MediaIngestError:
        raw = stderr.decode("utf-8", errors="replace")
        upper = raw.upper()
        if any(token in upper for token in ("429", "TOO MANY REQUESTS", "RATE LIMIT")):
            reason = MediaIngestReason.RATE_LIMIT
        elif any(
            token in upper
            for token in (
                "SIGN IN",
                "LOGIN REQUIRED",
                "PRIVATE VIDEO",
                "PROTECTED",
                "AGE-RESTRICTED",
                "AGE RESTRICTED",
            )
        ):
            reason = MediaIngestReason.AUTH_REQUIRED
        elif any(
            token in upper
            for token in (
                "NOT FOUND",
                "NO VIDEO",
                "NO MEDIA",
                "DOES NOT EXIST",
                "UNAVAILABLE",
                "REMOVED",
            )
        ):
            reason = MediaIngestReason.NOT_FOUND
        elif any(
            token in upper
            for token in (
                "NO SPACE LEFT",
                "PERMISSION DENIED",
                "READ-ONLY FILE SYSTEM",
                "FFMPEG",
            )
        ):
            reason = MediaIngestReason.LOCAL_IO
        else:
            reason = MediaIngestReason.EXTRACTOR
        diagnostic = self._sanitize_diagnostic(f"{phase}: {raw}", url)
        return MediaIngestError(
            source_kind=kind.value,
            reason=reason,
            public_message=self._public_message(kind, reason),
            diagnostic=diagnostic,
        )

    @staticmethod
    def _combined_reason(errors: list[MediaIngestError]) -> MediaIngestReason:
        reasons = {error.reason for error in errors}
        for reason in (
            MediaIngestReason.RATE_LIMIT,
            MediaIngestReason.AUTH_REQUIRED,
            MediaIngestReason.NOT_FOUND,
            MediaIngestReason.EXTRACTOR,
        ):
            if reason in reasons:
                return reason
        return MediaIngestReason.EXTRACTOR

    @staticmethod
    def _public_message(kind: VideoUrlKind, reason: MediaIngestReason) -> str:
        source = (
            "X" if kind is VideoUrlKind.X else "YouTube" if kind is VideoUrlKind.YOUTUBE else "URL"
        )
        if reason is MediaIngestReason.RATE_LIMIT:
            return f"{source} временно ограничил частоту запросов. Попробуйте позже."
        if reason is MediaIngestReason.AUTH_REQUIRED:
            if kind is VideoUrlKind.YOUTUBE:
                return "YouTube требует обновить cookies."
            return f"Видео {source} недоступно без авторизации."
        if reason is MediaIngestReason.NOT_FOUND:
            return f"Видео {source} не найдено или было удалено."
        if reason is MediaIngestReason.LOCAL_IO:
            return "Не удалось сохранить или собрать скачанное видео."
        return f"Не удалось получить видео из {source}."

    @staticmethod
    def _sanitize_diagnostic(text: str, source_url: str) -> str:
        sanitized = text.replace(source_url, "<source-url>")
        sanitized = _COOKIE_PATH_RE.sub(r"\1<redacted>", sanitized)
        sanitized = _SECRET_RE.sub(r"\1: <redacted>", sanitized)
        sanitized = _URL_RE.sub("<url>", sanitized)
        return " ".join(sanitized.split())[:_MAX_DIAGNOSTIC_LENGTH]
