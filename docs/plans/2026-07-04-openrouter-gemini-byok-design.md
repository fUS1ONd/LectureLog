# Переход Gemini-вызовов на OpenRouter BYOK — дизайн

Статус: **фаза 2 (транспорт) ВЫПОЛНЕНА (2026-07-04)** — spike пройден, код-переезд на
OpenRouter BYOK завершён и влит. Проработан через grill-me + brainstorming, ключевые
развилки проверены живыми запросами к OpenRouter.
Дата: 2026-07-04.

> **Итог фазы 2 (транспорт).** Сделано: `LlmClient` на `openai` SDK (форс BYOK
> `provider.only=google-ai-studio, allow_fallbacks=false`, картинки inline base64,
> `response_format` json, per-stage `reasoning.effort`+`exclude`), `ModelCooldown` с
> точным TTL из тела 429 (парсер `rate_limit.py`: RPM→`retryDelay`, RPD→полночь Pacific,
> иначе фикс-60с, DST-корректно), `LlmConfig` (env `OPENROUTER_API_KEY`/`LLM_*`, per-stage
> `effort`), `lifespan`/`structurizer` переведены. Удалён мёртвый код: `KeyPool`,
> `model_limits`, `GeminiClient`, `VideoSlideProvider` (+ их тесты). Извлечение слайдов из
> видео **тихо отключено** (видео обрабатывается как аудио-лекция; slides_origin всегда
> `document`); enum `PipelineStage.VIDEO_SLIDES` и usage-поле `video_slides` оставлены в
> domain-моделях ради истории, но не заполняются. Ворота зелёные: `ruff check`/`format`,
> `pytest -q` (330 passed).
>
> **Хвосты для фазы 3 (НЕ сделано):** удалить `google-genai` из `pyproject.toml`;
> вычистить ветку `video_slide_provider_factory`/`get_video_slides_config` полностью
> (в фазе 2 отключено, но плумбинг зачищен частично); убрать `video_slides` из
> OpenAPI-описания эндпоинта; обновить `.env.example`/деплой (`OPENROUTER_API_KEY`
> обязательное fail-fast, `LLM_*` вместо `GEMINI_*`, убрать `GEMINI_MODELS_VIDEO_SLIDES`/
> `GEMINI_CONCURRENCY_VIDEO`); обновить README/документацию.

