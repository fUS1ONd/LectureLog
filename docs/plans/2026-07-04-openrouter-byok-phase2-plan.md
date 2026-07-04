# OpenRouter BYOK — Фаза 2 (транспорт): план реализации

> **For Claude:** REQUIRED SUB-SKILL: используй `superpowers:executing-plans` для
> исполнения плана задача-за-задачей. Оркестрация — через сабагентов (см. раздел
> «Оркестрация»).

**Goal:** Перевести LLM-транспорт ядра lecturelog с прямого `google.genai` на OpenRouter
BYOK через `openai` SDK, заменить `KeyPool` на `ModelCooldown` с точным TTL из тела 429,
сохранив внешний контракт стадий (split/subsplit/render).

**Architecture:** Один `AsyncOpenAI`-клиент (base_url OpenRouter, ключ из env). Каждый
запрос форсит BYOK (`provider.only=google-ai-studio, allow_fallbacks=false`) → при
исчерпании ключа честный 429. `ModelCooldown` (process-wide синглтон в `app.state`)
парсит `error.metadata.raw` (JSON-строка Google), различает RPM/RPD по `quotaId` и
выставляет cooldown модели: RPM → `retryDelay`, RPD → до полуночи Pacific, иначе фикс-60с.
`acquire(models)` отдаёт первую не-остывающую модель списка стадии.

**Tech Stack:** Python 3.12, `openai` (AsyncOpenAI), `pytest`/`pytest-asyncio`, pydantic
`BaseSettings`, FastAPI lifespan. Тесты — чистые фейки (`FakeClock`, mock AsyncOpenAI),
без реального сетевого вызова.

**Base branch:** worktree от `origin/main`. ВНИМАНИЕ: актуальный дизайн и часть правок
ядра живут на `dev`. Перед стартом свериться: `git log dev` и дизайн-документ
`docs/plans/2026-07-04-openrouter-gemini-byok-design.md`.

---

## Контекст из spike (фаза 1 — выполнена, НЕ переисследовать)

Всё проверено живыми запросами к OpenRouter 2026-07-04. Опорные факты для кода:

1. **Транспорт**: `POST {base_url}/chat/completions`, OpenAI-совместимо. Ответ:
   `choices[0].message.content`, `usage.prompt_tokens`/`.completion_tokens`/`.total_tokens`,
   `usage.is_byok`, `usage.cost`.
2. **Форс BYOK (обязательно в каждом запросе)** — иначе OpenRouter молча уходит на платный
   Vertex-пул и списывает деньги (проверено: баланс −$0.01):
   ```json
   "provider": {"only": ["google-ai-studio"], "allow_fallbacks": false}
   ```
3. **429 приходит в теле** (НЕ в заголовках, `Retry-After` отсутствует):
   ```json
   {"error": {"code": 429, "message": "Provider returned error",
     "metadata": {"raw": "<JSON-СТРОКА Google>", "is_byok": true}}}
   ```
   `metadata.raw` парсится вторым `json.loads`. Внутри `details[]`:
   - `QuotaFailure.violations[0].quotaId`: `...PerMinute...` = RPM / `...PerDay...` = RPD.
   - `RetryInfo.retryDelay` (напр. `"38s"`). Для RPM — реалистичен. Для RPD — ВРЁТ
     (прыгает 22↔58с), реальный сброс в полночь Pacific → игнорировать.
4. **Reasoning**: по умолчанию Gemini-flash НЕ рассуждает. Чтобы включить и не возвращать
   мысли — пара `reasoning:{"effort":"<low|medium|high>", "exclude":true}`. `effort` —
   per-stage конфиг. При активном reasoning — щедрый `max_tokens` (иначе обрезка).
5. **JSON-mode**: `response_format:{"type":"json_object"}` → чистый JSON. Работает.
6. **Картинки**: `content:[{"type":"text","text":...},{"type":"image_url","image_url":
   {"url":"data:image/png;base64,..."}}]`. Текст перед картинками.
