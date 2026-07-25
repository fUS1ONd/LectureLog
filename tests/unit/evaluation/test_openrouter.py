import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from lecturelog.evaluation.openrouter import (
    ADJUDICATOR_MODEL,
    TEXT_MODEL,
    VISION_MODEL,
    ContentAddressedCache,
    JudgeResponseError,
    ModelRequirement,
    ModelValidationError,
    OpenRouterJudgeClient,
    RemoteLlmDisabled,
    _parse_strict_json,
    validate_model_catalog,
)
from lecturelog.evaluation.planner import RequestBudget


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int


def test_mvp_policy_pins_all_roles_to_schema_capable_free_gemma():
    expected = "google/gemma-4-26b-a4b-it:free"
    assert TEXT_MODEL == VISION_MODEL == ADJUDICATOR_MODEL == expected


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"score": 7}\n```',
        '{"score": 7}   ',
        '{"score": 7}\nОценка завершена.',
        '```json\n{"score": 7}\n```\nОценка завершена.',
    ],
)
def test_strict_parser_accepts_fences_and_plain_trailing_prose(content):
    assert _parse_strict_json(content, Verdict, model="judge").score == 7


@pytest.mark.parametrize(
    "content",
    [
        '{"score": 7} explanation {details}',
        '{"score": 7}\n{"score": 7}',
        '```json\n{"score": 7}\n```\n```json\n{"score": 7}\n```',
        '{"score": 7} {"score": 8}',
        '{"score": 7} []',
        'before {"score": 7}',
    ],
)
def test_strict_parser_rejects_prose_or_different_trailing_value(content):
    with pytest.raises(JudgeResponseError):
        _parse_strict_json(content, Verdict, model="judge")


def model_entry(model=TEXT_MODEL):
    return {
        "id": model,
        "pricing": {"prompt": "0", "completion": "0.000000"},
        "context_length": 32768,
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["response_format"],
    }


def test_catalog_rejects_non_free_or_missing_capability():
    paid = model_entry()
    paid["pricing"]["completion"] = "0.1"
    with pytest.raises(ModelValidationError, match="not zero-cost"):
        validate_model_catalog({TEXT_MODEL: paid}, TEXT_MODEL, ModelRequirement())
    with pytest.raises(ModelValidationError, match="image input"):
        validate_model_catalog(
            {TEXT_MODEL: model_entry()}, TEXT_MODEL, ModelRequirement(image_input=True)
        )


@pytest.mark.asyncio
async def test_remote_requires_callable_opt_in_before_catalog(tmp_path):
    client = OpenRouterJudgeClient(
        api_key="test",
        cache=ContentAddressedCache(tmp_path),
        budget=RequestBudget("smoke"),
        allow_remote=lambda: False,
    )
    with pytest.raises(RemoteLlmDisabled, match="explicit"):
        await client.judge(
            model=TEXT_MODEL,
            requirement=ModelRequirement(),
            prompt="x",
            schema=Verdict,
            prompt_version="v1",
        )


@pytest.mark.asyncio
async def test_actual_model_cache_resume_and_zero_second_request(tmp_path):
    calls = {"post": 0}

    def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [model_entry()]})
        calls["post"] += 1
        return httpx.Response(
            200,
            json={
                "model": "actual/provider-model:free",
                "choices": [{"message": {"content": json.dumps({"score": 91})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
    )
    budget = RequestBudget("smoke")
    cache = ContentAddressedCache(tmp_path)
    client = OpenRouterJudgeClient(
        api_key="test",
        cache=cache,
        budget=budget,
        allow_remote=lambda: True,
        http_client=http,
    )
    kwargs = {
        "model": TEXT_MODEL,
        "requirement": ModelRequirement(),
        "prompt": "judge this",
        "schema": Verdict,
        "prompt_version": "v1",
    }
    first = await client.judge(**kwargs)
    cache_path = tmp_path / f"{first.cache_key}.json"
    legacy_entry = json.loads(cache_path.read_text(encoding="utf-8"))
    legacy_entry.pop("actual_model_reported")
    cache_path.write_text(json.dumps(legacy_entry), encoding="utf-8")
    second = await client.judge(**kwargs)
    assert first.actual_model == "actual/provider-model:free"
    assert first.actual_model_reported is True
    assert second.actual_model_reported is True
    assert not first.cached and second.cached
    assert calls["post"] == 1
    assert budget.used == 1


@pytest.mark.asyncio
async def test_omitted_actual_model_falls_back_to_pinned_request_and_is_flagged(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [model_entry()]})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"score": 91})}}],
            },
        )

    client = OpenRouterJudgeClient(
        api_key="test",
        cache=ContentAddressedCache(tmp_path),
        budget=RequestBudget("smoke"),
        allow_remote=lambda: True,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
        ),
    )
    result = await client.judge(
        model=TEXT_MODEL,
        requirement=ModelRequirement(),
        prompt="judge this",
        schema=Verdict,
        prompt_version="v1",
    )
    assert result.actual_model is None
    assert result.actual_model_reported is False
    cached = await client.judge(
        model=TEXT_MODEL,
        requirement=ModelRequirement(),
        prompt="judge this",
        schema=Verdict,
        prompt_version="v1",
    )
    assert cached.actual_model is None
    assert cached.actual_model_reported is False


@pytest.mark.asyncio
async def test_strict_json_error_is_actionable(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [model_entry()]})
        return httpx.Response(
            200,
            json={
                "model": TEXT_MODEL,
                "choices": [{"message": {"content": "```json nope"}}],
            },
        )

    client = OpenRouterJudgeClient(
        api_key="test",
        cache=ContentAddressedCache(tmp_path),
        budget=RequestBudget("smoke"),
        allow_remote=lambda: True,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
        ),
    )
    with pytest.raises(JudgeResponseError, match=r"line 1, column 1"):
        await client.judge(
            model=TEXT_MODEL,
            requirement=ModelRequirement(),
            prompt="x",
            schema=Verdict,
            prompt_version="v1",
        )
    assert client.attempt_records[-1]["status"] == "validation_error"
    assert client.attempt_records[-1]["error_stage"] == "response_validation"
    assert len(client.attempt_records) == 1
    assert client.budget.used == 1


