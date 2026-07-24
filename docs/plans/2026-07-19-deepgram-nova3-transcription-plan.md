# Deepgram Nova-3 как второй STT-провайдер — план реализации

**Статус:** готов к реализации после ревью плана
**Дата:** 2026-07-19
**Ветка:** `plan/deepgram-nova3-transcription`
**База:** `origin/dev` @ `77082c331c1cf00cdb0d706fa1b099a982a5ab4a`

## 1. Цель

Добавить Deepgram Nova-3 как второй провайдер транскрибации аудио в текст, не ломая
существующий Groq Whisper-путь и внешний контракт LectureLog:

- вход стадии — локальный аудиофайл;
- выход стадии — `transcript.srt` с корректными абсолютными таймкодами;
- downstream-стадии (`structurize`, `video_slides`, нарезка медиа) не знают о провайдере;
- `GET /tasks/{id}/transcript?srt|txt` сохраняет текущий wire-контракт;
- `usage.transcribe` продолжает содержать `audio_seconds`, `provider`, `model`, `raw`;
- провайдер переключается конфигурацией и откатывается без миграции БД или API.

Первая реализация должна быть пригодна для Pay-As-You-Go/free-credit тестов, но не должна
автоматически включать платные Deepgram add-on'ы.

## 2. Не входит в первую реализацию

- автоматический fallback Deepgram -> Groq внутри одной задачи;
- одновременная транскрибация одной задачи двумя провайдерами в production;
- streaming/WebSocket STT;
- speaker diarization, redaction, summarization и Audio Intelligence;
- per-task выбор провайдера или языка через публичный API;
- сохранение полного сырого ответа Deepgram в БД или S3;
- динамические keyterms из содержимого слайдов/названия лекции;
- изменение публичного OpenAPI-контракта.

Автоматический fallback намеренно откладывается: он может незаметно удвоить расходы,
породить разные транскрипты при одинаковом входе и затруднить диагностику качества.
На первом этапе провайдер выбирается один раз при старте процесса.

## 3. Как транскрибация устроена сейчас

### 3.1. Контракт и оркестрация

- `lecturelog/domain/ports.py::Transcriber` принимает `audio_path`, `output_dir`,
  `on_progress`, `on_usage` и возвращает путь к SRT.
- `lecturelog/application/pipeline_service.py` передаёт адаптеру исходное аудио либо
  MP3, извлечённый из видео, затем сразу персистит `usage`.
- Один и тот же SRT используется для:
  - тематического разбиения и рендера конспекта;
  - привязки кадров видео по времени;
  - `GET /tasks/{id}/transcript` в форматах SRT/TXT.
- `lecturelog/infrastructure/srt.py` ожидает стандартные блоки с таймкодами
  `HH:MM:SS,mmm --> HH:MM:SS,mmm`.

Следствие: Deepgram-адаптер нельзя ограничить выдачей plain text или vendor JSON — он
должен сформировать валидный, монотонный SRT в общей временной шкале исходного аудио.

### 3.2. Текущий Groq-адаптер

`lecturelog/infrastructure/transcribe/groq_transcriber.py`:

1. best-effort получает длительность через `ffprobe`;
2. перекодирует вход в MP3 128 kbps и режет на 20-минутные чанки;
3. последовательно отправляет чанки в Groq `whisper-large-v3`;
4. запрашивает word timestamps;
5. добавляет к словам offset `index * 1200`;
6. собирает SRT механическими группами по семь слов;
7. ретраит timeout/429/503/524 и умеет переключать Groq-ключи.

### 3.3. Текущие жёсткие связи с Groq/Whisper

- `lecturelog/api/lifespan.py` напрямую создаёт только `GroqTranscriber`.
- `lecturelog/config/settings.py` всегда требует `GROQ_API_KEYS`, даже если будет выбран
  другой провайдер.