7. **Модели** (все доступны, +префикс `google/`): `google/gemini-3.5-flash`,
   `google/gemini-3-flash-preview`, `google/gemini-3.1-flash-lite`. `*-image` не трогаем.

## Границы фазы 2

**Входит:** `LlmClient` (openai SDK, текст+картинки), `ModelCooldown` (+парсер 429),
конфиг `LlmConfig`, зависимости, `lifespan`, адаптация `structurizer`/`error_classifier`,
тесты.

**НЕ входит (фаза 3, отдельно):** удаление `video_provider.py` и `google.genai` из
зависимостей, чистка `video_slides` из `routes`/`lifespan`/`app.state`, OpenAPI-контракт.
**НО:** фаза 2 не должна ломать сборку — `video_provider` временно оставляем
компилируемым (см. Задачу 8: заглушка/isolation).

---

## Оркестрация (как исполнять)

Исполнитель — **оркестратор**, который на каждую Задачу N дифспетчит **свежий сабагент**
(`superpowers:subagent-driven-development`) и делает ревью между задачами:

1. Оркестратор читает эту задачу и передаёт сабагенту: файлы, шаги, ожидаемый вывод.
2. Сабагент выполняет TDD-цикл (тест → фейл → код → пасс → коммит) **одной задачи**.
3. Оркестратор проверяет результат (`superpowers:requesting-code-review` для крупных
   задач: 3, 4, 6), при проблемах — правит или передаёт назад.
4. Только после зелёного — следующая задача. Не батчить несколько задач в один сабагент.

**Порядок задач жёсткий** (зависимости): 1→2→3→4→5→6→7→8. Задачи 1-2 (конфиг, зависимости)
можно одним сабагентом. Задача 3 (`ModelCooldown`) — самая сложная, отдельный сабагент +
ревью. Задача 4 (`LlmClient`) зависит от 3.

**Коммиты**: после каждой задачи. Сообщения на русском, БЕЗ упоминания авторства ИИ.

---

## Задача 1: Зависимости — openai in, подготовка к удалению google-genai

**Files:**
- Modify: `pyproject.toml` (секция dependencies)

**Step 1: Добавить `openai`**

В `[project].dependencies` добавить строку (версию взять актуальную стабильную):
```
"openai>=1.55",
```
`google-genai` пока **НЕ удалять** — его ещё держит `video_provider.py` (уйдёт в фазе 3).

**Step 2: Синхронизировать окружение**

Run: `uv sync` (или `pip install -e .`, как принято в проекте — свериться с Makefile/README)
Expected: `openai` установлен, ошибок нет.

**Step 3: Проверить импорт**

Run: `python -c "from openai import AsyncOpenAI; print('ok')"`
Expected: `ok`

**Step 4: Commit**
```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): добавить openai SDK для транспорта OpenRouter"
```

---

## Задача 2: Конфиг — GeminiConfig → LlmConfig

**Files:**
- Modify: `lecturelog/config/settings.py` (класс `GeminiConfig`, строки ~24-62)
- Test: `tests/unit/test_settings_llm.py` (создать)

**Контекст:** сейчас `GeminiConfig` с alias `GEMINI_*`. Переименовываем в `LlmConfig`
(атрибут `cfg.llm`), env-алиасы `LLM_*` + новый `OPENROUTER_API_KEY`/`OPENROUTER_BASE_URL`,
per-stage `effort`. Модели хранятся С префиксом `google/`.

**Step 1: Написать падающий тест**

`tests/unit/test_settings_llm.py`:
```python
import pytest
from lecturelog.config.settings import LlmConfig


def test_llm_config_reads_openrouter_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("LLM_MODELS_SPLIT", "google/gemini-3.5-flash,google/gemini-3-flash-preview")
    cfg = LlmConfig()
    assert cfg.openrouter_key == "sk-or-test"
    assert cfg.base_url == "https://openrouter.ai/api/v1"  # дефолт
    assert cfg.split_models == ["google/gemini-3.5-flash", "google/gemini-3-flash-preview"]


def test_llm_config_effort_per_stage_defaults(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    cfg = LlmConfig()
    # effort по стадиям — строки low|medium|high, дефолты подобраны в дизайне
    assert cfg.effort_split in ("low", "medium", "high")
    assert cfg.effort_render in ("low", "medium", "high")
```