@pytest.mark.asyncio
async def test_non_retryable_http_error_surfaces_sanitized_response_body(tmp_path):
    api_key = "sk-secret-example-key"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [model_entry()]})
        return httpx.Response(
            400,
            text=f'{{"error":"response_format unsupported","debug":"Bearer {api_key}"}}',
        )

    client = OpenRouterJudgeClient(
        api_key=api_key,
        cache=ContentAddressedCache(tmp_path),
        budget=RequestBudget("smoke"),
        allow_remote=lambda: True,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
        ),
    )
    with pytest.raises(JudgeResponseError) as caught:
        await client.judge(
            model=TEXT_MODEL,
            requirement=ModelRequirement(),
            prompt="x",
            schema=Verdict,
            prompt_version="v1",
        )
    message = str(caught.value)
    assert "OpenRouter HTTP 400" in message
    assert "response_format unsupported" in message
    assert api_key not in message
    assert "Bearer [REDACTED]" in message
    assert client.attempt_records == [
        {
            "requested_model": TEXT_MODEL,
            "attempt_index": 1,
            "status": "http_error",
            "http_status": 400,
            "error_stage": "transport",
            "error_type": "HTTPStatusError",
            "actual_model_reported": False,
            "normalization_warnings": [],
        }
    ]


@pytest.mark.asyncio
async def test_trailing_prose_warning_is_recorded_in_result_cache_and_attempt(tmp_path):
    calls = {"post": 0}

    def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [model_entry()]})
        calls["post"] += 1
        return httpx.Response(
            200,
            json={
                "model": TEXT_MODEL,
                "choices": [
                    {"message": {"content": '{"score": 91}\\nОценка завершена.'}}
                ],
            },
        )

    client = OpenRouterJudgeClient(
        api_key="test",
        cache=ContentAddressedCache(tmp_path),
        budget=RequestBudget("smoke"),
        allow_remote=lambda: True,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
        ),
    )
    kwargs = {
        "model": TEXT_MODEL,
        "requirement": ModelRequirement(),
        "prompt": "judge",
        "schema": Verdict,
        "prompt_version": "v1",
    }
    first = await client.judge(**kwargs)
    second = await client.judge(**kwargs)
    assert first.normalization_warnings == ("trailing_prose_ignored",)
    assert second.normalization_warnings == ("trailing_prose_ignored",)
    assert client.attempt_records[-1]["normalization_warnings"] == [
        "trailing_prose_ignored"
    ]
    assert calls["post"] == 1


@pytest.mark.asyncio
async def test_retries_each_consume_hard_request_budget(tmp_path):
    calls = {"post": 0}

    def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [model_entry()]})
        calls["post"] += 1
        return httpx.Response(429, json={"error": "quota"})

    client = OpenRouterJudgeClient(
        api_key="test",
        cache=ContentAddressedCache(tmp_path),
        budget=RequestBudget("smoke", max_requests=1),
        allow_remote=lambda: True,
        retries=3,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
        ),
    )
    with pytest.raises(Exception, match="cap reached"):
        await client.judge(
            model=TEXT_MODEL,
            requirement=ModelRequirement(),
            prompt="x",
            schema=Verdict,
            prompt_version="v1",
        )
    assert calls["post"] == 1
