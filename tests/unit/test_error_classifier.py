import httpx
import pytest

from lecturelog.application.error_classifier import classify_error
from lecturelog.domain.enums import ErrorCode


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.groq.com/x")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


@pytest.mark.parametrize("code", [429, 503])
def test_groq_http_status_is_rate_limit(code):
    assert classify_error(_http_status_error(code)) is ErrorCode.RATE_LIMIT


def test_http_status_500_is_internal():
    assert classify_error(_http_status_error(500)) is ErrorCode.INTERNAL


@pytest.mark.parametrize(
    "text",
    [
        "Gemini не дал ответ за 5 попыток: 429 Too Many Requests",
        "RESOURCE_EXHAUSTED quota",
        "503 Service Unavailable",
        "model UNAVAILABLE",
    ],
)
def test_text_signal_is_rate_limit(text):
    assert classify_error(RuntimeError(text)) is ErrorCode.RATE_LIMIT


def test_llm_client_retries_exhausted_is_rate_limit():
    # Регресс: точный текст, который LlmClient (infrastructure/llm/llm_client.py)
    # генерит при исчерпании ретраев (см. `raise RuntimeError(f"OpenRouter не дал
    # ответ за {retries} попыток (429/RESOURCE_EXHAUSTED): {last_error}")`).
    exc = RuntimeError(
        "OpenRouter не дал ответ за 5 попыток (429/RESOURCE_EXHAUSTED): "
        "Error code: 429 - {'error': {'message': 'rate limit exceeded'}}"
    )
    assert classify_error(exc) is ErrorCode.RATE_LIMIT


def test_file_not_found_is_bad_input():
    assert classify_error(FileNotFoundError("Видеофайл не найден: x")) is ErrorCode.BAD_INPUT


def test_value_error_is_bad_input():
    assert (
        classify_error(ValueError("Неподдерживаемый формат слайдов: .xyz")) is ErrorCode.BAD_INPUT
    )


def test_generic_runtime_is_internal():
    assert classify_error(RuntimeError("ffmpeg завершился с ошибкой: ...")) is ErrorCode.INTERNAL