**Step 2: Запустить — упадёт**

Run: `pytest tests/unit/test_settings_llm.py -v`
Expected: FAIL (`ImportError: cannot import name 'LlmConfig'`)

**Step 3: Реализация**

В `settings.py` заменить `GeminiConfig` на `LlmConfig`:
```python
class LlmConfig(BaseSettings):
    model_config = _BASE
    openrouter_key: str = Field(alias="OPENROUTER_API_KEY")
    base_url: str = Field("https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")

    models_split: str = Field(
        "google/gemini-3.5-flash,google/gemini-3-flash-preview", alias="LLM_MODELS_SPLIT"
    )
    models_subsplit: str = Field(
        "google/gemini-3.5-flash,google/gemini-3-flash-preview", alias="LLM_MODELS_SUBSPLIT"
    )
    models_render: str = Field(
        "google/gemini-3.1-flash-lite,google/gemini-3.5-flash,google/gemini-3-flash-preview",
        alias="LLM_MODELS_RENDER",
    )
    concurrency_subsplit: int = Field(2, alias="LLM_CONCURRENCY_SUBSPLIT")
    concurrency_render: int = Field(5, alias="LLM_CONCURRENCY_RENDER")
    # reasoning effort по стадиям (дизайн: подобрать; старт — консервативно)
    effort_split: str = Field("low", alias="LLM_EFFORT_SPLIT")
    effort_subsplit: str = Field("low", alias="LLM_EFFORT_SUBSPLIT")
    effort_render: str = Field("low", alias="LLM_EFFORT_RENDER")

    @property
    def split_models(self) -> list[str]:
        return _split_csv(self.models_split)

    @property
    def subsplit_models(self) -> list[str]:
        return _split_csv(self.models_subsplit)

    @property
    def render_models(self) -> list[str]:
        return _split_csv(self.models_render)
```
Удалить: `api_keys_raw`/`keys`, `models_video_slides`/`video_slides_models`,
`concurrency_video`. В корневом `AppConfig` заменить поле `gemini: GeminiConfig` на
`llm: LlmConfig` (найти по grep `gemini:` / `GeminiConfig` в `settings.py`).

**Step 4: Тест проходит**

Run: `pytest tests/unit/test_settings_llm.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add lecturelog/config/settings.py tests/unit/test_settings_llm.py
git commit -m "feat(config): LlmConfig с OpenRouter env и per-stage effort"
```

---

## Задача 3: ModelCooldown — парсер 429 + RPM/RPD TTL (КЛЮЧЕВАЯ, +ревью)

**Files:**
- Create: `lecturelog/infrastructure/llm/model_cooldown.py`
- Create: `lecturelog/infrastructure/llm/rate_limit.py` (парсер 429 — отдельно, тестируемо)
- Test: `tests/unit/test_rate_limit.py`, `tests/unit/test_model_cooldown.py`
- Reuse: перенести из `key_pool.py` хелперы `pacific_date`,
  `seconds_until_pacific_midnight` (они уже есть и корректны — не переписывать логику,
  скопировать в `model_cooldown.py` или общий модуль).

**Контекст:** различение RPM/RPD — сердце фазы. Парсер отделён от cooldown, чтобы
тестировать на живых образцах тел из spike. Cooldown — process-wide, `asyncio.Lock`.

### Часть A: парсер (`rate_limit.py`)

