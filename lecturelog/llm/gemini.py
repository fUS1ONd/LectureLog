from __future__ import annotations

import asyncio
import os
from typing import Any

from lecturelog.llm.key_pool import KeyPool

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")


def _is_rate_limit_error(error: Exception) -> bool:
    message = str(error).upper()
    return "429" in message or "RESOURCE_EXHAUSTED" in message


def _is_overload_error(error: Exception) -> bool:
    message = str(error).upper()
    return "503" in message or "UNAVAILABLE" in message


async def call_gemini(
    pool: KeyPool,
    prompt: str,
    images: list[bytes] | None = None,
    retries: int = 5,
) -> str:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        client, idx = await pool.acquire()
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

            response = client.models.generate_content(
                model=DEFAULT_GEMINI_MODEL,
                contents=contents,
            )
            response_text = getattr(response, "text", None)
            if not response_text:
                raise RuntimeError("Gemini вернул пустой ответ")
            return response_text
        except Exception as error:
            last_error = error
            if _is_rate_limit_error(error):
                pool.mark_rate_limited(idx)
                continue
            if _is_overload_error(error):
                await asyncio.sleep(10 * attempt)
                continue
            raise

    raise RuntimeError(f"Не удалось получить ответ Gemini после {retries} попыток: {last_error}")

