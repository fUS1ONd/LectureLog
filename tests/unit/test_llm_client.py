import base64
import json

import httpx
import openai
import pytest

from lecturelog.infrastructure.llm.llm_client import LlmClient
from lecturelog.infrastructure.llm.model_cooldown import ModelCooldown


class FakeCompletions:
    def __init__(self, behaviors):
        self._b = list(behaviors)
        self.calls = 0
        self.kwargs_history: list[dict] = []

    async def create(self, **kwargs):
        self.kwargs_history.append(kwargs)
        b = self._b[self.calls]
        self.calls += 1
        if isinstance(b, Exception):
            raise b
        return b


class FakeChat:
    def __init__(self, b):
        self.completions = FakeCompletions(b)


class FakeAsyncOpenAI:
    def __init__(self, b):
        self.chat = FakeChat(b)


def _resp(text, pt=10, ct=5):
    class M:
        content = text

    class C:
        message = M()

    class U:
        prompt_tokens = pt
        completion_tokens = ct
        total_tokens = pt + ct

    class R:
        choices = [C()]
        usage = U()

    return R()


def _rate_limit_error(raw_metadata: str) -> openai.RateLimitError:
    body = {
        "error": {
            "message": "rate limited",
            "metadata": {"raw": raw_metadata},
        }
    }
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(429, request=request, json=body)
    return openai.RateLimitError(message="rate limited", response=response, body=body)


def _authentication_error(
    *, byok: bool, sdk_unwrapped: bool = False
) -> openai.AuthenticationError:
    error_body = {
        "message": "authentication failed",
        "metadata": {
            "is_byok": byok,
            "provider_name": "Google AI Studio" if byok else "OpenRouter",
        },
    }
    body = error_body if sdk_unwrapped else {"error": error_body}
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(401, request=request, json=body)
    return openai.AuthenticationError(
        message="authentication failed", response=response, body=body
    )


_RPM_RAW = json.dumps(
    {
        "error": {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "GenerateContentPerMinutePerProjectPerModel"}],
                },
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "12s",
                },
            ]
        }
    }
)


@pytest.mark.asyncio
async def test_returns_content():
    client = LlmClient(FakeAsyncOpenAI([_resp("привет")]), ModelCooldown())
    out = await client.call("q", models=["google/gemini-3.5-flash"])
    assert out == "привет"


@pytest.mark.asyncio
async def test_usage_callback_reads_openai_fields():
    seen = []
    client = LlmClient(FakeAsyncOpenAI([_resp("x", pt=12, ct=7)]), ModelCooldown())
    await client.call("q", models=["m1"], on_usage=lambda p: seen.append(p))
    assert seen[0] == {"model": "m1", "prompt": 12, "output": 7}


@pytest.mark.asyncio
async def test_usage_callback_supports_async():
    seen = []

    async def on_usage(payload):
        seen.append(payload)

    client = LlmClient(FakeAsyncOpenAI([_resp("x", pt=1, ct=2)]), ModelCooldown())
    await client.call("q", models=["m1"], on_usage=on_usage)
    assert seen[0] == {"model": "m1", "prompt": 1, "output": 2}


@pytest.mark.asyncio
async def test_forces_byok_provider_and_extra_body():
    fake = FakeAsyncOpenAI([_resp("ok")])
    client = LlmClient(fake, ModelCooldown())
    await client.call("q", models=["m1"], effort="low")
    kwargs = fake.chat.completions.kwargs_history[0]
    assert kwargs["extra_body"]["provider"] == {
        "only": ["google-ai-studio"],
        "allow_fallbacks": False,
    }
    assert kwargs["extra_body"]["reasoning"] == {"effort": "low", "exclude": True}


@pytest.mark.asyncio
async def test_temperature_is_forwarded_only_when_requested():
    fake = FakeAsyncOpenAI([_resp("ok"), _resp("ok")])
    client = LlmClient(fake, ModelCooldown())

    await client.call("q", models=["m1"], temperature=0)
    await client.call("q", models=["m1"])

    assert fake.chat.completions.kwargs_history[0]["temperature"] == 0
    assert "temperature" not in fake.chat.completions.kwargs_history[1]


