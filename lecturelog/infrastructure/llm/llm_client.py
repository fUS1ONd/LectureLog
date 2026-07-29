"""Клиент LLM поверх OpenRouter (openai SDK), форс BYOK на google-ai-studio.

Контракт `call(...)` совместим со старым `GeminiClient.call` — им пользуется
`structurizer` (перенос потребителя — Задача 6). Внутри вместо google-genai
используется `openai.AsyncOpenAI`, указывающий на OpenRouter, с принудительным
провайдером через `extra_body["provider"]` (нестандартное поле, до OpenRouter
доходит только так — обычные kwargs openai SDK не проксируются).
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import openai

from lecturelog.infrastructure.llm.model_cooldown import ModelCooldown
from lecturelog.infrastructure.llm.rate_limit import parse_cooldown_ttl

logger = logging.getLogger(__name__)

UsageCallback = Callable[[dict], Any]

_DEFAULT_MAX_TOKENS = 65536
_BYOK_PROVIDER = {"only": ["google-ai-studio"], "allow_fallbacks": False}
# Бэк-офф между ретраями сетевых ошибок (ConnectTimeout и т.п.): разовый флап
# сети не должен ронять всю задачу — повтор почти всегда проходит.
_NETWORK_BACKOFF_S = 2.0
_BYOK_AUTH_COOLDOWN_S = 300.0
# Короткая пауза для модели, чей ответ оборвался или чей апстрим отдал 5xx:
# следующая попытка должна уйти на другую модель, но выводить эту из ротации
# надолго незачем — отказ транзиентный.
_UPSTREAM_ERROR_COOLDOWN_S = 60.0


async def _emit_usage(on_usage: UsageCallback | None, payload: dict) -> None:
    if on_usage is None:
        return
    maybe_awaitable = on_usage(payload)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


def _extract_rate_limit_raw(error: openai.RateLimitError) -> str:
    """Достать error.metadata.raw (JSON-строка Google) из тела 429-ответа.

    Тело может быть уже распарсенным dict (`err.body`) либо доступно только
    через `err.response` (httpx.Response). Пробуем оба пути защитно —
    любой сбой возвращает "", что даёт parse_cooldown_ttl фолбэк 60с.
    """
    body: Any = getattr(error, "body", None)
    if body is None:
        response = getattr(error, "response", None)
        if response is not None:
            try:
                body = response.json()
            except Exception:
                body = None
    try:
        if isinstance(body, str):
            body = json.loads(body)
        raw = body.get("error", {}).get("metadata", {}).get("raw", "")
        return raw if isinstance(raw, str) else ""
    except Exception:
        return ""


def _is_google_byok_auth_error(error: openai.AuthenticationError) -> bool:
    """True only for a provider-side Google BYOK credential failure.

    An invalid OpenRouter API key must still propagate immediately.  OpenRouter
    identifies a failed bound Google credential in error.metadata.
    """
    body: Any = getattr(error, "body", None)
    try:
        if isinstance(body, str):
            body = json.loads(body)
        error_body = body.get("error", body)
        metadata = error_body.get("metadata", {})
        return (
            metadata.get("is_byok") is True and metadata.get("provider_name") == "Google AI Studio"
        )
    except (AttributeError, ValueError, TypeError):
        return False


def _detect_image_mime(image: bytes) -> str:
    """Определяет MIME по магическим байтам. По умолчанию — png (обратная совместимость)."""
    if image.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if image.startswith(b"\x89PNG"):
        return "image/png"
    return "image/png"


def _build_messages(prompt: str, images: list[bytes] | None) -> list[dict]:
    if not images:
        return [{"role": "user", "content": prompt}]
    content: list[dict] = [{"type": "text", "text": prompt}]
    for image in images:
        b64 = base64.b64encode(image).decode()
        mime = _detect_image_mime(image)
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    return [{"role": "user", "content": content}]


class LlmClient:
    """Тонкая обёртка над AsyncOpenAI (OpenRouter) с cooldown-ретраями на 429."""

    def __init__(
        self,
        async_openai_client: Any,
        cooldown: ModelCooldown,
        *,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._client = async_openai_client
        self._cooldown = cooldown
        self._max_tokens = max_tokens

    async def call(
        self,
        prompt: str,
        models: list[str],
        images: list[bytes] | None = None,
        *,
        on_usage: UsageCallback | Callable[[dict], Awaitable[None]] | None = None,
        response_json: bool = False,
        response_schema: dict | None = None,
        response_schema_name: str = "response",
        effort: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int = 5,
    ) -> str:
        messages = _build_messages(prompt, images)
        extra_body: dict[str, Any] = {"provider": dict(_BYOK_PROVIDER)}
        if effort is not None:
            extra_body["reasoning"] = {"effort": effort, "exclude": True}

        last_error: Exception | None = None
        last_reason = "429/RESOURCE_EXHAUSTED"
        for attempt in range(retries):
            model = await self._cooldown.acquire(models)
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens or self._max_tokens,
                "extra_body": extra_body,
            }
            if response_schema is not None:
                # strict-режим: провайдер обязан вернуть все поля схемы, тогда как
                # json_object гарантирует лишь синтаксически валидный JSON.
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_schema_name,
                        "strict": True,
                        "schema": response_schema,
                    },
                }
            elif response_json:
                kwargs["response_format"] = {"type": "json_object"}
            if temperature is not None:
                kwargs["temperature"] = temperature
            try:
                resp = await self._client.chat.completions.create(**kwargs)
            except openai.RateLimitError as error:
                last_error = error
                raw = _extract_rate_limit_raw(error)
                ttl, _kind = parse_cooldown_ttl(
                    raw, seconds_to_midnight=self._cooldown.seconds_to_midnight()
                )
                await self._cooldown.mark_rate_limited(model, ttl)
                continue
            except openai.AuthenticationError as error:
                if not _is_google_byok_auth_error(error):
                    raise
                last_error = error
                logger.warning(
                    "Google BYOK credential rejected for %s; trying next model",
                    model,
                )
                await self._cooldown.mark_rate_limited(model, _BYOK_AUTH_COOLDOWN_S)
                continue
            except openai.InternalServerError as error:
                # 5xx апстрима («high demand», UNAVAILABLE) транзиентен и не связан
                # с запросом: пока есть другие модели, задача падать не должна.
                last_error = error
                last_reason = f"HTTP {getattr(error, 'status_code', '5xx')} апстрима"
                logger.warning(
                    "Апстрим недоступен (%s), попытка %d/%d: %s",
                    model,
                    attempt + 1,
                    retries,
                    error,
                )
                await self._cooldown.mark_rate_limited(model, _UPSTREAM_ERROR_COOLDOWN_S)
                # Апстрим сам просит подождать, поэтому ретрай без паузы сжёг бы
                # все попытки за доли секунды.
                await asyncio.sleep(_NETWORK_BACKOFF_S * (attempt + 1))
                continue
            except (openai.APITimeoutError, openai.APIConnectionError) as error:
                # Сетевой флап (не 429): модель не виновата, cooldown не трогаем —
                # ждём с нарастающим бэк-оффом и пробуем снова.
                last_error = error
                logger.warning(
                    "Сетевая ошибка OpenRouter (%s), попытка %d/%d: %s",
                    model,
                    attempt + 1,
                    retries,
                    error,
                )
                await asyncio.sleep(_NETWORK_BACKOFF_S * (attempt + 1))
                continue

            # Провайдер может отвалиться до начала генерации: тогда OpenRouter
            # отдаёт 200 с телом из одной ошибки, а `choices` приходит пустым или
            # отсутствует вовсе. Это отказ, а не ответ, и обращение к choices[0]
            # роняло всю задачу с TypeError.
            choices = getattr(resp, "choices", None)
            if not choices:
                resp_error = getattr(resp, "error", None)
                last_error = RuntimeError(
                    f"ответ модели {model} пришёл без choices (error={resp_error})"
                )
                last_reason = "ответы апстрима без choices"
                logger.warning("%s; пробуем следующую модель", last_error)
                await self._cooldown.mark_rate_limited(model, _UPSTREAM_ERROR_COOLDOWN_S)
                continue

            choice = choices[0]
            # Апстрим может оборвать генерацию посреди потока: HTTP-ошибки нет,
            # приходит 200 с частичным контентом, нулевым usage и признаком отказа.
            # Так выглядят RECITATION-фильтр Gemini и 503 provider_overloaded.
            # Без этой проверки обрывок уходил наверх как валидный ответ и ронял
            # разбор схемы, что выглядело как «модель не соблюдает схему».
            choice_error = getattr(choice, "error", None)
            if getattr(choice, "finish_reason", None) == "error" or choice_error:
                native = getattr(choice, "native_finish_reason", None)
                last_error = RuntimeError(
                    f"ответ модели {model} оборван апстримом "
                    f"(native_finish_reason={native}, error={choice_error})"
                )
                last_reason = "оборванные ответы апстрима"
                logger.warning("%s; пробуем следующую модель", last_error)
                # Повтор той же модели бесполезен: RECITATION детерминирован для
                # данного промпта, а перегрузка провайдера не проходит мгновенно.
                await self._cooldown.mark_rate_limited(model, _UPSTREAM_ERROR_COOLDOWN_S)
                continue

            text = getattr(choice.message, "content", None)
            if not text:
                raise RuntimeError(f"Пустой ответ от модели {model}")

            usage = getattr(resp, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", 0)
            await _emit_usage(
                on_usage,
                {"model": model, "prompt": prompt_tokens, "output": completion_tokens},
            )
            return text

        raise RuntimeError(
            f"OpenRouter не дал ответ за {retries} попыток ({last_reason}): {last_error}"
        )