**Step 1: Падающий тест** `tests/unit/test_rate_limit.py`:
```python
from lecturelog.infrastructure.llm.rate_limit import parse_cooldown_ttl

# Реальные образцы из spike (2026-07-04).
RPD_RAW = '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":[' \
    '{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":[' \
    '{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},' \
    '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"58s"}]}}'
RPM_RAW = '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":[' \
    '{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":[' \
    '{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},' \
    '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"38s"}]}}'


def test_rpm_uses_retry_delay():
    ttl, kind = parse_cooldown_ttl(RPM_RAW, seconds_to_midnight=1000.0)
    assert kind == "rpm"
    assert 30 <= ttl <= 60  # retryDelay 38s

def test_rpd_uses_midnight_not_retry_delay():
    ttl, kind = parse_cooldown_ttl(RPD_RAW, seconds_to_midnight=1000.0)
    assert kind == "rpd"
    assert ttl == 1000.0  # игнорируем врущий retryDelay 58s

def test_unparseable_falls_back_to_60():
    ttl, kind = parse_cooldown_ttl("не json", seconds_to_midnight=1000.0)
    assert kind == "unknown"
    assert ttl == 60.0
```

**Step 2: Запустить — фейл.** `pytest tests/unit/test_rate_limit.py -v` → FAIL.

**Step 3: Реализация** `rate_limit.py`:
```python
from __future__ import annotations
import json
import re

_FALLBACK_TTL = 60.0


def parse_cooldown_ttl(raw: str, *, seconds_to_midnight: float) -> tuple[float, str]:
    """Из строки metadata.raw (формат ошибки Google) вернуть (ttl_сек, вид).

    вид: 'rpm' | 'rpd' | 'unknown'. RPM → retryDelay; RPD → до полуночи Pacific
    (retryDelay для RPD врёт); иначе → фикс-60с. Парсинг защитный: любой сбой → unknown.
    """
    try:
        data = json.loads(raw)
        details = data.get("error", {}).get("details", [])
        quota_id = ""
        retry_delay = None
        for d in details:
            t = d.get("@type", "")
            if "QuotaFailure" in t:
                viol = d.get("violations", [{}])
                quota_id = viol[0].get("quotaId", "") if viol else ""
            elif "RetryInfo" in t:
                rd = d.get("retryDelay", "")  # напр. "38s"
                m = re.match(r"(\d+(?:\.\d+)?)s", rd)
                if m:
                    retry_delay = float(m.group(1))
        if "PerDay" in quota_id:
            return seconds_to_midnight, "rpd"
        if "PerMinute" in quota_id:
            return (retry_delay if retry_delay is not None else _FALLBACK_TTL), "rpm"
        return _FALLBACK_TTL, "unknown"
    except Exception:
        return _FALLBACK_TTL, "unknown"
```

**Step 4: Тест проходит.** PASS.

**Step 5: Commit** `feat(llm): парсер TTL из 429-тела OpenRouter (RPM/RPD)`

### Часть B: ModelCooldown

**Step 6: Падающий тест** `tests/unit/test_model_cooldown.py` (паттерн `FakeClock` из
существующего `test_key_pool.py`):
```python
import pytest
from lecturelog.infrastructure.llm.model_cooldown import ModelCooldown


class FakeClock:
    def __init__(self): self.t = 1_700_000_000.0
    def __call__(self): return self.t
    def advance(self, s): self.t += s


@pytest.mark.asyncio
async def test_acquire_returns_first_model():
    cd = ModelCooldown(time_func=FakeClock())
    assert await cd.acquire(["A", "B"]) == "A"

@pytest.mark.asyncio
async def test_cooldown_skips_to_next_model():
    clock = FakeClock()
    cd = ModelCooldown(time_func=clock)
    await cd.mark_rate_limited("A", ttl=30.0)
    assert await cd.acquire(["A", "B"]) == "B"  # A остывает
    clock.advance(31)
    assert await cd.acquire(["A", "B"]) == "A"  # остыла

@pytest.mark.asyncio
async def test_all_cooling_returns_least_cooling():
    # когда все модели остывают — вернуть ту, что освободится раньше (не падать)
    clock = FakeClock()
    cd = ModelCooldown(time_func=clock)
    await cd.mark_rate_limited("A", ttl=10.0)
    await cd.mark_rate_limited("B", ttl=50.0)
    assert await cd.acquire(["A", "B"]) == "A"
```

**Step 7: Фейл.** `pytest tests/unit/test_model_cooldown.py -v` → FAIL.

