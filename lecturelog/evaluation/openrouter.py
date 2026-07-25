"""Reproducible, zero-cost-only OpenRouter transport for evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from lecturelog.evaluation.planner import RequestBudget

VISION_MODEL = "google/gemma-4-26b-a4b-it:free"
TEXT_MODEL = VISION_MODEL
ADJUDICATOR_MODEL = VISION_MODEL
PINNED_MODELS = frozenset({TEXT_MODEL, VISION_MODEL, ADJUDICATOR_MODEL})


class RemoteLlmDisabled(RuntimeError):
    pass


class ModelValidationError(RuntimeError):
    pass


class JudgeResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelRequirement:
    image_input: bool = False
    min_context_length: int = 16_000
    structured_output: bool = True


@dataclass(frozen=True)
class JudgeCallResult:
    value: BaseModel
    requested_model: str
    actual_model: str | None
    cache_key: str
    cached: bool
    actual_model_reported: bool = True
    normalization_warnings: tuple[str, ...] = ()
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ContentAddressedCache:
    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def key(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def load(self, key: str) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def store(self, key: str, payload: Mapping[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / f"{key}.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _is_zero_price(value: Any) -> bool:
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


def validate_model_catalog(
    catalog: Mapping[str, Mapping[str, Any]],
    model: str,
    requirement: ModelRequirement,
) -> None:
    if model == "openrouter/free":
        raise ModelValidationError("openrouter/free is never an implicit evaluator model")
    data = catalog.get(model)
    if data is None:
        raise ModelValidationError(f"Pinned evaluator model is unavailable: {model}")
    pricing = data.get("pricing") or {}
    if not (_is_zero_price(pricing.get("prompt")) and _is_zero_price(pricing.get("completion"))):
        raise ModelValidationError(f"Evaluator model is not zero-cost at runtime: {model}")
    architecture = data.get("architecture") or {}
    modalities = set(architecture.get("input_modalities") or [])
    if requirement.image_input and "image" not in modalities:
        raise ModelValidationError(f"Evaluator model does not advertise image input: {model}")
    if int(data.get("context_length") or 0) < requirement.min_context_length:
        raise ModelValidationError(f"Evaluator model context is too short: {model}")
    supported = set(data.get("supported_parameters") or [])
    has_structured_output = bool({"response_format", "structured_outputs"} & supported)
    if requirement.structured_output and not has_structured_output:
        raise ModelValidationError(f"Evaluator model lacks structured output support: {model}")


class OpenRouterJudgeClient:
    """Sequential client. The caller owns ordering; no calls are spawned concurrently."""

    def __init__(
        self,
        *,
        api_key: str,
        cache: ContentAddressedCache,
        budget: RequestBudget,
        allow_remote: Callable[[], bool],
        http_client: httpx.AsyncClient | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        retries: int = 2,
    ):
        self.api_key = api_key
        self.cache = cache
        self.budget = budget
        self.allow_remote = allow_remote
        self._http = http_client or httpx.AsyncClient(base_url=base_url, timeout=90)
        self.retries = retries
        self._catalog: dict[str, Mapping[str, Any]] | None = None
        self._lock = asyncio.Lock()
        self.attempt_records: list[dict[str, Any]] = []

    async def catalog(self) -> dict[str, Mapping[str, Any]]:
        if self._catalog is None:
            response = await self._http.get("/models")
            response.raise_for_status()
            models = response.json().get("data", [])
            self._catalog = {item["id"]: item for item in models}
        return self._catalog

    async def judge(
        self,
        *,
        model: str,
        requirement: ModelRequirement,
        prompt: str,
        schema: type[BaseModel],
        images: list[str] | None = None,
        prompt_version: str,
        validate_value: Callable[[BaseModel], None] | None = None,
    ) -> JudgeCallResult:
        request_identity = {
            "model": model,
            "prompt": prompt,
            "prompt_version": prompt_version,
            "schema": schema.model_json_schema(),
            "images": images or [],
        }
        cache_key = self.cache.key(request_identity)
        cached = self.cache.load(cache_key)
        if cached is not None:
            result = self._parse_cached(cached, schema, cache_key)
            if validate_value is not None:
                validate_value(result.value)
            return result
        if not self.allow_remote():
            raise RemoteLlmDisabled(
                "Remote LLM evaluation requires explicit allow_remote opt-in; "
                "free providers may retain prompts and outputs"
            )
        if not self.api_key:
            raise RemoteLlmDisabled("OPENROUTER_API_KEY is required for remote evaluation")

        async with self._lock:
            # A second sequential waiter may find the first call's cache entry.
            cached = self.cache.load(cache_key)
            if cached is not None:
                result = self._parse_cached(cached, schema, cache_key)
                if validate_value is not None:
                    validate_value(result.value)
                return result
            validate_model_catalog(await self.catalog(), model, requirement)
            response_data = await self._post_with_transient_retries(
                model=model, prompt=prompt, schema=schema, images=images
            )
            reported_model = response_data.get("model")
            actual_model_reported = bool(reported_model)
            actual_model = str(reported_model) if reported_model else None
            if self.attempt_records:
                self.attempt_records[-1]["actual_model_reported"] = actual_model_reported
            try:
                content = response_data["choices"][0]["message"].get("content")
                value, normalization_warnings = _parse_strict_json_with_warnings(
                    content, schema, model=actual_model or model
                )
                if validate_value is not None:
                    validate_value(value)
            except Exception as error:
                # The HTTP request already has one physical-attempt record.
                # Reclassify that same record when its body is unusable rather
                # than inventing a second request in provenance.
                attempt = self.attempt_records[-1]
                attempt.update(
                    {
                        "status": "validation_error",
                        "error_stage": "response_validation",
                        "error_type": type(error).__name__,
                        "actual_model_reported": actual_model_reported,
                        "normalization_warnings": [],
                    }
                )
                raise
            if self.attempt_records:
                self.attempt_records[-1]["normalization_warnings"] = list(
                    normalization_warnings
                )
            usage = response_data.get("usage") or {}
            stored = {
                "requested_model": model,
                "actual_model": actual_model,
                "actual_model_reported": actual_model_reported,
                "normalization_warnings": list(normalization_warnings),
                "response": value.model_dump(mode="json"),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
            }
            self.cache.store(cache_key, stored)
            return JudgeCallResult(
                value=value,
                requested_model=model,
                actual_model=actual_model,
                actual_model_reported=actual_model_reported,
                normalization_warnings=normalization_warnings,
                cache_key=cache_key,
                cached=False,
                prompt_tokens=stored["prompt_tokens"],
                completion_tokens=stored["completion_tokens"],
            )

    async def _post_with_transient_retries(
        self, *, model: str, prompt: str, schema: type[BaseModel], images: list[str] | None
    ) -> dict[str, Any]:
        content: str | list[dict[str, Any]] = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            content.extend({"type": "image_url", "image_url": {"url": image}} for image in images)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            "provider": {"allow_fallbacks": False},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            # The cap covers physical completion requests, including retries.
            self.budget.consume()
            try:
                response = await self._http.post("/chat/completions", json=payload, headers=headers)
                if response.status_code in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                response.raise_for_status()
                self.attempt_records.append(
                    {
                        "requested_model": model,
                        "attempt_index": attempt + 1,
                        "status": "success",
                        "http_status": response.status_code,
                        "error_stage": None,
                        "actual_model_reported": None,
                        "normalization_warnings": [],
                    }
                )
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as error:
                last_error = error
                self.attempt_records.append(
                    {
                        "requested_model": model,
                        "attempt_index": attempt + 1,
                        "status": (
                            "http_error"
                            if isinstance(error, httpx.HTTPStatusError)
                            else "timeout"
                            if isinstance(error, httpx.TimeoutException)
                            else "network_error"
                        ),
                        "http_status": (
                            error.response.status_code
                            if isinstance(error, httpx.HTTPStatusError)
                            else None
                        ),
                        "error_stage": "transport",
                        "error_type": type(error).__name__,
                        "actual_model_reported": False,
                        "normalization_warnings": [],
                    }
                )
                retryable = not isinstance(error, httpx.HTTPStatusError) or (
                    error.response.status_code in {429, 500, 502, 503, 504}
                )
                if isinstance(error, httpx.HTTPStatusError) and not retryable:
                    excerpt = _sanitized_response_excerpt(
                        error.response.text, api_key=self.api_key
                    )
                    raise JudgeResponseError(
                        f"OpenRouter HTTP {error.response.status_code}: {excerpt}"
                    ) from error
                if attempt >= self.retries:
                    raise
                await asyncio.sleep(0)
        raise RuntimeError(f"OpenRouter request failed: {last_error}")

    @staticmethod
    def _parse_cached(
        cached: Mapping[str, Any], schema: type[BaseModel], cache_key: str
    ) -> JudgeCallResult:
        try:
            value = schema.model_validate(cached["response"], strict=True)
        except (KeyError, ValidationError) as error:
            message = f"Invalid cached judge response {cache_key}: {error}"
            raise JudgeResponseError(message) from error
        return JudgeCallResult(
            value=value,
            requested_model=str(cached["requested_model"]),
            actual_model=(
                str(cached["actual_model"])
                if cached.get("actual_model") is not None
                else None
            ),
            actual_model_reported=bool(cached.get("actual_model_reported", True)),
            normalization_warnings=tuple(cached.get("normalization_warnings", ())),
            cache_key=cache_key,
            cached=True,
            prompt_tokens=int(cached.get("prompt_tokens") or 0),
            completion_tokens=int(cached.get("completion_tokens") or 0),
        )


def _sanitized_response_excerpt(text: str, *, api_key: str, limit: int = 1000) -> str:
    sanitized = text
    if api_key:
        sanitized = sanitized.replace(api_key, "[REDACTED]")
    sanitized = re.sub(r"(?i)\bbearer\s+[^\s\"']+", "Bearer [REDACTED]", sanitized)
    sanitized = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", sanitized)
    sanitized = "".join(
        character if character in "\n\r\t" or character.isprintable() else " "
        for character in sanitized
    ).strip()
    return sanitized[:limit] or "<empty response body>"


def _parse_strict_json(content: Any, schema: type[BaseModel], *, model: str) -> BaseModel:
    value, _ = _parse_strict_json_with_warnings(content, schema, model=model)
    return value


def _parse_strict_json_with_warnings(
    content: Any, schema: type[BaseModel], *, model: str
) -> tuple[BaseModel, tuple[str, ...]]:
    if not isinstance(content, str) or not content.strip():
        raise JudgeResponseError(f"Judge {model} returned empty/non-text JSON content")
    source = content.strip()
    if source.startswith("```"):
        if source.casefold().startswith("```json"):
            source = source[7:].lstrip()
        elif source.startswith("```\n"):
            source = source[4:]
        else:
            raise JudgeResponseError(f"Judge {model} returned an invalid JSON fence")
    decoder = json.JSONDecoder()
    try:
        decoded, end = decoder.raw_decode(source.lstrip())
    except json.JSONDecodeError as error:
        excerpt = source[:240].replace("\n", " ")
        raise JudgeResponseError(
            f"Judge {model} returned invalid JSON at line {error.lineno}, "
            f"column {error.colno}: {error.msg}; excerpt={excerpt!r}"
        ) from error
    trailing = source.lstrip()[end:].strip()
    if trailing.startswith("```"):
        trailing = trailing[3:].strip()
    if trailing and ("{" in trailing or "[" in trailing):
        raise JudgeResponseError(
            f"Judge {model} returned a second JSON structure after JSON"
        )
    warnings = ("trailing_prose_ignored",) if trailing else ()
    try:
        return schema.model_validate(decoded, strict=True), warnings
    except ValidationError as error:
        raise JudgeResponseError(
            f"Judge {model} JSON violates {schema.__name__}: {error}"
        ) from error