- `.env.example`, `deploy/env.core.example` и README описывают только Groq STT.
- `UsageAccumulator.record_transcribe()` имеет неявный default `provider="groq"`.
- `prompts/section_v1.md` утверждает, что транскрипт всегда получен из Whisper.
- комментарии и имена части error-classifier тестов привязаны к Groq, хотя сам
  `httpx.HTTPStatusError` уже провайдер-нейтрален.

## 4. Что подтверждено документацией Deepgram

Основной API для готовых файлов — `POST https://api.deepgram.com/v1/listen` с
`Authorization: Token <key>` и бинарным телом локального файла.

Для LectureLog нужны параметры:

| Параметр | Решение | Причина |
|---|---|---|
| `model` | `nova-3` | Явно фиксируем нужное семейство; без параметра API использует `base`. |
| `language` | конфиг, стартовое значение `ru` | Deepgram по умолчанию использует `en`; русский Nova-3 поддерживает явно. |
| `smart_format` | `true` | Даёт пунктуацию/регистр/paragraphs и включён в цену. |
| `utterances` | `true` | Даёт пауза-ориентированные сегменты и word timestamps для построения SRT. |
| `utt_split` | конфиг, default `0.8` | Документированный default; позволяет настроить слишком мелкие/крупные блоки без релиза кода. |
| `mip_opt_out` | всегда `true` | Исключает аудио лекций из Model Improvement Program; с 2026-03-05 для Pay-As-You-Go это не меняет публичную цену. |

Существенные ограничения и свойства:

- поддерживаются MP3, MP4, AAC, WAV, FLAC, M4A, Ogg, Opus, WebM и многие другие
  контейнеры/кодеки;
- максимальный размер файла — 2 GB;
- синхронная обработка Nova имеет серверный processing-time limit 10 минут; после него
  API возвращает 504;
- актуальный лимит Pay-As-You-Go для pre-recorded Nova-3 — до 50 одновременных запросов
  на проект; внутренний `MAX_CONCURRENT_TASKS=2` существенно ниже;
- при 429 Deepgram рекомендует exponential backoff;
- API возвращает `metadata.duration`, сведения о фактической модели, channel alternatives,
  `words`, а с `utterances=true` — ещё и смысловые/пауза-ориентированные сегменты;
- Deepgram не хранит транскрипт для последующего получения: успешный JSON-ответ нужно
  преобразовать и сохранить сразу;
- без `mip_opt_out=true` запрос по умолчанию участвует в Model Improvement Program; для
  LectureLog privacy-safe default должен быть opt-out, при котором данные удерживаются только
  на время, необходимое для обработки запроса;
- `language=ru` ограничивает распознавание выбранным языком; для лекций с настоящим
  переключением между русским и английским доступен `language=multi`.

### Официальные источники