**Step 8: Реализация** `model_cooldown.py`:
```python
from __future__ import annotations
import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
_PACIFIC = ZoneInfo("America/Los_Angeles")


def seconds_until_pacific_midnight(epoch: float) -> float:
    now = datetime.fromtimestamp(epoch, tz=_PACIFIC)
    tomorrow = (now + timedelta(days=1)).date()
    midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=_PACIFIC)
    return (midnight - now).total_seconds()


class ModelCooldown:
    """Process-wide реактивный cooldown моделей при 429.

    acquire(models) — первая не-остывающая модель списка (порядок = приоритет).
    Если все остывают — та, что освободится раньше (fallback, не падаем).
    """

    def __init__(self, time_func: Callable[[], float] | None = None) -> None:
        self._time = time_func or time.time
        self._blocked_until: dict[str, float] = defaultdict(float)
        self._lock = asyncio.Lock()

    async def acquire(self, models: list[str]) -> str:
        async with self._lock:
            now = self._time()
            soonest, soonest_at = models[0], float("inf")
            for m in models:
                if now >= self._blocked_until[m]:
                    return m
                if self._blocked_until[m] < soonest_at:
                    soonest, soonest_at = m, self._blocked_until[m]
            logger.warning("все модели остывают, беру %s (раньше всех освободится)", soonest)
            return soonest

    async def mark_rate_limited(self, model: str, ttl: float) -> None:
        async with self._lock:
            self._blocked_until[model] = self._time() + ttl

    def seconds_to_midnight(self) -> float:
        return seconds_until_pacific_midnight(self._time())
```

**Step 9: Пасс.** PASS.

**Step 10: Commit** `feat(llm): ModelCooldown — process-wide cooldown моделей при 429`

**Step 11: ОРКЕСТРАТОР — ревью** (`superpowers:requesting-code-review`): проверить
парсер на устойчивость к битому raw, отсутствие гонок в `acquire`, корректность
RPD→midnight. Только после зелёного — Задача 4.

---

## Задача 4: LlmClient на openai SDK (текст + картинки)

**Files:**
- Create: `lecturelog/infrastructure/llm/llm_client.py`
- Test: `tests/unit/test_llm_client.py`
- Reference: старый `gemini_client.py` (контракт `generate`/`call`), удалить его в конце
  задачи (после переноса потребителей — но потребитель `structurizer` в Задаче 6, поэтому
  здесь только СОЗДАЁМ новый, старый НЕ удаляем).

**Контекст:** контракт сохраняем: `call(prompt, models, images=None, on_usage=None,
response_json=False, effort=None)` → `str`. Внутри — `AsyncOpenAI`, форс BYOK, при 429
парсим тело → `ModelCooldown.mark_rate_limited` → ретрай другой моделью.

**Step 1: Падающий тест** `tests/unit/test_llm_client.py` (мок AsyncOpenAI):
```python
import pytest
from lecturelog.infrastructure.llm.llm_client import LlmClient
from lecturelog.infrastructure.llm.model_cooldown import ModelCooldown


class FakeCompletions:
    def __init__(self, behaviors): self._b = list(behaviors); self.calls = 0
    async def create(self, **kwargs):
        b = self._b[self.calls]; self.calls += 1
        if isinstance(b, Exception): raise b
        return b  # готовый объект-ответ

class FakeChat:
    def __init__(self, b): self.completions = FakeCompletions(b)
class FakeAsyncOpenAI:
    def __init__(self, b): self.chat = FakeChat(b)

def _resp(text, pt=10, ct=5):
    class M: content = text
    class C: message = M()
    class U: prompt_tokens = pt; completion_tokens = ct; total_tokens = pt + ct
    class R: choices = [C()]; usage = U()
    return R()


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
```
Плюс тест: 429-исключение (эмулировать `openai` ошибку с телом, несущим RPM raw) →
второй вызов другой моделью успешен, `ModelCooldown` получил mark. Точную форму
исключения `openai.RateLimitError`/`APIStatusError` свериться с версией SDK (у ошибки
есть `.response`/`.body` с телом — распарсить `error.metadata.raw`).

**Step 2: Фейл.** FAIL (нет модуля).