> **Результаты spike вынесены в раздел [«Результаты spike»](#результаты-spike-выполнен-2026-07-04)
> ниже.** Он перекрывает часть старых ⚠️/🔬-пометок — при расхождении верить разделу spike.

## Легенда статуса проработки

Каждый раздел помечен, чтобы следующая сессия (планирование/код) не приняла
недоработанное за решённое:

- ✅ **РЕШЕНО** — развилка закрыта, решение принято, можно кодить по описанию.
- 🔬 **ТРЕБУЕТ РЕСЁРЧА** — блокирует реализацию соответствующего куска, нужен spike ДО кода.
- ⚠️ **ПРОВЕРИТЬ ПРИ РЕАЛИЗАЦИИ** — решение принято, но детали формата/API надо
  подтвердить эмпирически по факту (не блокирует дизайн, влияет на точность кода).

### Карта статусов по разделам

| Раздел | Статус |
|---|---|
| Мотивация, стоимость | ✅ (подтверждено докой + ресёрчем) |
| Транспорт на `openai` SDK | ✅ решение / ⚠️ детали формата запросов |
| Удаление видео-пути | ✅ РЕШЕНО полностью |
| Контракт ядра (тихая деградация) | ✅ РЕШЕНО |
| `ModelCooldown` (архитектура, process-wide) | ✅ РЕШЕНО (роль пересмотрена — см. spike) |
| TTL блокировки в `ModelCooldown` | ✅ **РЕШЕНО spike'ом**: фикс-60с (фолбэк OR гасит большинство 429) |
| Провайдерный фолбэк OpenRouter (BYOK→платный пул) | ✅ **РЕШЕНО spike'ом**: оставляем включённым (всегда free-tier) |
| Reasoning у Gemini через OpenRouter | ✅ **РЕШЕНО spike'ом**: `effort` (per-stage) + `exclude:true` |
| Конфиг, именование, зависимости | ✅ РЕШЕНО (+ per-stage `effort`, префикс `google/`) |
| Fallback / GroqKeyPool | ✅ РЕШЕНО (не делаем / не трогаем) |
| Картинки base64 через OpenRouter | ✅ **подтверждено живьём** (формат `image_url`+`data:`); лимит размера — не проверяли |
| Structured output через OpenRouter для Gemini | ✅ **подтверждено живьём** (`response_format:json_object`) |

## Результаты spike (выполнен 2026-07-04)

Заведён тестовый OpenRouter-аккаунт (free-tier) с BYOK-ключом Google AI Studio,
прогнаны живые `curl`-запросы к `POST /api/v1/chat/completions`. Ниже — что реально
подтвердилось и что изменилось в дизайне по итогам.

### Главная находка: двухслойный фолбэк OpenRouter меняет роль `ModelCooldown`

Наблюдаемый механизм на живых запросах:

1. Запрос идёт через **твой BYOK-ключ** (AI Studio): в ответе `usage.is_byok=true`,
   `usage.cost=0`, `provider="Google AI Studio"`.
2. Бесплатный ключ AI Studio упирается в низкий RPM → Google отдаёт 429.
3. **OpenRouter по умолчанию молча фолбэкает** на свой платный пул той же модели
   (Google **Vertex AI**): `usage.is_byok=false`, `usage.cost>0`, `provider="Google"`.
   При аккаунте `is_free_tier=true` эти запросы покрываются бесплатной квотой
   OpenRouter (~20 req/min, 50–1000 req/day) → реально $0.

Подтверждено через `GET /api/v1/key`: `is_free_tier=true`, `include_byok_in_limit=false`
(BYOK не ест лимит ключа — обещанный «1M free BYOK/мес»), `limit=null`.

**Продуктовое решение (ФИНАЛ, пересмотрено):** **фолбэк ЗАПРЕЩАЕМ.** Первоначально
решили оставить (расчёт «всегда free-tier → бесплатно»), но эмпирика опровергла: даже на
free-tier платный Vertex-фолбэк реально **списывает деньги** — на живом тесте баланс ушёл
в **−$0.01** (микро-цена НЕ округляется в ноль, копится в минус). «Бесплатно» не
выполняется → фолбэк недопустим.

**Как запрещаем (проверено живьём):** в каждый запрос добавлять
```json
"provider": {"only": ["google-ai-studio"], "allow_fallbacks": false}
```
Тогда запрос идёт **только** через твой BYOK-ключ; при исчерпании — **честный 429
наружу** (не тихое списание), $0 гарантирован. Проверено: с этим параметром на
исчерпанном ключе стабильно `HTTP 429`, `is_byok:true`, платный путь закрыт.

**Страховка (уровень 2, желательно):** в UI OpenRouter (Settings → Integrations, ключ
Google AI Studio) включить тумблер **«Always use this key»** — запрещает фолбэк на
кредиты OpenRouter глобально, даже если в коде забыли параметр `provider`.

**Следствия для дизайна:**

- **429 наружу теперь приходит стабильно** (фолбэк закрыт) → `ModelCooldown` снова
  **горячий путь**, работает как задумано: 429 на модели А → cooldown А → `acquire`
  отдаёт Б из списка стадии.
- **TTL: берём точное значение из тела ответа** (см. раздел «429-passthrough» ниже) —
  Google пробрасывает `RetryInfo.retryDelay` и `quotaId` в `metadata.raw`. Фикс-60с
  остаётся только **запасным** путём, если тело не распарсилось.
- **Потолок пиков — RPD/RPM самого BYOK-ключа Google** (в тесте RPD free-tier = 20/день
  на модель). Это жёстче, чем казалось: без фолбэка при исчерпании дневной квоты модели
  `ModelCooldown` должен увести на следующую модель списка. Сверить `concurrency_*` и
  длину списков моделей стадий с реальными квотами ключа.

### Reasoning у Gemini через OpenRouter (подтверждено живьём)

- **По умолчанию Gemini-flash через OpenRouter НЕ рассуждает**: без параметра `reasoning`
  ответ верный, но `reasoning_tokens=0`. То есть «включить мышление» надо **явно**.
- `reasoning:{exclude:true}` **сам по себе НЕ активирует** reasoning (прячет то, чего нет).
- Чтобы reasoning реально работал и при этом блок мыслей не приходил в ответе, нужна
  **пара**: `reasoning:{"effort":"<low|medium|high>", "exclude":true}`.
  Проверено: `effort:high+exclude` → `reasoning_tokens=174`, `content` — чистый результат
  без блока мыслей.
- **Решение:** `effort` — **конфигурируемый по стадиям** (split/subsplit/render), значения
  подберём при кодинге; `exclude:true` всегда. Выносится в конфиг как per-stage параметр
  рядом с моделями и concurrency.
- **Последствие для `max_tokens`:** reasoning ест бюджет ответа. При `effort>0` закладывать
  `max_tokens` с запасом (в spike `max_tokens:50` при активном reasoning обрезал ответ,
  `finish_reason:length`). Использовать щедрый лимит (в тестах 2000 хватало).

### Формат запросов/ответов (подтверждено живьём)

- **Usage**: `usage.prompt_tokens` / `.completion_tokens` / `.total_tokens` — как в дизайне.
  Дополнительно: `usage.is_byok`, `usage.cost`, `usage.cost_details`,
  `usage.completion_tokens_details.reasoning_tokens`.
- **JSON-mode**: `response_format:{"type":"json_object"}` → чистый валидный JSON. Работает.
- **Картинки**: `content:[{"type":"text",...},{"type":"image_url","image_url":{"url":
  "data:image/png;base64,..."}}]` → корректное распознавание. Прямой аналог `Part.from_bytes`.
  Битый base64 отдаёт `400 INVALID_ARGUMENT "Failed to decode image data"` от провайдера
  (в `error.metadata.raw`) — валидировать/логировать. Практический предел размера не мерили.
- **Заголовки атрибуции**: `HTTP-Referer` / `X-Title` принимаются без проблем.
- **Модели — сопоставление `.env` ↔ OpenRouter (сверено с `/api/v1/models`, 2026-07-04):**
  Все три наши модели **доступны** на OpenRouter, менять состав не надо — только добавить
  префикс `google/`.

  | Модель в `.env` | На OpenRouter | Слаг для конфига |
  |---|---|---|
  | `gemini-3.5-flash` | ✅ | `google/gemini-3.5-flash` |
  | `gemini-3-flash-preview` | ✅ (проверено живьём, `is_byok:true`) | `google/gemini-3-flash-preview` |
  | `gemini-3.1-flash-lite` | ✅ | `google/gemini-3.1-flash-lite` |

  - `*-image`-модели (`gemini-3.1-flash-image`, `gemini-2.5-flash-image` и т.п.) — **не
    трогаем**: это генераторы картинок. Наши стадии шлют картинки только **на вход**,
    распознаёт обычная (не-image) flash.
  - Итоговые списки (с префиксом `google/`, состав как в `.env`, без изменений):
    `SPLIT=[gemini-3.5-flash, gemini-3-flash-preview]`,
    `SUBSPLIT=[gemini-3.5-flash, gemini-3-flash-preview]`,
    `RENDER=[gemini-3.1-flash-lite, gemini-3.5-flash, gemini-3-flash-preview]`.
    `VIDEO_SLIDES` — удаляется (видео-путь убираем по дизайну).

### 429-passthrough наружу — РАЗОБРАН ЖИВЬЁМ (обе формы: RPM и RPD)

С `provider.only + allow_fallbacks:false` живой 429 стабильно ловится наружу. Пойманы
**оба** режима за один прогон:

| Модель (из нашего конфига) | `quotaId` | Тип | `retryDelay` |
|---|---|---|---|
| `gemini-3.5-flash` | `GenerateRequestsPerDayPerProjectPerModel-FreeTier` | **RPD** (суточный) | 22s / 58s (прыгает) |
| `gemini-3.1-flash-lite` | `GenerateRequestsPerMinutePerProjectPerModel-FreeTier` | **RPM** (минутный) | ~38s |

> Детекция по подстроке `PerMinute` / `PerDay` устойчива к разным метрикам квоты: Google
> отдаёт и `GenerateRequests…PerMinute…` (запросы/мин), и `…InputTokens…PerMinute…`
> (токены/мин) — обе несут `PerMinute`. Правило `ModelCooldown` завязано на `PerMinute`/
> `PerDay`, а не на конкретную метрику, поэтому покрывает оба.

**HTTP-заголовки:** `Retry-After` в ответе **НЕТ**. Весь сигнал — в **теле**.

**Тело 429** (OpenRouter-обёртка):
```
{"error": {"code": 429, "message": "Provider returned error",
  "metadata": {"raw": "<СТРОКА-JSON от Google>", "provider_name": "Google AI Studio", "is_byok": true}}}
```
`metadata.raw` — это **JSON-строка** (не объект!) полной ошибки Google
(`status:"RESOURCE_EXHAUSTED"`), внутри `details[]` с:
- `QuotaFailure.violations[0].quotaId` — **различает RPM vs RPD** (подстрока `PerDay` /
  `PerMinute`), плюс `quotaValue`, `quotaDimensions.model`.
- `RetryInfo.retryDelay` (напр. `"58s"`) — но **у RPD он врёт**: прыгает 22↔58s, реальный
  сброс суточной квоты — в полночь Pacific, а не через минуту. Доверять `retryDelay`
  можно **только для RPM**.

**Логика TTL в `ModelCooldown` (ФИНАЛ, вариант «б» — из тела):**
1. Распарсить `error.metadata.raw` → `json.loads` (best-effort, это формат Google).
2. Достать `quotaId` из `QuotaFailure` и `retryDelay` из `RetryInfo`.
3. Ветвление:
   - `quotaId` содержит `PerMinute` (**RPM**) → `ttl = retryDelay` (реалистичен, ~20-30с).
   - `quotaId` содержит `PerDay` (**RPD**) → `ttl = длинный` (до конца суток Pacific ИЛИ
     крупная константа, напр. ≥1ч); `retryDelay` от Google **игнорировать**. Смысл:
     дневная квота на модель исчерпана → не долбить её, `acquire` уводит на другую модель
     списка стадии до сброса.
   - Не распарсилось / нет полей → **фикс-60с** (запасной путь).

Это делает `ModelCooldown` точным и для RPM, и для RPD — исходная развилка spike закрыта
полностью, причём лучше, чем «фикс-60с для всего».

> ⚠️ **Хрупкость:** `metadata.raw` — недокументированный passthrough формата Google.
> Парсинг обязан быть защитным: любой сбой → фикс-60с, не падать. При смене формата
> OpenRouter/Google деградируем к запасному TTL, а не ломаемся.

## Мотивация

Google банит запросы к Gemini API по гео-метке IP: VPS с RU-меткой перестаёт
работать (`FAILED_PRECONDITION: User location is not supported`). Бан применяется
по IP источника на уровне API-эндпоинта в целом (домен
`generativelanguage.googleapis.com`), а не только на inference.

OpenRouter делает исходящий вызов к Google своей инфраструктурой, используя наш
зашифрованный ключ (BYOK). Фактический IP запроса к Google = IP OpenRouter, не наш
VPS — это и снимает гео-бан. Дополнительно OpenRouter поддерживает несколько
BYOK-ключей одного провайдера и делает between-key fallback при 429 — туда
складываются все бесплатные Gemini-ключи.

## Стоимость

- Первый 1 млн BYOK-запросов/мес — бесплатно (комиссия 0%), сброс в начале месяца UTC.
- После лимита — 5% от номинальной стоимости запроса по прайсу OpenRouter.
- Наш объём: ~54 000 запросов/мес — на два порядка меньше порога. Остаёмся бесплатными.

Цифры подтверждены докой (2026-07): OpenRouter BYOK auth + анонс «1M free BYOK/мес».

## Итоговые решения (сводка развилок)

| Область | Решение |
|---|---|
| Транспорт | Переписать на `openai` SDK (`AsyncOpenAI`, base_url OpenRouter). OpenAI-совместимый формат. |
| Текст (split/subsplit/render) | → OpenRouter. Тривиальный перенос. |
| Inline-картинки | → OpenRouter, `image_url` + `data:image/png;base64,...`. Прямой аналог `Part.from_bytes`. |
| Видео (video_slides) | **Удаляется целиком** (deprecated). Причина ниже. |
| `google.genai` | **Удаляется из зависимостей** (единственный потребитель Files API — видео). |
| `KeyPool` (проактивный RPM/RPD) | **Удаляется**. Ключи скрыты внутри OpenRouter. |
| Заменяется на | `ModelCooldown` — process-wide cooldown по модели при 429. |
| `model_limits.py` | **Удаляется** (относился к прямому free tier, вводил бы в заблуждение). |
| Именование | `GeminiConfig`→`LlmConfig` (`cfg.llm`), `GeminiClient`→`LlmClient`. Нейтрально к провайдеру. |
| Fallback на прямой Gemini | **Нет**. Прямой Gemini забанен по гео — аварийным путём быть не может. |
| `GroqKeyPool` (транскрипция) | **Не трогаем** — это про Groq, не про Gemini, вне скоупа. |

## Почему видео-путь удаляется, а не переносится

Ресерч (2026-07) подтвердил:

1. **OpenRouter не проксирует Gemini Files API** и не хранит файлы. Текущий паттерн
   `client.files.upload → file_uri → Part.from_uri` через OpenRouter невозможен.
2. **Прямой `files.upload` с VPS тоже под гео-баном** (тот же домен, тот же IP) —
   оставить видео «на старом KeyPool» = гео-проблема для видео не решается.
3. Единственный способ видео через OpenRouter — base64-инлайн `video_url` **только на
   Vertex-версию** Gemini (AI Studio принимает по видео лишь YouTube-ссылки). Тяжёлые
   запросы, неизвестный практический предел размера чанка.

Решение продукта: **video_slides в текущем виде показывает слабые результаты и дорог.**
Механику будут переделывать отдельно — препроцессингом извлекать стоп-кадры и слать
Gemini на анализ уже **картинки** (вердикт «слайд / не слайд»). А картинки OpenRouter
принимает inline без Files API. Поэтому в рамках этого переезда видео-путь просто
удаляется, и `google.genai` уходит целиком.

### Что именно удаляется по видео

- `lecturelog/infrastructure/slides/video_provider.py` (`VideoSlideProvider`).
- Ветка `video_slide_provider_factory` в `routes.py`, зависимость `get_video_slides_config`.
- `GEMINI_MODELS_VIDEO_SLIDES` из конфига, `video_slides_models`/`concurrency_video`
  из `lifespan`/`app.state`.
- **Остаётся** (ради целостности истории задач в БД, но больше не заполняется):
  enum `PipelineStage.VIDEO_SLIDES`, usage-поле `video_slides`, полоса progress_plan.

### Контракт ядра при видео-запросе

Извлечение слайдов из видео было **неявным дефолтом** (видео без `no_slides` и без
приложенного документа → включалось), а не явным флагом. Явного параметра «дай слайды
из видео» в контракте нет → отбивать нечего.

**Тихая деградация**: видео-запрос обрабатывается как лекция по аудиодорожке
(транскрипция + структуризация как обычно), слайды из видео просто не извлекаются.
`slides` (документ-провайдер, `DocumentSlideProvider`) и `no_slides` работают как
раньше — они не про видео и не про Files API. OpenAPI-описание эндпоинта обновляется:
убрать упоминание извлечения слайдов из видео.

UX (предупреждение «видео-режим слайдов временно отключён») — ответственность
web/ui-слоя, не ядра. Ядро только фиксирует контракт.

## Транспорт: LlmClient на OpenAI SDK ✅ решение / ⚠️ детали формата

> ✅ Решение переписать на `openai` SDK — принято.
> ⚠️ Конкретные поля запроса (структура `content`-частей для картинок, точное имя поля
> usage, поведение `response_format` для Gemini через OpenRouter) — подтверждены докой,
> но **живьём не проверялись**. При реализации свериться с реальным ответом OpenRouter.

Заменяет `GeminiClient`. Один `AsyncOpenAI`-клиент (ключи внутри OpenRouter, список
клиентов больше не нужен):

```python
AsyncOpenAI(base_url=cfg.llm.base_url, api_key=cfg.llm.openrouter_key)
```

Изменения формата (OpenAI-совместимый):

- **Сообщения**: `messages=[{"role": "user", "content": [...]}]` вместо `contents`.
- **Картинки**: элемент `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`
  вместо `types.Part.from_bytes`. Текст слать перед картинками.
- **JSON-mode**: `response_format={"type": "json_object"}` (или `json_schema` при
  необходимости strict) вместо `GenerateContentConfig(response_mime_type=...)`.
- **Имена моделей**: с префиксом `google/` (`google/gemini-3.5-flash`) — хранятся уже
  с префиксом в конфиге, уходят в OpenRouter один-в-один.
- **Usage-токены**: `response.usage.prompt_tokens` / `.completion_tokens` вместо
  `usage_metadata.prompt_token_count` / `.candidates_token_count`. Читать защитно.
- **Ретраи/детекция rate-limit**: 429 от OpenRouter → cooldown модели (см. ниже).
  Эвристики `_is_rate_limit_error`/`_is_overload_error` адаптировать под ответы
  OpenRouter (см. spike).

Контракт `generate(models, prepare, ...)` упрощается: `prepare` больше не строит
`google.genai`-специфичные объекты, а возвращает OpenAI-`content`-части. Единственный
внешний потребитель кастомного `prepare` был `video_provider` — он удаляется, значит
остаётся только внутренний путь `call(prompt, images=...)`.

## ModelCooldown — замена KeyPool

`KeyPool` решал две задачи: (1) перебор моделей по приоритету, (2) round-robin ключей
+ RPM/RPD-троттлинг. Задача (2) исчезает (ключи внутри OpenRouter). Остаётся (1) плюс
реактивная блокировка модели при 429.

Схлопывается до крошечного process-wide объекта (~20 строк):

```python
class ModelCooldown:
    # blocked_until: dict[str, float]  — модель -> epoch снятия блокировки
    async def acquire(self, models: list[str]) -> str: ...       # первая не-остывающая
    async def mark_rate_limited(self, model: str, ttl: float): ...
```

- **Process-wide синглтон** в `app.state` (как сейчас `pool`), шарится между всеми
  задачами воркера. Ключевой выигрыш: две одновременные лекции делят знание «модель А
  остывает» — вторая не повторяет ошибок первой (параллельная обработка ускоряется).
- **Логика fallback по моделям сохраняется 1:1**: 429 на модели А (= у OpenRouter
  исчерпаны все BYOK-ключи под А) → cooldown А → `acquire` отдаёт Б из списка стадии.
- **Конкурентность**: при `concurrency_render=5` несколько вызовов могут поймать 429 на
  одной модели одновременно и звать `mark_rate_limited` — это идемпотентно (пишут одно
  и то же), плюс `asyncio.Lock` вокруг словаря для консистентного чтения `acquire`.
- **TTL блокировки** — см. spike ниже. Дизайн закладывает TTL как **одну подменяемую
  точку**: константа 60с ИЛИ значение из заголовка ответа.

## Spike: поведение OpenRouter при rate-limit ✅ ВЫПОЛНЕН (2026-07-04)

> ✅ **Spike выполнен, блок снят.** Итог — см. [«Результаты spike»](#результаты-spike-выполнен-2026-07-04)
> выше. Кратко: **берём фикс-60с**. Различить RPM vs RPD по ответу OpenRouter нельзя
> (сырые Google-заголовки наружу не пробрасываются), а провайдерный фолбэк OpenRouter
> гасит большинство 429 до того, как они дойдут до нашего `ModelCooldown` — поэтому
> тонкость TTL практически не влияет. Ниже — исходная постановка вопроса для истории.

Фиксированные 60с плохо различают два режима 429:
- **RPM** (минутный лимит всех ключей) — восстанавливается ~через минуту, 60с в самый раз.
- **RPD** (суточный лимит всех ключей) — восстанавливается только в полночь Pacific;
  60с бессмысленны: модель весь день ловит лишний round-trip каждую минуту.

Старый `KeyPool` считал RPD сам; теперь счётчиков нет — оба случая приходят одинаковым
429, и мы их не различаем без сигнала от OpenRouter.

**Подвопросы spike:**
- Какой HTTP-статус OpenRouter отдаёт при исчерпании BYOK-ключей — всегда 429 или различает?
- Есть ли `Retry-After` / `X-RateLimit-Reset` / поле в JSON-body с временем восстановления?
- **Различается ли ответ для RPM vs RPD** — это ядро вопроса.
- Источники: дока OpenRouter (rate-limits, BYOK), их OpenAPI-спека, при возможности —
  живой тестовый 429.

**Выход spike:**
- (а) фикс-60с, если сигнала о времени восстановления нет — переживаем RPD «плохо, но
  не смертельно» (лишний retry раз в минуту, при нашем объёме RPD-исчерпание маловероятно);
- (б) TTL из заголовка/поля, если сигнал есть — точно, и RPM и RPD.

Эскалация cooldown (60с→5м→30м) — не делаем (YAGNI).

## Конфиг

`GeminiConfig` → `LlmConfig` (`cfg.llm`). Новые/изменённые env:

- `OPENROUTER_API_KEY` — **обязательное** (заменяет `GEMINI_API_KEYS`).
- `OPENROUTER_BASE_URL` — дефолт `https://openrouter.ai/api/v1` (для тестов/мока и
  гипотетического LiteLLM-хоста без правки кода).
- `LLM_MODELS_SPLIT` / `_SUBSPLIT` / `_RENDER` — имена **с префиксом** `google/`.
  `LLM_MODELS_VIDEO_SLIDES` — удаляется.
- `GEMINI_CONCURRENCY_*` → `LLM_CONCURRENCY_*` (concurrency стадий, не про ключи) —
  `_VIDEO` удаляется.

**Миграция окружения**: обновить `.env`-примеры и деплой-конфиг. Новое обязательное
`OPENROUTER_API_KEY` — при его отсутствии на проде приложение не поднимется (fail-fast
в `AppConfig.model_post_init`). Учесть при выкатке.

## Зависимости

- Удалить `google-genai` из `pyproject.toml`.
- Добавить `openai` (AsyncOpenAI).

## Затрагиваемые файлы (ориентир)

- `lecturelog/infrastructure/llm/gemini_client.py` → переписать в `llm_client.py` (`LlmClient`).
- `lecturelog/infrastructure/llm/key_pool.py` → заменить на `model_cooldown.py`.
- `lecturelog/infrastructure/llm/model_limits.py` → удалить.
- `lecturelog/infrastructure/slides/video_provider.py` → удалить.
- `lecturelog/config/settings.py` → `GeminiConfig`→`LlmConfig`, env-алиасы.
- `lecturelog/api/lifespan.py` → `AsyncOpenAI` вместо `genai.Client`-списка; убрать
  video_slides из `app.state`.
- `lecturelog/api/routes.py`, `dependencies.py` → убрать video_slides_config/factory;
  переименовать `get_gemini`→`get_llm`; обновить OpenAPI-описание эндпоинта.
- `structurizer` / `pipeline_service` / `usage_accumulator` → переименования типов,
  video_slides usage перестаёт заполняться.
- Тесты: адаптировать под новый транспорт и `ModelCooldown`; удалить тесты video/KeyPool RPM/RPD.

## Порядок реализации (фазы)

1. ~~**Spike** поведения OpenRouter при rate-limit~~ ✅ **ВЫПОЛНЕН** (см. «Результаты spike»).
   Итог: фикс-60с TTL; фолбэк OR оставляем включённым; reasoning `effort`+`exclude`.
2. Транспорт: `LlmClient` на `openai` SDK (текст + картинки), `ModelCooldown`, конфиг,
   зависимости. Тесты. **← текущая фаза.**
3. Удаление видео-пути и `google.genai`. Обновление контракта/OpenAPI. Тесты.
4. Обновить README/документацию и `.env`-примеры (отдельным проходом).

## Границы дизайна: что НЕ проработано / вынесено за скоуп

Явный список, чтобы сессия планирования не приняла отсутствие за упущение:

**🔬 Требует ресёрча перед кодом (блокирует свой кусок):**
- TTL-поведение OpenRouter при 429 (RPM vs RPD, наличие `Retry-After`) — см. Spike.
  Блокирует финальный выбор TTL в `ModelCooldown`.

**⚠️ Проверить эмпирически при реализации (не блокирует дизайн):**
- Практический предел размера base64-картинки в одном запросе OpenRouter (в доке лимита нет).
- Точный формат usage-полей и `response_format` для `google/gemini-*` через OpenRouter —
  свериться с живым ответом.
- Совпадают ли имена моделей `google/gemini-3.5-flash` и т.п. с реально доступными на
  OpenRouter (список моделей может отличаться от того, что было на прямом Gemini).

**🚫 Сознательно вне скоупа этой фичи (отдельные будущие истории):**
- Новая механика слайдов из видео (препроцессинг стоп-кадров + gemini-вердикт по картинкам).
  Здесь только удаляется старый video-путь; новая механика — отдельный дизайн.
- Fallback-прокси (LiteLLM в разрешённом регионе) на случай ненадёжности OpenRouter.
  Не делаем сейчас (YAGNI); прямой Gemini как fallback невозможен (гео-бан).
- Эскалация cooldown (60с→5м→30м) — не делаем.
- UX-предупреждение о выключенном видео-режиме — ответственность web/ui-слоя, не ядра.

**❓ Не обсуждалось детально (всплывёт при кодовом планировании):**
- Стратегия миграции тестов: какие тесты KeyPool/video удаляются, какие переписываются,
  нужен ли мок OpenRouter-эндпоинта (через `OPENROUTER_BASE_URL` на локальный сервер).
- Нужен ли заголовок `HTTP-Referer`/`X-Title` в запросах к OpenRouter (их рекомендация
  для атрибуции трафика) — мелочь, но решить при реализации транспорта.
