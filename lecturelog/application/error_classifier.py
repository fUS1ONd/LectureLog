from __future__ import annotations

import httpx

from lecturelog.domain.enums import ErrorCode

# Подстроки-сигналы лимита. LlmClient (infrastructure/llm/llm_client.py) при
# исчерпании ретраев оборачивает исходную ошибку провайдера в RuntimeError с
# текстом вида "OpenRouter не дал ответ за N попыток (429/RESOURCE_EXHAUSTED): ...".
# Ядро классифицирует по тексту, а не по типам SDK — это позволяет распознавать
# лимит независимо от конкретного провайдера/клиента.
_RATE_LIMIT_TOKENS = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")

# Подстроки-сигналы протухших/непринятых YouTube-cookies (текст yt-dlp).
_COOKIES_INVALID_TOKENS = ("SIGN IN TO CONFIRM", "CONFIRM YOU'RE NOT A BOT")


def classify_error(exc: BaseException) -> ErrorCode:
    """Классифицировать исключение пайплайна в машинный код ошибки.

    rate_limit — распознаваемый лимит провайдера (HTTP 429/503 или текстовый
    сигнал RESOURCE_EXHAUSTED/UNAVAILABLE). bad_input — вход битый/не распознан
    (нет файла, неподдерживаемый формат). Остальное — internal."""
    # 1) HTTP-статус от Groq (httpx.HTTPStatusError несёт response.status_code).
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 503):
        return ErrorCode.RATE_LIMIT
    # 2) Типовые сигналы битого/нераспознанного входа.
    if isinstance(exc, (FileNotFoundError, ValueError)):
        return ErrorCode.BAD_INPUT
    # 3) Текстовый сигнал лимита (LlmClient оборачивает last_error в RuntimeError).
    message = str(exc).upper()
    # Сигнал протухших cookies от yt-dlp.
    if any(token in message for token in _COOKIES_INVALID_TOKENS):
        return ErrorCode.COOKIES_INVALID
    if any(token in message for token in _RATE_LIMIT_TOKENS):
        return ErrorCode.RATE_LIMIT
    # 4) Дефолт.
    return ErrorCode.INTERNAL