**Step 3: Реализация** `llm_client.py`. Ключевые точки:
- `provider={"only":["google-ai-studio"],"allow_fallbacks":false}` в каждый `create`
  (передаётся через `extra_body`, т.к. это не стандартное поле OpenAI SDK).
- `reasoning={"effort": effort, "exclude": True}` если `effort` задан (иначе не слать).
- `messages`: текст + картинки как `image_url`/`data:base64` (см. spike п.6).
- `response_format={"type":"json_object"}` если `response_json`.
- `max_tokens` — щедрый дефолт (напр. 4096) т.к. reasoning ест бюджет.
- usage: `resp.usage.prompt_tokens` / `.completion_tokens` (защитно, getattr).
- на 429: достать тело → `rate_limit.parse_cooldown_ttl(raw, seconds_to_midnight=
  cooldown.seconds_to_midnight())` → `mark_rate_limited(model, ttl)` → continue.
- прочие ошибки — raise. После `retries` попыток — `RuntimeError(f"...{last_error}")`
  (сохранить подстроку `429`/`RESOURCE_EXHAUSTED` в тексте, чтобы `error_classifier`
  распознал — см. Задачу 7).

**Step 4: Пасс.** PASS.

**Step 5: Commit** `feat(llm): LlmClient на openai SDK (форс BYOK, cooldown, картинки)`

**Step 6: ОРКЕСТРАТОР — ревью** (крупная задача): форс-BYOK в каждом запросе, разбор
тела 429 из реального типа ошибки openai, сохранность сигнала лимита в тексте.

---

## Задача 5: model_limits — удалить

**Files:**
- Delete: `lecturelog/infrastructure/llm/model_limits.py`
- Delete: `lecturelog/infrastructure/llm/key_pool.py` (заменён на ModelCooldown)
- Delete: `tests/unit/test_key_pool.py`

**Контекст:** `model_limits` использовался только `key_pool.py`. `key_pool` — только
`gemini_client`/`lifespan`. К этой задаче `lifespan` ещё на старом коде — поэтому
удаление здесь ПРЕЖДЕВРЕМЕННО. **Перенести эту задачу ПОСЛЕ Задачи 6** (когда lifespan и
structurizer уже на новом клиенте). Оставлено здесь для полноты; исполнять шестой-седьмой.

**Step 1:** убедиться `grep -rn "key_pool\|model_limits\|KeyPool\|limits_for" lecturelog/`
не даёт ссылок вне удаляемых файлов.
**Step 2:** удалить файлы. **Step 3:** `pytest tests/unit -v` зелёный.
**Step 4: Commit** `refactor(llm): удалить KeyPool/model_limits (заменены ModelCooldown)`

---

## Задача 6: lifespan + structurizer на LlmClient

**Files:**
- Modify: `lecturelog/api/lifespan.py:45-50, 53-61, 104-107`
- Modify: `lecturelog/infrastructure/structurize/gemini_structurizer.py` (импорт+тип
  `GeminiClient`→`LlmClient`, 5 вызовов `.call`)
- Test: `tests/unit/test_gemini_structurizer.py` (адаптировать фейк-клиент)

**Step 1:** В `lifespan.py` заменить блок создания клиента:
```python
from openai import AsyncOpenAI
from lecturelog.infrastructure.llm.llm_client import LlmClient
from lecturelog.infrastructure.llm.model_cooldown import ModelCooldown

openai_client = AsyncOpenAI(base_url=cfg.llm.base_url, api_key=cfg.llm.openrouter_key)
cooldown = ModelCooldown()
llm = LlmClient(openai_client, cooldown)
```
Убрать `genai.Client`-список, `pool`. `structurizer = GeminiStructurizer(gemini_client=llm,
split_models=cfg.llm.split_models, ...)` + прокинуть `effort_*`. Убрать из `app.state`:
`video_slides_models`, `concurrency_video` (но `app.state.gemini`→`app.state.llm`
оставить — его читает routes для video-провайдера; видео уйдёт в фазе 3, здесь только
переименование ссылки, не удаление функциональности).

