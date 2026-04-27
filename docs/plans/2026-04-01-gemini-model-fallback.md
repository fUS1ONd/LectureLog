# Gemini Model Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** При 429 на текущей модели автоматически пробовать следующую модель из списка (на том же ключе), пока не исчерпаются все модели на всех ключах.

**Architecture:** Добавить `GEMINI_MODELS` в config.py (список через запятую). `call_gemini` получает список моделей и при 429 перебирает их по порядку перед тем как пометить ключ заблокированным. Логика: для каждой попытки — пара (ключ, модель); если 429 — сначала меняем модель (оставаясь на том же ключе), потом ключ.

**Tech Stack:** Python asyncio, google-genai, pydantic-settings

---

### Task 1: Обновить config.py и .env

**Files:**
- Modify: `lecturelog/config.py`
- Modify: `.env`
- Modify: `tests/test_config_models.py`

**Step 1: Написать падающий тест**

Добавить в `tests/test_config_models.py`:

```python
def test_gemini_models_parsed_from_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEYS", "key1")
    monkeypatch.setenv("GEMINI_MODELS", "gemini-3-flash-preview,gemini-2.5-flash,gemini-2.5-flash-lite")
    monkeypatch.setenv("UPLOAD_DIR", "/tmp/test")
    from lecturelog.config import Settings
    s = Settings()
    assert s.gemini_models == ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"]

def test_gemini_models_default(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEYS", "key1")
    monkeypatch.setenv("UPLOAD_DIR", "/tmp/test")
    monkeypatch.delenv("GEMINI_MODELS", raising=False)
    from lecturelog.config import Settings
    s = Settings()
    assert s.gemini_models == ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
```

**Step 2: Запустить тест — убедиться что падает**

```bash
source .venv/bin/activate && pytest tests/test_config_models.py::test_gemini_models_parsed_from_env -v
```
Ожидаем: `AttributeError` — нет `gemini_models`.

**Step 3: Добавить GEMINI_MODELS в config.py**

В `lecturelog/config.py` добавить поле и property после `GEMINI_MODEL`:

```python
GEMINI_MODELS: str = "gemini-3-flash-preview,gemini-2.5-flash,gemini-2.5-flash-lite"

@property
def gemini_models(self) -> List[str]:
    return [m.strip() for m in self.GEMINI_MODELS.split(",") if m.strip()]
```

**Step 4: Запустить тесты**

```bash
source .venv/bin/activate && pytest tests/test_config_models.py -v
```
Ожидаем: все PASS.

**Step 5: Обновить .env**

Добавить строку в `.env`:
```
GEMINI_MODELS=gemini-3-flash-preview,gemini-2.5-flash,gemini-2.5-flash-lite
```

Убрать устаревшую строку `GEMINI_MODEL=gemini-2.5-pro` (она больше не используется как основная).

**Step 6: Коммит**

```bash
git add lecturelog/config.py tests/test_config_models.py .env
git commit -m "feat: добавить GEMINI_MODELS с приоритетным списком моделей"
```

---

### Task 2: Обновить call_gemini — перебор моделей при 429

**Files:**
- Modify: `lecturelog/llm/gemini.py`
- Modify: `tests/test_key_pool.py`

**Текущая логика** `call_gemini`: при 429 → `pool.mark_rate_limited(idx)` → следующая попытка берёт другой ключ.

**Новая логика**: при 429 → сначала пробуем следующую модель из списка на том же ключе → если все модели на этом ключе исчерпаны → `pool.mark_rate_limited(idx)` → берём другой ключ с первой моделью.

**Step 1: Написать падающий тест**

Добавить в `tests/test_key_pool.py`:

```python
@pytest.mark.anyio
async def test_call_gemini_falls_back_to_next_model_on_429():
    """При 429 пробует следующую модель на том же ключе."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from lecturelog.llm.gemini import call_gemini
    from lecturelog.llm.key_pool import KeyPool

    call_count = {"n": 0, "models": []}

    def fake_generate(model, contents):
        call_count["n"] += 1
        call_count["models"].append(model)
        if model == "gemini-3-flash-preview":
            raise Exception("429 RESOURCE_EXHAUSTED quota")
        result = MagicMock()
        result.text = "конспект"
        return result

    client = MagicMock()
    client.models.generate_content.side_effect = fake_generate
    pool = KeyPool(clients=[client], rpm_per_key=1000)

    result = await call_gemini(
        pool=pool,
        prompt="тест",
        models=["gemini-3-flash-preview", "gemini-2.5-flash"],
    )

    assert result == "конспект"
    assert call_count["models"] == ["gemini-3-flash-preview", "gemini-2.5-flash"]
```