@pytest.mark.asyncio
async def test_no_reasoning_field_when_effort_none():
    fake = FakeAsyncOpenAI([_resp("ok")])
    client = LlmClient(fake, ModelCooldown())
    await client.call("q", models=["m1"])
    kwargs = fake.chat.completions.kwargs_history[0]
    assert "reasoning" not in kwargs["extra_body"]


@pytest.mark.asyncio
async def test_images_encoded_as_data_urls():
    fake = FakeAsyncOpenAI([_resp("ok")])
    client = LlmClient(fake, ModelCooldown())
    await client.call("q", models=["m1"], images=[b"pngbytes"])
    kwargs = fake.chat.completions.kwargs_history[0]
    content = kwargs["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "q"}
    expected_b64 = base64.b64encode(b"pngbytes").decode()
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{expected_b64}"},
    }


@pytest.mark.asyncio
async def test_images_mime_detected_by_magic_bytes_jpeg():
    fake = FakeAsyncOpenAI([_resp("ok")])
    client = LlmClient(fake, ModelCooldown())
    jpeg_bytes = b"\xff\xd8\xff\xe0restofjpeg"
    await client.call("q", models=["m1"], images=[jpeg_bytes])
    kwargs = fake.chat.completions.kwargs_history[0]
    content = kwargs["messages"][0]["content"]
    expected_b64 = base64.b64encode(jpeg_bytes).decode()
    assert content[1]["image_url"]["url"] == f"data:image/jpeg;base64,{expected_b64}"


@pytest.mark.asyncio
async def test_images_mime_detected_by_magic_bytes_png():
    fake = FakeAsyncOpenAI([_resp("ok")])
    client = LlmClient(fake, ModelCooldown())
    png_bytes = b"\x89PNGrestofpng"
    await client.call("q", models=["m1"], images=[png_bytes])
    kwargs = fake.chat.completions.kwargs_history[0]
    content = kwargs["messages"][0]["content"]
    expected_b64 = base64.b64encode(png_bytes).decode()
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{expected_b64}"


@pytest.mark.asyncio
async def test_response_json_sets_response_format():
    fake = FakeAsyncOpenAI([_resp("{}")])
    client = LlmClient(fake, ModelCooldown())
    await client.call("q", models=["m1"], response_json=True)
    kwargs = fake.chat.completions.kwargs_history[0]
    assert kwargs["response_format"] == {"type": "json_object"}


class SpyModelCooldown(ModelCooldown):
    """ModelCooldown со шпионом на mark_rate_limited.

    Реальная логика (acquire/задержки) не подменяется — записываем только
    аргументы вызова, чтобы проверить (модель, ttl) явно.
    """

    def __init__(self) -> None:
        super().__init__()
        self.marked: list[tuple[str, float]] = []

    async def mark_rate_limited(self, model: str, ttl: float) -> None:
        self.marked.append((model, ttl))
        await super().mark_rate_limited(model, ttl)


@pytest.mark.asyncio
async def test_rate_limit_retries_other_model_and_marks_cooldown():
    cooldown = SpyModelCooldown()
    fake = FakeAsyncOpenAI([_rate_limit_error(_RPM_RAW), _resp("second model ok")])
    client = LlmClient(fake, cooldown)
    out = await client.call("q", models=["m1", "m2"])
    assert out == "second model ok"
    assert fake.chat.completions.calls == 2

    history = fake.chat.completions.kwargs_history
    # ретрай ушёл на другую модель, а не повторно на m1
    assert history[0]["model"] == "m1"
    assert history[1]["model"] == "m2"

    # BYOK provider форсируется в обоих вызовах (и первом, и ретрае)
    expected_provider = {"only": ["google-ai-studio"], "allow_fallbacks": False}
    assert history[0]["extra_body"]["provider"] == expected_provider
    assert history[1]["extra_body"]["provider"] == expected_provider

    # mark_rate_limited вызван ровно один раз, для m1, с ttl из retryDelay="12s"
    assert cooldown.marked == [("m1", 12.0)]

    # первая модель должна остывать
    picked = await cooldown.acquire(["m1", "m2"])
    assert picked == "m2"