> ⚠️ `routes.py:196` (`gemini_client=gemini`) и video-провайдер ещё существуют. Здесь
> задача — не сломать импорт/сборку. Если `VideoSlideProvider` требует старый
> `GeminiClient`-контракт — оставить `app.state.llm` совместимым по методу `.generate`
> ИЛИ временно оставить видео-ветку неактивной (см. Задача 8). Решение принять по факту
> сигнатур; НЕ реализовывать видео-логику заново.

**Step 2:** `structurizer` — заменить тип и убедиться, что `.call(prompt, models,
images=...)` совпадает по сигнатуре с новым `LlmClient`. Прокинуть `effort` per-stage
(split/subsplit/render) в соответствующие вызовы.

**Step 3:** адаптировать `test_gemini_structurizer.py` — фейк-клиент под новый контракт.

**Step 4:** `pytest tests/unit -v` зелёный.

**Step 5: Commit** `feat(llm): перевести lifespan и structurizer на LlmClient/OpenRouter`

**Step 6: ОРКЕСТРАТОР — ревью.**

---

## Задача 7: error_classifier — проверить сигнал лимита

**Files:**
- Modify (при необходимости): `lecturelog/application/error_classifier.py:9`
- Test: `tests/unit/test_error_classifier.py` (если есть — дополнить; иначе создать)

**Контекст:** классификатор ловит лимит по подстрокам `("429","RESOURCE_EXHAUSTED",
"503","UNAVAILABLE")`. Новый `LlmClient` должен оборачивать финальную ошибку так, чтобы
текст содержал `429` или `RESOURCE_EXHAUSTED` (в Задаче 4 это учтено).

**Step 1: Тест:** RuntimeError с текстом от нового клиента (`"...429..."`) →
`classify_error` возвращает `ErrorCode.RATE_LIMIT`.
**Step 2:** прогнать — если зелёный без правок кода, значит сигнал сохранён (правок не
нужно, только тест-регресс). Комментарий в classifier обновить (ссылка на `LlmClient`,
не `gemini_client`).
**Step 3: Commit** `test(errors): регресс rate_limit-классификации под LlmClient`

---

## Задача 8: Проверка сборки, финальный прогон

**Files:** —

**Step 1:** `pytest -v` — весь набор зелёный (кроме явно удалённых тестов).
**Step 2:** `ruff check lecturelog tests` — чисто.
**Step 3:** приложение импортируется: `python -c "import lecturelog.api.lifespan"`
(video_provider ещё на `google.genai` — это ок, зависимость не удалена до фазы 3).
**Step 4:** зафиксировать в дизайн-документе: фаза 2 выполнена, открытые хвосты для
фазы 3 (video_provider, удаление google-genai, OpenAPI).
**Step 5: Commit** `chore: финализация фазы 2 (транспорт OpenRouter BYOK)`

---

## Хвосты для фазы 3 (НЕ делать сейчас, зафиксировать)

- Удалить `video_provider.py`, ветку `video_slide_provider_factory` в `routes.py`,
  `get_video_slides_config`.
- Удалить `google-genai` из `pyproject.toml`.
- Убрать `video_slides` из OpenAPI-описания эндпоинта (тихая деградация — см. дизайн).
- `.env.example` и деплой: `OPENROUTER_API_KEY` (обязательное, fail-fast), `LLM_*` вместо
  `GEMINI_*`, убрать `GEMINI_MODELS_VIDEO_SLIDES`/`GEMINI_CONCURRENCY_VIDEO`.
- README/документация — отдельным субагентом (по глобальному правилу проекта).

## Открытые вопросы для исполнителя (решить по факту кода)

1. Точный тип исключения `openai` SDK на 429 и где в нём тело (`.body` / `.response.json()`)
   — свериться с установленной версией; парсить `error.metadata.raw` оттуда.
2. `extra_body` vs прямые kwargs для `provider`/`reasoning` в `openai` SDK — проверить,
   что нестандартные поля доходят до OpenRouter (в spike слались как топ-левел JSON).
3. Совместимость `app.state.llm` со старым video-провайдером до фазы 3 (Задача 6/8).