- [Pre-recorded audio: начало работы](https://developers.deepgram.com/docs/pre-recorded-audio)
- [API `POST /v1/listen`](https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded)
- [Nova-3: модели и языки](https://developers.deepgram.com/docs/models-languages-overview)
- [Language и ограничение выбранным языком](https://developers.deepgram.com/docs/language)
- [Smart Format](https://developers.deepgram.com/docs/smart-format)
- [Utterances](https://developers.deepgram.com/docs/utterances)
- [Utterance Split](https://developers.deepgram.com/docs/utterance-split)
- [Форматы аудио](https://developers.deepgram.com/docs/supported-audio-formats)
- [Rate limits](https://developers.deepgram.com/reference/api-rate-limits)
- [Ошибки и retry-рекомендации](https://developers.deepgram.com/docs/errors)
- [Keyterm Prompting](https://developers.deepgram.com/docs/keyterm)
- [Model Improvement Program и `mip_opt_out`](https://developers.deepgram.com/docs/the-deepgram-model-improvement-partnership-program)
- [Изменение MIP pricing от 2026-03-05](https://developers.deepgram.com/changelog/2026/3/5)
- [Актуальный pricing](https://deepgram.com/pricing)

## 5. Экономика free credit

На момент подготовки плана Deepgram показывает $200 бесплатного Pay-As-You-Go кредита,
без обязательного минимального платежа; цену нужно перепроверить перед production rollout,
так как тарифы являются внешним изменяемым контрактом.

Для pre-recorded:

| Режим | Текущая цена | Цена часа | Примерно часов на $200 |
|---|---:|---:|---:|
| Nova-3 monolingual (`language=ru`) | $0.0077/мин | $0.462 | 433 ч |
| Nova-3 multilingual (`language=multi`) | $0.0092/мин | $0.552 | 362 ч |

`smart_format` включён. `mip_opt_out=true` не меняет опубликованную цену для
Pay-As-You-Go/Growth по changelog от 2026-03-05; это всё равно повторно проверяется в console
перед длинным бенчем. Не включаем по умолчанию:

- Keyterm Prompting — отдельная доплата; к тому же пока нет надёжного источника
  per-lecture словаря;
- Speaker Diarization — отдельная доплата и новый продуктовый контракт speaker labels;
- Redaction — отдельная доплата и потенциально меняет исходный смысл лекции.

Полный A/B-бенч из трёх 10-минутных отрывков и одной 90-минутной лекции будет стоить
около $0.92 в `ru` или $1.10 в `multi` по текущему прайсу.

## 6. Проведённые smoke-тесты

Ключ использовался только через переменную окружения интерактивного shell; в worktree,
Git, команды с выводом и тестовые артефакты он не записывался. Все аудиофайлы и JSON-ответы
находятся только под `/tmp/deepgram-nova3-smoke/`.

### 6.1. Русский binary-upload

Из четырёх открытых CC BY аудиосэмплов Wikimedia/Shtooka собран WAV длительностью
7.351 с: «русский», «язык», «пример», «правда» с паузами.

- запрос: `model=nova-3`, `language=ru`, `smart_format=true`, `utterances=true`;
- статус: HTTP 200;
- фактическая архитектура: `nova-3` (`general-nova-3`);
- распознано: `Русский Язык Пример Правда` — 4/4 слова;
- получены `words`, `punctuated_word`, confidence и `utterances`.

Тест выполнялся на публичных CC BY образцах; в нём `mip_opt_out` ещё не был передан. Все
следующие тесты, особенно с пользовательским материалом, обязаны передавать
`mip_opt_out=true`.

Обнаружен важный крайний случай: `metadata.duration=7.351`, но `end` последнего слова и
utterance был `9.52`. Искусственная склейка не является quality benchmark, однако ответ
доказывает, что vendor timestamps нельзя безусловно доверять: перед записью SRT нужны clamp,
проверка конечности чисел, сортировка и обеспечение монотонности.

### 6.2. Естественная английская речь

Официальный sample `spacewalk.wav`, 25.933 с:

- HTTP 200 за 2.07 с клиентского времени;
- 62 слова и 7 utterances;
- первый timestamp `0.0`, последний `25.355`, то есть внутри длительности;
- smart formatting, пунктуация и регистр присутствуют;
- фактическая архитектура — Nova-3.

### 6.3. Что smoke-тесты не доказывают

- качество на длинных русских лекциях;
- сохранность англоязычных терминов в режиме `ru`;
- преимущество `ru` или `multi` для реальных материалов LectureLog;
- поведение на шуме, нескольких спикерах и границах очень длинного файла;
- billing и latency на типичной 60–120-минутной лекции.

Это проверяется отдельным A/B-этапом до включения Deepgram по умолчанию.

## 7. Архитектурные решения

### 7.1. Выбор провайдера

Добавить процессный конфиг:

```env
TRANSCRIBE_PROVIDER=groq       # groq | deepgram
GROQ_API_KEYS=

DEEPGRAM_API_KEY=
DEEPGRAM_BASE_URL=https://api.deepgram.com
DEEPGRAM_MODEL=nova-3
DEEPGRAM_LANGUAGE=ru           # ru | multi; технически допускается любой поддержанный код
DEEPGRAM_DETECT_LANGUAGE=false # true: определить доминирующий язык, language не отправлять
DEEPGRAM_UTT_SPLIT=0.8
```

Правила:

- default остаётся `groq`, чтобы merge/deploy не переключил production случайно;
- обязательным становится ключ только выбранного провайдера;
- если выбран `deepgram`, отсутствие/пустота `DEEPGRAM_API_KEY` валит приложение на старте;
- если выбран `groq`, текущая CSV-семантика `GROQ_API_KEYS` сохраняется;
- `DEEPGRAM_API_KEY` хранить как `SecretStr`, не сериализовать и не логировать;
- `DEEPGRAM_BASE_URL` оставляет возможность EU endpoint
  (`https://api.eu.deepgram.com`) без изменения кода;
- base URL обязан быть HTTPS-хостом из allowlist официальных hosted endpoints
  (`api.deepgram.com`, `api.eu.deepgram.com`, `api.au.deepgram.com`), без userinfo/query;
  custom Dedicated/self-hosted endpoint не входит в эту фазу;
- HTTP redirects не follow'ить, чтобы Authorization не мог уйти на другой host;
- каждый STT-запрос без конфигурационного переключателя передаёт `mip_opt_out=true`;
- в лог старта выводить provider/model/language/base host, но никогда ключ.

### 7.2. `ru` против `multi`

Код не должен зашивать окончательный продуктовый выбор. Начальный безопасный default — `ru`,
потому что продукт и промпты ориентированы на русские лекции, monolingual дешевле, а отдельная
русская модель обычно предсказуемее. Перед rollout обязательно сравнить `ru` и `multi` на
лекции с русской речью и англоязычными техническими терминами.

Если `ru` пропускает или транслитерирует значимые английские фразы, production env переводится
на `multi` без изменения кода. Доплата по текущему прайсу — около $0.09 за час аудио.

### 7.3. HTTP напрямую, без нового SDK

Использовать уже имеющийся `httpx`, а не добавлять Deepgram SDK:

- контракт — один стабильный REST endpoint;
- в проекте уже есть асинхронные httpx-паттерны и MockTransport-тесты;
- проще контролировать streaming body, таймауты, ретраи, progress и отсутствие утечки ключа;
- меньше зависимостей и lockfile churn.

SDK можно пересмотреть, если REST-контракт усложнится или понадобится streaming/callback API.

### 7.4. Один файл вместо 20-минутных STT-чанков

Для Deepgram сначала отправлять один локальный файл целиком:

- сохраняется контекст длинной лекции и code-switching;
- нет обрыва слов и потери контекста на границах 20 минут;
- нет вычисляемых offsets и накопления ошибки времени;
- Deepgram принимает файлы до 2 GB;
- для видео pipeline уже извлекает отдельную аудиодорожку.

Тело нельзя собирать через `Path.read_bytes()` для больших WAV. Реализовать повторно
открываемый async file stream с фиксированным `Content-Length`; каждый retry открывает файл
заново. Перед запросом проверить `stat().st_size <= 2 GB`, иначе выдать понятную ошибку до сети.

Если реальный full-lecture тест стабильно упирается в 504 processing timeout, запасной путь —
не немедленно включать callback API, а добавить provider-specific chunking с реальными
`ffprobe`-длительностями чанков и измеренными offsets. Callback потребовал бы нового входящего
API, персистентного request state и корректного resume после рестарта, поэтому это отдельная фаза.

### 7.5. Построение SRT

Источник текста по приоритету:

1. `results.utterances` — сохраняет smart-formatted transcript и паузы;
2. fallback: `results.channels[0].alternatives[0].words`;
3. пустой transcript — пустой SRT, как сейчас у Groq, без искусственной ошибки.

Для каждого caption:

- короткий utterance отдавать как `utterance.transcript`, чтобы сохранить smart formatting;
- слишком длинный utterance делить по его `words` с ограничением, например, 12 слов или
  8 секунд; текст дочерних блоков собирать из `punctuated_word` с fallback на `word`, потому
  что исходный `utterance.transcript` нельзя корректно разрезать по индексам сырых слов;
- пустые/нечисловые элементы пропускать;
- ограничить start/end диапазоном `[0, effective_duration]`, где effective duration —
  конечная положительная `metadata.duration`, а при её отсутствии — точная float-длительность
  `ffprobe`; если обе неизвестны, сохранить монотонность без верхнего clamp и залогировать warning;
- обеспечить `end >= start` и неубывающий порядок блоков;
- после нормализации заново пронумеровать блоки;
- писать атомарно в `output_dir/transcript.srt`;
- не включать confidence, speaker labels или vendor metadata в пользовательский SRT.

Порог 12 слов/8 секунд не считать окончательной истиной: его покрыть тестами и проверить на
реальной лекции. Он нужен, чтобы один длинный Deepgram utterance не превращался в огромный
SRT-блок, который ухудшает тематический split и пользовательские субтитры.

### 7.6. Progress и usage

Progress должен оставаться монотонным даже при повторной загрузке и не создавать DB write
на каждый сетевой chunk. `PipelineService.transcribe_progress()` синхронно вызывает
`repository.update`, поэтому adapter эмитит только фиксированные пороги:

- 5% — файл проверен, длительность определена;
- 10, 20, ..., 70% — streaming upload по переданным байтам, максимум семь callback'ов;
- 90% — успешный ответ получен и провалидирован;
- 100% — SRT записан.

На retry нельзя эмитить меньше уже выданного процента.

До сетевого запроса вызвать `on_usage` с best-effort `ffprobe`:

```json
{"audio_seconds": 123, "provider": "deepgram", "model": "nova-3"}
```

Это сохраняет частичный usage при последующей ошибке, как сейчас. После успешного ответа
повторно вызвать `on_usage` с `int(metadata.duration)`, если duration конечная и положительная:
`UsageAccumulator.record_transcribe()` перезапишет предварительное зерно, и success usage будет
опираться на authoritative provider metadata. Если metadata отсутствует/невалидна, оставить
ffprobe-значение. Публичная usage-схема не меняется.

### 7.7. Таймауты и ошибки

Клиентский read timeout должен быть немного больше документированного 10-минутного серверного
лимита, чтобы клиент не обрывал ещё допустимый запрос. Предлагаемый старт:

- connect 30 с;
- write/upload 300 с;
- read 660 с;
- pool 30 с.

Retry policy (с jitter и ограниченным числом попыток):

- network timeout/reset, 408, 429, 500, 502, 503 — exponential backoff;
- 504 — не более одного повтора, затем понятная ошибка с предложением chunk/callback path;
- 400/401/403/413/415/422 — без бессмысленного retry;
- логировать HTTP status, Deepgram `err_code` и `request_id`, но не Authorization, тело аудио
  или полный ответ;
- 429/503 после исчерпания попыток должны по текущему `classify_error` стать
  `ErrorCode.RATE_LIMIT`;
- adapter должен разбирать безопасные `err_code`/status и переводить только input-сигналы
  (413/415, `ASR_UNPROCESSABLE_ENTITY`, подтверждённый corrupt/unsupported media) в `ValueError`
  -> `BAD_INPUT`; generic 400 нельзя автоматически считать пользовательской ошибкой, потому что
  он также покрывает неверные model/language/query настройки;
- 401/403, неверные model/language/base URL и malformed successful JSON/response shape должны
  стать `INTERNAL` как ошибка конфигурации или контракта провайдера;
- текст исключения ограничить безопасным сообщением; не прикладывать полный vendor body.

## 8. План реализации по шагам

Каждый шаг делать test-first; после шага запускать указанный узкий набор, после всей серии —
полный unit/integration suite.

### Шаг 1. Provider-neutral конфигурация

**Файлы:**

- `lecturelog/config/settings.py`
- `tests/unit/test_config.py`

**Изменения:**

1. Добавить `TranscribeConfig` с provider и provider-specific полями.
2. Сделать `GROQ_API_KEYS` условно обязательным только для `groq`.
3. Сделать `DEEPGRAM_API_KEY` условно обязательным только для `deepgram`.
4. Провалидировать `utt_split > 0`, непустые model/language, допустимый provider и HTTPS
   hosted endpoint из allowlist.
5. Сохранить совместимость существующего production env без новых переменных.

**Тесты:**

- старый env создаёт Groq config;
- CSV Groq keys парсится как раньше;
- Deepgram config создаётся без `GROQ_API_KEYS`;
- отсутствующий ключ выбранного провайдера вызывает startup validation error;
- ключ не появляется в `repr`/`model_dump` открытым текстом;
- неизвестный provider, неположительный `utt_split`, HTTP/userinfo/query и неизвестный host
  Deepgram endpoint отклоняются.

### Шаг 2. Общие безопасные STT/SRT helpers

**Файлы:**

- новый `lecturelog/infrastructure/transcribe/common.py`
- `lecturelog/infrastructure/transcribe/groq_transcriber.py`
- `tests/unit/test_groq_transcriber.py`
- новый `tests/unit/test_transcribe_common.py`

**Изменения:**

1. Вынести без изменения поведения `_emit_progress`, `_emit_usage`, timestamp formatting и
   best-effort `ffprobe`; общий probe возвращает float, а Groq при формировании usage продолжает
   приводить его к int, чтобы не менять существующий контракт.
2. Добавить provider-neutral функции нормализации времени и записи SRT-caption'ов.
3. Перевести Groq на общие helpers без изменения его HTTP/chunk/retry поведения.

**Тесты:**

- sync/async/no-op callbacks;
- отрицательные, NaN/Inf и выходящие за duration таймкоды;
- сортировка, монотонность, перенумерация;
- SRT timestamp >24h остаётся валидным;
- весь существующий `test_groq_transcriber.py` зелёный.

### Шаг 3. Deepgram Nova-3 адаптер

**Файлы:**

- новый `lecturelog/infrastructure/transcribe/deepgram_transcriber.py`
- новый `tests/unit/test_deepgram_transcriber.py`

**Изменения:**

1. Реализовать `DeepgramTranscriber(Transcriber)` с инъекцией transport/client factory для тестов.
2. Проверять существование и размер файла до запроса.
3. Стримить бинарное тело с `Content-Length` и подходящим Content-Type.
4. Передавать model/language/smart_format/utterances/utt_split и обязательный
   `mip_opt_out=true`; redirects отключить.
5. Реализовать таймауты, reopen-on-retry, backoff+jitter и безопасные ошибки.
6. Валидировать response shape и строить нормализованный SRT.
7. Эмитить монотонный progress и provider-neutral usage.

**Обязательные тесты:**

- точный URL/query и `Authorization: Token ...`, при этом секрет не попадает в исключение;
- query всегда содержит `mip_opt_out=true`, redirect не пересылает Authorization;
- binary body передан без multipart-обёртки;
- retry заново читает файл с начала;
- 429/network/503 ретраятся, 401/403/413 — нет;
- malformed JSON/нет channels/нет alternatives обрабатываются явно;
- utterance transcript сохраняет пунктуацию;
- длинный utterance делится;
- fallback на words работает;
- пустой transcript создаёт пустой SRT;
- timestamp больше `metadata.duration` clamp'ится (регресс по smoke-тесту);
- progress не убывает при retry и вызывает callback не более семи раз на upload;
- при failure usage сохраняет ffprobe duration, при success уточняется из metadata.duration;
- input vendor errors мапятся в `BAD_INPUT`, auth/config/malformed JSON — в `INTERNAL`;
- файл >2 GB отклоняется до HTTP (через mock `stat`, без создания гигантского fixture).

Ни один unit test не вызывает реальный Deepgram API.

### Шаг 4. Factory и wiring

**Файлы:**

- `lecturelog/application/factories.py`
- `lecturelog/api/lifespan.py`
- `tests/unit/test_factories.py`
- при необходимости новый `tests/unit/test_lifespan_transcriber_wiring.py`

**Изменения:**

1. Добавить `transcriber_factory(config) -> Transcriber`.
2. Убрать прямой `GroqTranscriber(...)` из lifespan.
3. Логировать выбранные provider/model/language без секрета.
4. Не менять `PipelineService` и domain port.

**Тесты:**

- factory создаёт Groq и Deepgram по конфигу;
- неизвестный provider невозможен после validation;
- оба объекта являются `Transcriber`;
- lifespan не требует Groq key при выбранном Deepgram.

### Шаг 5. Usage, errors и prompt neutrality

**Файлы:**

- `lecturelog/application/usage_accumulator.py`
- `lecturelog/application/error_classifier.py`
- `prompts/section_v1.md`
- `tests/unit/test_usage_accumulator.py`
- `tests/unit/test_error_classifier.py`
- `tests/unit/test_pipeline_service.py`

**Изменения:**

1. Убрать default `provider="groq"`; provider обязан прийти от адаптера, fallback — `unknown`.
2. Переименовать Groq-only комментарии/тесты в provider-neutral.
3. Добавить Deepgram URL/status fixtures для 429/503 и provider config/input cases.
4. Заменить «получен из Whisper STT» в промпте на «получен системой распознавания речи»;
   оставить инструкцию исправлять ASR-артефакты.
5. Проверить инкрементальный `usage` для `provider=deepgram`.

Публичная schema `TranscribeUsage` уже содержит provider/model и не требует миграции/OpenAPI diff.

### Шаг 6. Env, deploy и README

**Файлы:**

- `.env.example`
- `deploy/env.core.example`
- `README.md`
- при необходимости `docs/api-contract.md` только если там обнаружится Groq-only утверждение

**Изменения:**

1. Документировать переключатель и обе группы ключей.
2. Объяснить `ru` vs `multi`, стоимость, обязательный MIP opt-out и отсутствие add-on'ов
   по умолчанию.
3. Обновить секцию запуска: обязательным является ключ выбранного STT provider.
4. Сохранить Groq pool документацию как отдельный provider-specific раздел.
5. Добавить Deepgram limits/retry/rollback и ссылку на текущий pricing.
6. Не помещать реальный тестовый ключ ни в пример, ни в историю Git.

Compose-файлы используют `env_file`, поэтому явного проброса новых переменных в services не нужно;
это подтвердить через `docker compose config` с placeholder env.

### Шаг 7. Автоматическая проверка

Команды из корня worktree. На текущем хосте `/snap/bin/uv` падает при создании transient
scope через DBus, поэтому использовать уже существующий Python 3.12 venv репозитория:

```bash
/root/lecturelog-core/.venv/bin/python -m pytest \
  tests/unit/test_transcribe_common.py \
  tests/unit/test_groq_transcriber.py \
  tests/unit/test_deepgram_transcriber.py \
  tests/unit/test_config.py \
  tests/unit/test_factories.py \
  tests/unit/test_usage_accumulator.py \
  tests/unit/test_error_classifier.py -q

/root/lecturelog-core/.venv/bin/python -m pytest tests/unit -q
/root/lecturelog-core/.venv/bin/python -m pytest tests/integration -q
/root/lecturelog-core/.venv/bin/ruff check .
git diff --check
```

Если OpenAPI не меняется, `scripts/export_openapi.py` не должен давать diff. Если даёт — остановиться
и выяснить непреднамеренное изменение контракта, а не обновлять snapshot автоматически.

## 9. A/B-бенч перед включением

### 9.1. Набор

Использовать только материал, разрешённый для отправки внешнему STT-провайдеру:

1. 10–15 минут чистой русской лекции;
2. 10–15 минут русской технической лекции с английскими терминами/названиями;
3. 10–15 минут шумной записи или диалога с аудиторией;
4. одна полная 60–90-минутная лекция для end-to-end проверки.

Секретные/чувствительные записи нельзя отправлять только потому, что они найдены на диске;
для них нужно отдельное подтверждение допустимости внешней обработки.

### 9.2. Варианты

На одинаковом аудио сравнить:

- текущий Groq Whisper;
- Deepgram Nova-3 `language=ru`;
- Deepgram Nova-3 `language=multi` на техническом отрывке.

### 9.3. Метрики

- WER/CER на вручную проверенных 3–5 мин каждого отрывка;
- recall важных русских и английских терминов;
- пунктуация и читаемость;
- доля пустых/явно галлюцинированных сегментов;
- корректность и монотонность SRT, последний end <= duration;
- наличие `mip_opt_out=true` в Deepgram request log/console;
- wall-clock latency;
- фактический billed duration/cost в Deepgram console;
- end-to-end: задача `done`, структуризация не теряет текст, кадры/нарезка совпадают по времени;
- `usage.transcribe.provider/model/audio_seconds` в статусе задачи.

### 9.4. Критерий выбора языка

- оставить `ru`, если он сохраняет значимые английские термины и не хуже `multi` на русском;
- выбрать `multi`, если он заметно улучшает code-switching/термины без существенной деградации
  русской части;
- решение и примеры ошибок записать в progress/report, а не принимать по одному smoke sample.

## 10. Rollout и rollback

1. Merge кода с default `TRANSCRIBE_PROVIDER=groq`.
2. Добавить новый production-grade Deepgram key в секретный env, не меняя provider.
3. На dev/canary переключить `TRANSCRIBE_PROVIDER=deepgram` и выбранный язык.
4. Прогнать набор из раздела 9 и 5–10 обычных задач.
5. Мониторить HTTP 4xx/429/5xx, latency, пустые SRT, timestamp clamp warnings и расход кредита.
6. После успешного canary изменить только env основного инстанса.
7. Rollback: вернуть `TRANSCRIBE_PROVIDER=groq` и перезапустить API; БД, S3 и API не меняются.

Уже начатая задача при рестарте, как и сейчас, станет `interrupted`; бесшовного переключения
провайдера посреди задачи не предполагается.

## 11. Безопасность ключа

- Тестовый ключ был передан в чате и использован для smoke-тестов; считать его временным.
- После завершения экспериментов отозвать/ротировать его в Deepgram console.
- Для dev/prod создать отдельные ключи с понятными именами и минимально нужными правами.
- Хранить ключ только в серверном `.env`/secret manager; не в Git, plan, CI artifacts или логах.
- Не логировать request headers, полный config dump или vendor response целиком.
- При ошибке сохранять только status, `err_code`, `request_id` и безопасное короткое сообщение.

## 12. Definition of Done

- Groq остаётся рабочим и default после merge.
- Deepgram Nova-3 выбирается одним env-переключателем и не требует Groq key.
- Каждый Deepgram-запрос использует `mip_opt_out=true`; ключ и аудио не попадают в логи.
- Реальный binary-upload возвращает SRT, совместимый с текущими structurize/frames/API путями.
- Таймкоды валидны, монотонны и не выходят за duration.
- Retry/progress/usage работают и покрыты детерминированными unit tests без сети.
- Внешний API и БД не меняются; OpenAPI snapshot не имеет непреднамеренного diff.
- README и deploy env описывают provider, язык, free-credit экономику и rollback.
- Проведён A/B на разрешённых реальных материалах, отдельно принято решение `ru` или `multi`.
- Dev-canary завершает полную аудио- и видео-задачу.
- Тестовый ключ ротирован до production rollout.