@pytest.mark.asyncio
async def test_raises_after_retries_exhausted_with_429_marker():
    cooldown = ModelCooldown()
    fake = FakeAsyncOpenAI([_rate_limit_error(_RPM_RAW) for _ in range(3)])
    client = LlmClient(fake, cooldown)
    with pytest.raises(RuntimeError) as exc_info:
        await client.call("q", models=["m1"], retries=3)
    message = str(exc_info.value)
    assert "429" in message or "RESOURCE_EXHAUSTED" in message


@pytest.mark.asyncio
async def test_non_rate_limit_error_propagates():
    client = LlmClient(FakeAsyncOpenAI([ValueError("boom")]), ModelCooldown())
    with pytest.raises(ValueError):
        await client.call("q", models=["m1"])


@pytest.mark.asyncio
async def test_google_byok_auth_error_falls_back_to_next_model(monkeypatch):
    import lecturelog.infrastructure.llm.llm_client as mod

    monkeypatch.setattr(mod, "_BYOK_AUTH_COOLDOWN_S", 60.0)
    cooldown = SpyModelCooldown()
    fake = FakeAsyncOpenAI([_authentication_error(byok=True), _resp("fallback ok")])
    client = LlmClient(fake, cooldown)

    assert await client.call("q", models=["m1", "m2"]) == "fallback ok"
    assert [item["model"] for item in fake.chat.completions.kwargs_history] == ["m1", "m2"]
    assert cooldown.marked == [("m1", 60.0)]


@pytest.mark.asyncio
async def test_google_byok_auth_error_with_sdk_unwrapped_body_falls_back(monkeypatch):
    import lecturelog.infrastructure.llm.llm_client as mod

    monkeypatch.setattr(mod, "_BYOK_AUTH_COOLDOWN_S", 60.0)
    fake = FakeAsyncOpenAI(
        [_authentication_error(byok=True, sdk_unwrapped=True), _resp("fallback ok")]
    )
    client = LlmClient(fake, ModelCooldown())

    assert await client.call("q", models=["m1", "m2"]) == "fallback ok"


@pytest.mark.asyncio
async def test_openrouter_auth_error_does_not_fallback():
    client = LlmClient(
        FakeAsyncOpenAI([_authentication_error(byok=False)]),
        ModelCooldown(),
    )

    with pytest.raises(openai.AuthenticationError):
        await client.call("q", models=["m1", "m2"])


def _timeout_error() -> openai.APITimeoutError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return openai.APITimeoutError(request=request)


@pytest.mark.asyncio
async def test_network_timeout_retried_without_cooldown(monkeypatch):
    # Разовый сетевой флап: повтор проходит, cooldown модели не выставляется
    import lecturelog.infrastructure.llm.llm_client as mod

    monkeypatch.setattr(mod, "_NETWORK_BACKOFF_S", 0.0)
    cooldown = ModelCooldown()
    fake = FakeAsyncOpenAI([_timeout_error(), _resp("ок")])
    client = LlmClient(fake, cooldown)
    out = await client.call("q", models=["m1"])
    assert out == "ок"
    assert fake.chat.completions.calls == 2


@pytest.mark.asyncio
async def test_network_errors_exhaust_retries(monkeypatch):
    import lecturelog.infrastructure.llm.llm_client as mod

    monkeypatch.setattr(mod, "_NETWORK_BACKOFF_S", 0.0)
    fake = FakeAsyncOpenAI([_timeout_error() for _ in range(3)])
    client = LlmClient(fake, ModelCooldown())
    with pytest.raises(RuntimeError):
        await client.call("q", models=["m1"], retries=3)
