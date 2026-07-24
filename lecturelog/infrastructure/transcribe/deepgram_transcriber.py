from __future__ import annotations

import asyncio
import logging
import math
import mimetypes
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from lecturelog.domain.ports import ProgressCallback, Transcriber, UsageCallback
from lecturelog.infrastructure.transcribe.common import (
    Caption,
    emit_progress,
    emit_usage,
    normalize_captions,
    probe_audio_seconds,
    render_srt,
    write_srt_atomic,
)

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 2 * 1024 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 256 * 1024
RETRYABLE_STATUSES = {408, 429, 500, 502, 503}
BAD_INPUT_STATUSES = {413, 415, 422}
BAD_INPUT_CODES = {"ASR_UNPROCESSABLE", "INVALID_AUDIO", "CORRUPT_AUDIO"}


def _positive_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _word_text(word: dict[str, Any]) -> str:
    return str(word.get("punctuated_word") or word.get("word") or "").strip()


def _caption_from_words(words: list[dict[str, Any]]) -> Caption | None:
    valid = [
        word
        for word in words
        if _word_text(word)
        and _positive_float(word.get("end")) is not None
        and word.get("start") is not None
    ]
    if not valid:
        return None
    try:
        start = float(valid[0]["start"])
        end = float(valid[-1]["end"])
    except (TypeError, ValueError):
        return None
    return Caption(start=start, end=end, text=" ".join(_word_text(word) for word in valid))


def _split_words(words: list[dict[str, Any]], words_per_caption: int = 12) -> list[Caption]:
    captions: list[Caption] = []
    for index in range(0, len(words), words_per_caption):
        caption = _caption_from_words(words[index : index + words_per_caption])
        if caption is not None:
            captions.append(caption)
    return captions


def build_captions(
    payload: dict[str, Any], fallback_duration: float
) -> tuple[list[Caption], float]:
    results = payload.get("results")
    metadata = payload.get("metadata")
    if not isinstance(results, dict) or not isinstance(metadata, dict):
        raise RuntimeError("Deepgram вернул ответ без results/metadata")
    duration = _positive_float(metadata.get("duration")) or fallback_duration
    utterances = results.get("utterances")
    captions: list[Caption] = []
    if isinstance(utterances, list) and utterances:
        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            words = utterance.get("words") if isinstance(utterance.get("words"), list) else []
            start = _positive_float(utterance.get("start"))
            raw_start = 0.0 if utterance.get("start") == 0 else start
            end = _positive_float(utterance.get("end"))
            transcript = str(utterance.get("transcript") or "").strip()
            long_utterance = len(words) > 12 or (
                raw_start is not None and end is not None and end - raw_start > 8
            )
            if long_utterance and words:
                captions.extend(_split_words(words))
            elif transcript and raw_start is not None and end is not None:
                captions.append(Caption(raw_start, end, transcript))
            elif words:
                caption = _caption_from_words(words)
                if caption is not None:
                    captions.append(caption)
    if not captions:
        channels = results.get("channels")
        if isinstance(channels, list) and channels:
            alternatives = channels[0].get("alternatives", [])
            if alternatives and isinstance(alternatives[0], dict):
                words = alternatives[0].get("words")
                if isinstance(words, list):
                    captions = _split_words(words)
    return normalize_captions(captions, duration=duration or None), duration


class _UploadProgress:
    def __init__(self, size: int, callback: ProgressCallback | None) -> None:
        self.size = size
        self.callback = callback
        self.highest = 0

    async def update(self, sent: int) -> None:
        if self.size <= 0:
            return
        percent = int(sent * 100 / self.size)
        for threshold in range(10, 71, 10):
            if percent >= threshold > self.highest:
                self.highest = threshold
                await emit_progress(self.callback, threshold)


class DeepgramTranscriber(Transcriber):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepgram.com",
        model: str = "nova-3",
        language: str = "ru",
        utt_split: float = 0.8,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._language = language
        self._utt_split = utt_split
        self._transport = transport
        self._sleep = sleep

    async def _stream(self, path: Path, tracker: _UploadProgress) -> AsyncIterator[bytes]:
        with path.open("rb") as source:
            sent = 0
            while chunk := await asyncio.to_thread(source.read, UPLOAD_CHUNK_BYTES):
                sent += len(chunk)
                await tracker.update(sent)
                yield chunk

    async def _request(
        self, client: httpx.AsyncClient, audio_path: Path, tracker: _UploadProgress
    ) -> httpx.Response:
        params = {
            "model": self._model,
            "language": self._language,
            "smart_format": "true",
            "utterances": "true",
            "utt_split": str(self._utt_split),
            "mip_opt_out": "true",
        }
        content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": content_type,
            "Content-Length": str(audio_path.stat().st_size),
        }
        last_response: httpx.Response | None = None
        for attempt in range(5):
            try:
                response = await client.post(
                    "/v1/listen",
                    params=params,
                    headers=headers,
                    content=self._stream(audio_path, tracker),
                )
                last_response = response
                if response.status_code == 504 and attempt < 1:
                    await self._sleep(1.0)
                    continue
                if response.status_code in RETRYABLE_STATUSES and attempt < 4:
                    await self._sleep((2**attempt) + random.random())
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt >= 4:
                    raise
                await self._sleep((2**attempt) + random.random())
        assert last_response is not None
        last_response.raise_for_status()
        return last_response

    async def transcribe(
        self,
        audio_path: Path,
        output_dir: Path,
        on_progress: ProgressCallback | None = None,
        on_usage: UsageCallback | None = None,
    ) -> Path:
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        size = audio_path.stat().st_size
        if size > MAX_AUDIO_BYTES:
            raise ValueError("Deepgram принимает файлы размером не более 2 ГБ")
        await emit_progress(on_progress, 5)
        probed_duration = await probe_audio_seconds(audio_path)
        usage = {
            "audio_seconds": int(probed_duration),
            "provider": "deepgram",
            "model": self._model,
        }
        await emit_usage(on_usage, usage)
        tracker = _UploadProgress(size, on_progress)
        timeout = httpx.Timeout(connect=30, write=300, read=660, pool=30)
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            try:
                response = await self._request(client, audio_path, tracker)
            except httpx.HTTPStatusError as exc:
                code = ""
                try:
                    body = exc.response.json()
                    code = str(body.get("err_code") or body.get("code") or "")
                except (ValueError, AttributeError):
                    pass
                if exc.response.status_code in BAD_INPUT_STATUSES or code in BAD_INPUT_CODES:
                    raise ValueError("Deepgram не смог обработать входное аудио") from exc
                if exc.response.status_code in {400, 401, 403}:
                    raise RuntimeError(
                        f"Deepgram отклонил запрос (status={exc.response.status_code})"
                    ) from exc
                raise
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Deepgram вернул некорректный JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Deepgram вернул некорректный JSON")
        captions, effective_duration = build_captions(payload, probed_duration)
        await emit_progress(on_progress, 90)
        if effective_duration > 0:
            await emit_usage(
                on_usage,
                {
                    "audio_seconds": int(effective_duration),
                    "provider": "deepgram",
                    "model": self._model,
                },
            )
        srt_path = write_srt_atomic(output_dir, render_srt(captions))
        await emit_progress(on_progress, 100)
        return srt_path
