from __future__ import annotations

import asyncio

from lecturelog.llm.key_pool import KeyPool


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
                from google.genai import types

                contents = [
                    *[types.Part.from_bytes(data=img, mime_type="image/png") for img in images],
                    prompt,
                ]
            else:
                contents = prompt

            response = client.models.generate_content(model=pool.model, contents=contents)
            text = getattr(response, "text", "")
            if not isinstance(text, str):
                raise RuntimeError("Gemini returned non-text response")
            return text
        except Exception as exc:
            last_error = exc
            err = str(exc)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                pool.mark_rate_limited(idx)
                continue
            if "503" in err or "UNAVAILABLE" in err:
                await asyncio.sleep(10 * attempt)
                continue
            raise

    raise RuntimeError(f"Не удалось получить ответ от Gemini после {retries} попыток: {last_error}")
