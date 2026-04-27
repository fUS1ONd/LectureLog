from __future__ import annotations

import asyncio
from typing import Any

from lecturelog.llm.key_pool import KeyPool

# Приоритетный список моделей по умолчанию (free tier)
_DEFAULT_MODELS = ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"]


def _is_rate_limit_error(error: Exception) -> bool:
    message = str(error).upper()
    return "429" in message or "RESOURCE_EXHAUSTED" in message


def _is_overload_error(error: Exception) -> bool:
    message = str(error).upper()
    return "503" in message or "UNAVAILABLE" in message


async def call_gemini(
    pool: KeyPool,
    prompt: str,
    models: list[str] | None = None,
    images: list[bytes] | None = None,
    retries: int = 5,
) -> str:
    """Вызов Gemini с перебором моделей при 429, затем перебором ключей."""
    model_list = models if models else _DEFAULT_MODELS
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        client, idx = await pool.acquire()
        overloaded = False
        # Перебираем модели по порядку на одном ключе
        for model in model_list:
            try:
                if images:
                    from google.genai import types  # type: ignore[import-not-found]

                    contents: Any = [
                        *[
                            types.Part.from_bytes(data=image, mime_type="image/png")
                            for image in images
                        ],
                        prompt,
                    ]
                else:
                    contents = prompt

                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model,
                    contents=contents,
                )
                response_text = getattr(response, "text", None)
                if not response_text:
                    raise RuntimeError("Gemini вернул пустой ответ")
                return response_text
            except Exception as error:
                last_error = error
                if _is_rate_limit_error(error):
                    # Квота этой модели исчерпана — пробуем следующую модель
                    continue
                if _is_overload_error(error):
                    # Сервер перегружен целиком — не пробуем другие модели
                    overloaded = True
                    break
                raise
        if overloaded:
            # При перегрузке ждём 2 минуты перед следующей попыткой
            await asyncio.sleep(120)
            continue
        # Все модели на этом ключе исчерпали квоту — блокируем ключ
        await pool.mark_rate_limited(idx)

    raise RuntimeError(f"Не удалось получить ответ Gemini после {retries} попыток: {last_error}")