**Step 2: Запустить тест — убедиться что падает**

```bash
source .venv/bin/activate && pytest tests/test_key_pool.py::test_call_gemini_falls_back_to_next_model_on_429 -v
```
Ожидаем: `TypeError` — `call_gemini` не принимает `models`.

**Step 3: Обновить call_gemini**

Заменить весь `lecturelog/llm/gemini.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

from lecturelog.llm.key_pool import KeyPool

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
                    # Эта модель исчерпала квоту — пробуем следующую
                    continue
                if _is_overload_error(error):
                    await asyncio.sleep(10 * attempt)
                    continue
                raise
        # Все модели на этом ключе исчерпаны — блокируем ключ
        await pool.mark_rate_limited(idx)

    raise RuntimeError(f"Не удалось получить ответ Gemini после {retries} попыток: {last_error}")
```

**Step 4: Запустить тесты**

```bash
source .venv/bin/activate && pytest tests/test_key_pool.py -v
```
Ожидаем: все PASS.

**Step 5: Коммит**

```bash
git add lecturelog/llm/gemini.py tests/test_key_pool.py
git commit -m "feat: перебор моделей Gemini при 429 перед сменой ключа"
```

---

### Task 3: Передать models из config в structurize

**Files:**
- Modify: `lecturelog/pipeline/structurize.py`
- Modify: `lecturelog/pipeline/runner.py`
- Modify: `tests/test_structurize.py`

**Текущая сигнатура** `structurize(... model: str ...)` — один string.
**Новая сигнатура** — `models: list[str]`.

**Step 1: Написать падающий тест**

В `tests/test_structurize.py` найти существующий тест и проверить что он использует `model=`. Добавить новый тест:

```python
@pytest.mark.anyio
async def test_structurize_passes_models_list(tmp_path):
    """structurize передаёт список моделей в call_gemini."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from lecturelog.pipeline.structurize import structurize
    from lecturelog.llm.key_pool import KeyPool

    srt = tmp_path / "t.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nПривет мир\n", encoding="utf-8")

    captured_models = []

    async def fake_call_gemini(pool, prompt, models=None, images=None):
        captured_models.append(models)
        if not captured_models or len(captured_models) == 1:
            return '[{"title":"Раздел 1","start":"00:00:00","end":"00:00:05"}]'
        if len(captured_models) == 2:
            return '{}'
        return "текст раздела"

    client = MagicMock()
    pool = KeyPool(clients=[client], rpm_per_key=1000)

    with patch("lecturelog.pipeline.structurize.call_gemini", side_effect=fake_call_gemini):
        await structurize(
            srt_path=srt,
            slide_images=[],
            output_dir=tmp_path / "out",
            pool=pool,
            models=["gemini-3-flash-preview", "gemini-2.5-flash"],
            on_progress=lambda p: None,
        )

    assert all(m == ["gemini-3-flash-preview", "gemini-2.5-flash"] for m in captured_models)
```

**Step 2: Запустить тест — убедиться что падает**

```bash
source .venv/bin/activate && pytest tests/test_structurize.py::test_structurize_passes_models_list -v
```
Ожидаем: `TypeError` — нет параметра `models`.

**Step 3: Обновить structurize.py**

Заменить сигнатуру функции в `lecturelog/pipeline/structurize.py`:

```python
async def structurize(
    srt_path: Path,
    slide_images: list[Path],
    output_dir: Path,
    pool: KeyPool,
    models: list[str],          # было: model: str
    on_progress: Callable[[int], Any],
) -> list[Section]:
```

И все вызовы `call_gemini(..., model=model)` заменить на `call_gemini(..., models=models)`.

**Step 4: Обновить runner.py**

Заменить:
```python
model=self.config.GEMINI_MODEL,
```
На:
```python
models=self.config.gemini_models,
```

**Step 5: Запустить все тесты**

```bash
source .venv/bin/activate && pytest tests/ -v
```
Ожидаем: все PASS.

**Step 6: Коммит**

```bash
git add lecturelog/pipeline/structurize.py lecturelog/pipeline/runner.py tests/test_structurize.py
git commit -m "feat: передавать список моделей из config в structurize и runner"
```
