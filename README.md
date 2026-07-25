# LectureLog

<p align="center">
  <img src="docs/assets/preview.png" alt="LectureLog — лекция превращается в структурированный конспект Obsidian" width="100%">
</p>

HTTP-сервис обработки лекций: на вход — лекция в виде аудиозаписи, видеофайла или
ссылки на видео (YouTube, публичные посты X и обычные HTTP(S)-URL), опционально +
слайды PDF/PPTX; на выходе —
структурированный конспект в формате Obsidian (Markdown + нарезанные
медиафрагменты + слайды), упакованный в ZIP.

## Что умеет

1. **Приём медиа**: аудиофайл, видеофайл или URL видео (скачивается через yt-dlp);
   для видео извлекается аудиодорожка для транскрибации.
2. **Транскрибация** аудио в SRT через Groq Whisper или Deepgram Nova-3.
3. **Слайды** — три источника: из приложенного PDF/PPTX (pymupdf + LibreOffice),
   автоматически из видеоряда через Gemini Vision, либо без слайдов (флаг `no_slides`).
4. **Структуризация** транскрипта на темы и подтемы через Gemini, с привязкой слайдов.
5. **Кадры из видео** (стадия `video_slides`, только для видео-лекций без
   приложенного документа слайдов) — из видеоряда автоматически отбираются
   осмысленные стопкадры (слайд / доска в момент «дописал» / финальный код
   live-coding), проверяются VLM и привязываются к секциям конспекта по
   таймкодам. Управляется `FRAMES_ENABLED`.
6. **Нарезка медиа** по секциям конспекта через ffmpeg (аудио- или видеофрагменты).
7. **Экспорт** в ZIP: `конспект.md` (с виджетом плеера Obsidian) + медиафрагменты + слайды/кадры.

Состояние задач хранится в Postgres и переживает рестарт сервиса. Зависшие после рестарта
задачи автоматически помечаются как `interrupted`. Несколько лекций обрабатываются параллельно
(лимит — `MAX_CONCURRENT_TASKS`), остальные ждут в очереди.

Качество готового ZIP можно проверить отдельным offline-evaluator: статически без сети
или через бесплатную judge-модель OpenRouter с кэшем и лимитом запросов. Он оценивает
достоверность и полноту конспекта, качество и язык блоков, структуру и размещение слайдов.
Инструкция: [docs/evaluation.md](docs/evaluation.md).

### Режимы слайдов

- **видео без слайдов-документа** → слайды извлекаются автоматически из видеоряда (Gemini Vision);
- **приложен PDF/PPTX** (`slides`) → слайды берутся из документа (документ приоритетнее видео);
- **флаг `no_slides`** → слайды не делаются.

Для аудио слайды есть только при приложенном документе.

Для документных слайдов доступны `legacy`, `shadow` и `v2` через
`DOCUMENT_SLIDE_ALIGNMENT_MODE`. В `v2` страница попадает inline только при
проверяемом свидетельстве в SRT и безопасном Markdown-anchor; менее уверенные
страницы остаются галереей раздела, а неупомянутые выводятся в отдельном
приложении. `shadow` считает диагностику, но сохраняет legacy-результат.

### Кадры из видео (стадия `video_slides`)

Отдельно от «Режимов слайдов» выше: для видео-лекций **без** приложенного документа
слайдов и без `no_slides` дополнительно работает стадия отбора кадров — она извлекает
из видеоряда не «слайды один в один», а осмысленные стопкадры (доска в момент
«дописал», финальный код live-coding и т. п.) и привязывает их к секциям конспекта по
таймкодам, уже после структуризации (на структуризацию кадры не влияют).

- Приложен документ-слайды → приоритет у документа, стадия кадров не запускается.
- Стадия построена по принципу **никогда не роняет задачу**: любой сбой на любом шаге —
  конспект просто получается без кадров, остальной пайплайн отрабатывает как обычно.
- Без доступного VLM (исчерпан бесплатный тир / `FRAMES_ENABLED=false` не влияет на
  сам VLM, но при отказе LLM) стадия деградирует: классификация кадров идёт по
  временным сигнатурам без LLM-подтверждения, финальный QC пропускается — итоговый
  набор кадров чуть менее вычищен, но стадия всё равно отрабатывает.
- Классификация режимов и финальный QC настраиваются отдельно: 1–2 вызова
  классификатора можно отправлять на более тяжёлую модель, а массовый QC оставить
  на дешёвом списке.
- Для массового VLM-QC первой используется экономичная
  `gemini-3.5-flash-lite`; фактическая стоимость зависит от объёма кадров и
  текущего тарифа Google AI Studio.

## Результат в Obsidian

Внутри ZIP — `конспект.md` и подкаталоги с нарезанными аудиофрагментами и слайдами.
Распакуйте архив в свой Obsidian-vault и откройте `конспект.md`.

Для каждой секции конспекта встроен виджет аудиоплеера: соответствующий фрагмент лекции
можно прослушать прямо из заметки. Виджет рендерится плагином
**[Audio Player](obsidian://show-plugin?id=obsidian-audio-player)** — установите его в
Obsidian (Settings → Community plugins), иначе вместо плеера будет виден сырой код-блок
` ```audio-player `.

### Пример конспекта

Готовый пример — конспект доклада **Philip O'Toole «Build Your Own Distributed System Using Go»**
([оригинал на YouTube](https://youtu.be/8XbxQ1Epi5w)), собранный в видео-режиме: оглавление по темам,
слайды из видеоряда, таймкоды с встроенным плеером и нарезанные видеофрагменты.

**[⬇ Скачать пример (ZIP, ~154 МБ)](https://github.com/fUS1ONd/LectureLog/releases/latest)**

Распакуйте архив в Obsidian-vault и откройте `конспект.md`.

## Запуск

Запуск через Docker Compose:

```bash
cp .env.example .env
# Выберите TRANSCRIBE_PROVIDER, заполните ключ выбранного STT-провайдера,
# OPENROUTER_API_KEY, CORE_POSTGRES_PASSWORD,
# S3_ACCESS_KEY и S3_SECRET_KEY.
docker compose up --build
```

Поднимутся сервисы: `db` (Postgres 16), `minio` (S3-хранилище лекций), `minio-init`
(разовое создание бакета и применение lifecycle-правил — см. ниже) и `api`. Миграции применяются
автоматически на старте контейнера. По умолчанию MinIO наружу не выставлен (`S3_PUBLIC_ENDPOINT`
не задан) — движок ходит к нему по internal-endpoint внутри docker-сети, presigned-эндпоинты
выключены, работает автономно. API доступен на `http://localhost:8000`.

Сервис `minio-init` (скрипт `docker/minio-init.sh`) идемпотентно настраивает lifecycle (ILM)
бакета `lectures`: `uploads/` — Expiration 7 дней и AbortIncompleteMultipartUpload 1 день
(чистка сырых исходников и orphan-частей оборванных presigned-заливок); `results-tmp/` —
Expiration 1 день (временные ZIP от `/result-url`). Префикс `results/` (постоянные результаты)
правил НЕ имеет и живёт до явного `DELETE /tasks/{id}`. Образы MinIO/mc запинены по тегам
релизов (а не `:latest`) для воспроизводимости.

В образе уже присутствуют системные зависимости видео-режима: `ffmpeg`/`ffprobe`
(нарезка фрагментов и извлечение кадров), `yt-dlp` (скачивание видео по URL), а также
питон-пакеты `opencv-python-headless` и `numpy` (локальный анализ видеоряда для
стадии `video_slides`).

Для X поддерживаются публичные `x.com`/`twitter.com`-посты без cookies. Если пост
содержит несколько видео, URL поста выбирает первое вложение, а суффикс `/video/N` —
явно указанное. Закрытые, удалённые и требующие авторизации посты не поддерживаются.
Качество всех URL-видео задаёт `VIDEO_TARGET_RESOLUTION`: числовое значение является
ориентационно-независимой мягкой целью, `best` снимает ограничение.

Проверка:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok"}
```

### VPS: готовый образ из GHCR

Основной prod-like сценарий для быстрой проверки — готовый Docker image из GHCR,
без сборки исходников на сервере. Каналы образов:

| Docker tag | Откуда берётся | Назначение |
|---|---|---|
| `dev` | каждый push в git-ветку `dev` | быстрый прод-чек текущей разработки |
| `latest` | git tag `v*` | стабильный релиз |
| `vX.Y.Z` | git tag `vX.Y.Z` | воспроизводимый релиз |

`latest` — это Docker-тег, не git-ветка. Стабильный код живёт в `main`, активная
разработка — в `dev`.

Минимальное развёртывание core на VPS:

```bash
mkdir -p /opt/lecturelog-core
cd /opt/lecturelog-core
curl -fsSLo docker-compose.yml https://raw.githubusercontent.com/LectureLog/lecturelog-core/refs/heads/dev/deploy/compose.vps.yml
curl -fsSLo .env https://raw.githubusercontent.com/LectureLog/lecturelog-core/refs/heads/dev/deploy/env.core.example
curl -fsSLo minio-init.sh https://raw.githubusercontent.com/LectureLog/lecturelog-core/refs/heads/dev/deploy/minio-init.sh
chmod +x minio-init.sh
docker network create lecturelog-shared || true
```

Отредактируйте `.env`: выберите `TRANSCRIBE_PROVIDER`, задайте ключ выбранного
STT-провайдера, `OPENROUTER_API_KEY`,
`CORE_POSTGRES_PASSWORD`, `S3_SECRET_KEY`, публичный `S3_PUBLIC_ENDPOINT` и общий
с web `LECTURELOG_WEBHOOK_SECRET`. Для связки с web укажите:

```env
PLATFORM_CALLBACK_URL=https://app.example.com/webhooks/core
S3_PUBLIC_ENDPOINT=https://files.example.com
```

Затем запустите:

```bash
docker compose pull
docker compose up -d
docker compose logs -f api
```

Для dev-проверки оставьте `LECTURELOG_CORE_IMAGE_TAG=dev`. Для стабильного канала
используйте `latest`, для воспроизводимого деплоя — конкретный тег, например
`v0.2.0`.

API и MinIO публикуются только на `127.0.0.1`; публичный HTTPS-доступ должен идти
через nginx/caddy. Core подключается к общей Docker-сети `lecturelog-shared`, чтобы
web мог обращаться к API по `http://lecturelog-core-api:8000`.

### Выпуск релиза

1. Проверьте, что `dev` зелёный и его образ `:dev` проверен на VPS.
2. Перенесите проверенный код в `main`.
3. Поставьте semver-тег и отправьте его в GitHub:

```bash
git checkout main
git merge --ff-only dev
git tag v0.2.0
git push origin main v0.2.0
```

GitHub Actions соберёт `ghcr.io/lecturelog/lecturelog-core:v0.2.0`,
обновит `ghcr.io/lecturelog/lecturelog-core:latest` и создаст GitHub Release.

## API

Базовый префикс — `/api/v1`.

### Машиночитаемый контракт (OpenAPI)

Схема API доступна в машинном виде и служит источником правды для генерации
типизированного клиента в platform-api.

- **Живая схема** (при запущенном сервере): `GET /openapi.json` — сама спека,
  `/docs` — Swagger UI, `/redoc` — ReDoc. Эти эндпоинты FastAPI отдаёт на корне,
  вне префикса `/api/v1`.
- **Снапшот в репозитории**: `docs/openapi.json` — закоммиченная актуальная версия
  схемы (типы `usage` в `GET /tasks/{id}`, коды ответов, `summary`/`tags`).
- **Регенерация локально**: `python scripts/export_openapi.py` обновляет
  `docs/openapi.json` без запуска сервера и без реальных секретов (используются
  env-заглушки).
- **Проверка в CI**: джоба `openapi` сверяет снапшот с кодом
  (`git diff --exit-code`) — если API изменился, а схему не перегенерировали,
  сборка краснеет.
- **Внеконтрактные флоу**: `docs/api-contract.md` описывает то, чего нет в OpenAPI
  by design — presigned-загрузку/скачивание, исходящий вебхук, два endpoint'а
  MinIO, HMAC-контур доверия и автономные режимы.

| Метод  | Путь                                     | Описание                                                                                                                                         |
| ------ | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POST` | `/tasks`                                 | Создать задачу. multipart: ровно один источник — `audio` (file) / `video` (file) / `video_url` (form) / `s3_key` (form, ссылка на загруженный в MinIO объект под `uploads/`; для него ещё `media`: `audio`\|`video`); опционально `slides` (file) и `no_slides` (form, bool). Возвращает `{"task_id": "<hex>"}`. |
| `POST` | `/uploads`                               | Выдать presigned PUT URL для загрузки исходника платформой в `uploads/`. Тело: `{filename}`. Ответ: `{key, url, expires_in}`. `409`, если `S3_PUBLIC_ENDPOINT` не задан. |
| `GET`  | `/tasks/{id}`                            | Статус задачи: `{task_id, stage, progress_pct, error, error_code, result_path, usage}`. `result_path` — S3-префикс папки результата (`results/<task_id>/`). `error_code` — машинный код ошибки (enum, `null` вне ошибочного статуса). `usage` — разбивка расхода ресурсов по стадиям и моделям (см. ниже). |
| `GET`  | `/tasks/{id}/transcript?format=srt\|txt` | Транскрипт (SRT или plain text).                                                                                                                 |
| `GET`  | `/tasks/{id}/result`                     | Готовый ZIP (`application/zip`), собирается НА ЛЕТУ из объектов под `results/<task_id>/` и стримится клиенту (MinIO клиенту не виден; дефолт для консоли/автономии).                                            |
| `GET`  | `/tasks/{id}/result-url`                 | Presigned GET URL на ZIP результата: `{url, expires_in}`. ZIP собирается во временный объект `results-tmp/<task_id>/<uuid>.zip` (его чистит lifecycle MinIO / DELETE). Опц. параметр `filename` зашивается в `Content-Disposition` (имя `<filename>.zip`). `409`, если `S3_PUBLIC_ENDPOINT` не задан; `404`, если результат не готов. |
| `DELETE` | `/tasks/{id}`                          | Идемпотентно удалить задачу: чистит объекты в MinIO (весь префикс `results/<task_id>/`, временные `results-tmp/<task_id>/` и связанный `uploads/`-исходник) и строку в БД. Повтор на уже удалённую/неизвестную задачу → `204`. |
| `GET`  | `/health`                                | Healthcheck.                                                                                                                                     |

### Коды ответов

- `POST /tasks`: `200` — успех; `400` — не ровно один источник, `video_url` без http/https-схемы, `media` не `audio`/`video` или `s3_key` вне `uploads/`.
- `POST /uploads`: `200` — `{key, url, expires_in}`; `409` — `S3_PUBLIC_ENDPOINT` не задан.
- `GET /tasks/{id}`: `200` — статус; `404` — задача не найдена.
- `GET /tasks/{id}/result-url`: `200` — `{url, expires_in}`; `409` — `S3_PUBLIC_ENDPOINT` не задан; `404` — результат не готов.
- `GET /tasks/{id}/transcript`:
  - `400` — `format` не `srt`/`txt`: `{"error":"invalid_format","allowed":["srt","txt"]}`
  - `404` — задачи нет: `{"error":"task_not_found"}`
  - `409` — упало на транскрибации: `{"error":"transcribe_failed","detail":"..."}`
  - `202` — ещё не готово: `{"status":"in_progress","stage":...,"progress":...}`
  - `200` — готово (SRT-файл или plain text).
- `GET /tasks/{id}/result`: `200` — ZIP; `404` — результат не готов / файл не найден / задачи нет.
- `DELETE /tasks/{id}`: `204` — задача и её объекты удалены (идемпотентно, в т.ч. на неизвестную задачу).

### Учёт расхода ресурсов (`usage`)

Ответ `GET /tasks/{id}` содержит поле `usage` — JSON с разбивкой потраченных ресурсов
по стадиям и моделям (стадия × модель). Это ядро движка, а не платформенная фича, поэтому
видно всем клиентам, включая консольный режим. Накапливается инкрементально по мере прохождения
стадий: на `failed`/`interrupted` содержит частично накопленное.

- `transcribe` — `{audio_seconds, provider, model}`.
- `structurize`, `video_slides` — `{provider, by_model: {<model>: {prompt, output, calls}}}`
  (стадия `video_slides` присутствует только если слайды извлекались из видеоряда).
- `total` — сводка: `{audio_seconds, gemini_prompt, gemini_output, source, slides_origin}`,
  где `source` — `audio`\|`video`, а `slides_origin` — `none`\|`document`\|`video_extracted`.

### Вебхук на терминальные события (опционально)

Если задан `PLATFORM_CALLBACK_URL`, на каждое терминальное событие лекции (`done`/`failed`/`interrupted`)
движок шлёт одну исходящую `POST` — fire-and-forget, с коротким таймаутом и без ретраев.
Без `PLATFORM_CALLBACK_URL` движок работает автономно как раньше (поллинг `GET /tasks/{id}` всегда доступен).

Тело тонкое — `{task_id, status, error, error_code}` (`status`: `done`\|`failed`\|`interrupted`; ключи
`error`/`error_code` присутствуют всегда, вне ошибочного статуса — `null`); полное состояние
(`usage`, `result_path`) платформа добирает через `GET /tasks/{id}`. Тело подписывается
HMAC-SHA256 секретом `LECTURELOG_WEBHOOK_SECRET`, подпись — в заголовке `X-Webhook-Signature`.

### Хранилище и загрузка через MinIO

Исходники и результаты лежат в S3-совместимом хранилище (MinIO): исходники — под `uploads/`,
результат — папкой `results/<task_id>/` (отдельные объекты `output/...` + нейтральное дерево
`structure.json`); единый ZIP не хранится, а собирается на лету при скачивании. Временные ZIP
от `/result-url` складываются под `results-tmp/<task_id>/`. Возможны два сценария.

- **Автономный (дефолт)**: исходник передаётся multipart-файлом в `POST /tasks`, результат
  забирается стримом через `GET /tasks/{id}/result`. MinIO наружу не выставлен, presigned-ссылки
  не нужны — так работает консольный клиент.
- **С платформой**: платформа берёт presigned PUT через `POST /uploads`, грузит объект в `uploads/`
  напрямую в MinIO, затем создаёт задачу через `POST /tasks` с `s3_key`; готовый результат отдаётся
  presigned GET через `GET /tasks/{id}/result-url`. Активно только при заданном `S3_PUBLIC_ENDPOINT`.

### Клиентский скрипт

Вместо сырых `curl`-запросов удобнее пользоваться `scripts/submit_task.py` — клиентом
на чистой стандартной библиотеке (без внешних зависимостей). Базовый URL берётся из
переменной окружения `LECTURELOG_URL` или флага `--base` (по умолчанию
`http://localhost:8000/api/v1`).

```bash
# аудио (+ опционально слайды) -> печатает task_id
python scripts/submit_task.py submit --audio lecture.mp3 --slides slides.pdf

# видео (слайды извлекаются из видеоряда автоматически)
python scripts/submit_task.py submit --video lecture.mp4
python scripts/submit_task.py submit --video-url "https://youtu.be/abc"
python scripts/submit_task.py submit --video-url "https://x.com/i/status/2078106556634124335"

# видео без слайдов
python scripts/submit_task.py submit --video lecture.mp4 --no-slides

# разовый статус
python scripts/submit_task.py status <task_id>

# опрашивать статус, пока задача не завершится (done/failed)
python scripts/submit_task.py poll <task_id>

# скачать готовый ZIP
python scripts/submit_task.py result <task_id> -o out.zip

# забрать транскрипт (srt|txt)
python scripts/submit_task.py transcript <task_id> --format txt
```

Если API поднят не на localhost, укажите адрес:

```bash
export LECTURELOG_URL=http://my-host:8000/api/v1
# или разово:
python scripts/submit_task.py --base http://my-host:8000/api/v1 status <task_id>
```

#### Команды

| Команда      | Аргументы                                   | Что делает                                                                      |
| ------------ | ------------------------------------------- | ------------------------------------------------------------------------------- |
| `submit`     | ровно один из `--audio <файл>` / `--video <файл>` / `--video-url <url>`; опционально `--slides <файл>`, `--no-slides` | Создаёт задачу из аудио/видео и опциональных слайдов. Печатает `task_id`. |
| `status`     | `<task_id>`                                 | Разовый запрос статуса задачи, печатает JSON-ответ.                              |
| `poll`       | `<task_id>`, `--interval <сек>` (по умолч. 3) | Опрашивает статус с заданным интервалом, пока задача не завершится (`done`/`failed`). |
| `result`     | `<task_id>`, `-o/--output <файл>` (по умолч. `result.zip`) | Скачивает готовый ZIP с конспектом на диск.                          |
| `transcript` | `<task_id>`, `--format srt\|txt` (по умолч. `srt`) | Печатает транскрипт в stdout (можно перенаправить в файл).                  |

Общий флаг `--base <url>` доступен у всех команд и переопределяет базовый URL API.

## Конфигурация

Переменные окружения (см. `.env.example`):

| Переменная             | Назначение                                                |
| ---------------------- | --------------------------------------------------------- |
| `TRANSCRIBE_PROVIDER`  | STT-провайдер: `groq` (по умолчанию) или `deepgram`. |
| `GROQ_API_KEYS`        | Ключи Groq через запятую; обязательны только для провайдера `groq`. |
| `DEEPGRAM_API_KEY`     | Ключ Deepgram; обязателен только для провайдера `deepgram`. |
| `DEEPGRAM_BASE_URL`    | Официальный HTTPS endpoint Deepgram. |
| `DEEPGRAM_MODEL`       | Модель Deepgram (по умолчанию `nova-3`). |
| `DEEPGRAM_LANGUAGE`    | Язык Deepgram (по умолчанию `ru`). |
| `DEEPGRAM_DETECT_LANGUAGE` | Автоопределение доминирующего языка (`true`/`false`). При `true` фиксированный `DEEPGRAM_LANGUAGE` не отправляется. |
| `DEEPGRAM_UTT_SPLIT`   | Порог паузы utterance в секундах (по умолчанию `0.8`). |
| `OPENROUTER_API_KEY`   | Ключ OpenRouter; LLM-вызовы идут через BYOK Google AI Studio. |
| `OPENROUTER_BASE_URL`  | Base URL OpenRouter (по умолчанию `https://openrouter.ai/api/v1`). |
| `VIDEO_TARGET_RESOLUTION` | Целевое разрешение URL-видео: `144..4320` либо `best`; по умолчанию `720`. |
| `LLM_MODELS_*`         | Приоритетные списки моделей по этапам структуризации (fallback при 429). |
| `LLM_CONCURRENCY_*`    | Параллельность вызовов LLM по этапам.                     |
| `LLM_EFFORT_*`         | Reasoning effort по этапам структуризации (по умолчанию `low`). |
| `FRAMES_ENABLED`       | Вкл./выкл. стадии отбора кадров из видео (`video_slides`). По умолчанию `true`. |
| `DOCUMENT_SLIDE_ALIGNMENT_MODE` | Привязка PDF/PPTX: `legacy`, `shadow` или `v2`; по умолчанию `legacy`. |
| `LLM_MODELS_VIDEO_SLIDES` | Приоритетный список VLM-моделей для QC кадров (fallback при 429). |
| `LLM_EFFORT_VIDEO_SLIDES` | Reasoning effort для QC кадров (по умолчанию `low`). |
| `LLM_MODELS_FRAMES_CLASSIFY` | Приоритетный список VLM-моделей для классификации режимов видео. |
| `LLM_EFFORT_FRAMES_CLASSIFY` | Reasoning effort для классификации режимов (по умолчанию `medium`). |
| `DATABASE_URL`         | Async-URL Postgres (`postgresql+asyncpg://...`).          |
| `S3_INTERNAL_ENDPOINT` | Endpoint MinIO для движка внутри docker-сети (напр. `http://minio:9000`). |
| `S3_PUBLIC_ENDPOINT`   | Опц. публичный хост для presigned-ссылок наружу. Не задан → presigned не выдаётся (`/uploads` и `/result-url` отдают 409), работает только стрим. |
| `S3_BUCKET`            | Бакет хранилища лекций.                                   |
| `S3_ACCESS_KEY`        | Access key MinIO/S3.                                      |
| `S3_SECRET_KEY`        | Secret key MinIO/S3.                                      |
| `S3_REGION`            | Регион S3 (по умолчанию `us-east-1`).                    |
| `S3_PRESIGN_EXPIRY`    | TTL presigned-ссылок в секундах (по умолчанию `3600`).   |
| `MAX_CONCURRENT_TASKS` | Сколько лекций обрабатывать одновременно.                 |
| `PLATFORM_CALLBACK_URL`| Опц. URL для вебхука на терминальные события. Не задан → движок работает автономно. |
| `LECTURELOG_WEBHOOK_SECRET` | Секрет для HMAC-SHA256 подписи тела вебхука (заголовок `X-Webhook-Signature`). |

## Ключи API и лимиты бесплатных тиров

Сервис поддерживает Groq и Deepgram для STT, а LLM вызывает через OpenRouter BYOK.
Для Groq можно указывать несколько ключей через запятую в
`GROQ_API_KEYS`; LLM-вызовы используют один `OPENROUTER_API_KEY`, а fallback идёт
по приоритетному списку моделей.

Получить бесплатные ключи:

- Groq — [console.groq.com/keys](https://console.groq.com/keys)
- Deepgram — [console.deepgram.com](https://console.deepgram.com/)
- Google AI Studio key для OpenRouter BYOK — [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### Groq (транскрибация, Whisper large-v3)

- Ключи из `GROQ_API_KEYS` образуют round-robin пул.
- При ответе `429` (rate limit) или `503` использованный ключ помечается «перегретым»
  и блокируется на **60 секунд** — пул автоматически переключается на следующий доступный ключ.
- Если все ключи временно заблокированы, запрос ждёт минимально необходимое время до
  освобождения ближайшего ключа.
- Чем больше ключей — тем выше суммарная пропускная способность транскрибации.

### Deepgram (транскрибация, Nova-3)

- Задайте `TRANSCRIBE_PROVIDER=deepgram` и `DEEPGRAM_API_KEY`.
- Файл отправляется одним потоковым запросом, без полной загрузки в память.
- Каждый запрос содержит `mip_opt_out=true`; автоматического fallback на Groq нет.
- Разрешены только официальные HTTPS endpoint'ы Deepgram. Дефолты: модель `nova-3`,
  язык `ru`, `utt_split=0.8`.
- Для автоматического определения доминирующего языка задайте
  `DEEPGRAM_DETECT_LANGUAGE=true`. Для смешанной речи с переключением языков
  используйте `DEEPGRAM_LANGUAGE=multi` при выключенном автоопределении.
- Временные сетевые и серверные ошибки повторяются с backoff; неподдерживаемое или
  повреждённое аудио классифицируется как `bad_input`.

### LLM через OpenRouter BYOK (структуризация и VLM)

OpenRouter вызывается в режиме BYOK с провайдером `google-ai-studio`, без fallback на
чужие провайдеры. Для каждой стадии задаётся приоритетный список моделей; при `429`
конкретная модель временно ставится на cooldown, после чего клиент пробует следующую
модель из списка.

Текущий набор моделей:

| Модель                           | Роль |
| -------------------------------- | ---- |
| `google/gemini-3.6-flash`        | Основная модель для критичных контентных и VLM-решений. |
| `google/gemini-3.5-flash-lite`   | Быстрая модель для массового рендера и VLM-QC. |
| `google/gemini-3.5-flash`        | Стабильный резерв при исчерпании лимитов новых моделей. |

Лимиты Google AI Studio зависят от проекта и usage tier; актуальные RPM/RPD
нужно проверять в AI Studio для проекта, чей BYOK-ключ подключён к OpenRouter.

- Модели для каждого этапа задаются приоритетным списком (`LLM_MODELS_*`).
- Для `video_slides` есть два списка: `LLM_MODELS_FRAMES_CLASSIFY` для дешёвых по
  количеству, но критичных решений классификатора и `LLM_MODELS_VIDEO_SLIDES` для QC.
- Несколько Google AI Studio ключей сейчас не ротируются в core; для увеличения RPD
  нужно выпускать отдельный OpenRouter key/конфигурацию на окружение.

## Тесты

```bash
pytest
```

Юнит-тесты гоняют репозиторий на SQLite in-memory, а инфраструктурные зависимости
(Groq/Deepgram/LLM/ffmpeg) мокаются — реальные ключи и внешние сервисы для тестов не нужны.

## Линтер и форматтер

Код проверяется и форматируется через [Ruff](https://docs.astral.sh/ruff/):

```bash
ruff check .          # линтер
ruff format --check . # проверка форматирования (без правок)

ruff check --fix .    # автоисправление линт-ошибок
ruff format .         # отформатировать код
```

Настройки правил и форматирования — в `pyproject.toml` (секция `[tool.ruff]`).

## Архитектура

```
lecturelog/
  domain/          модели, enums, порты, исключения — без зависимостей от инфраструктуры
  application/     ProgressPlan, PipelineService (оркестрация), use-cases, PipelineWorker
  infrastructure/  реализации портов: transcribe, structurize, slides, media, export, persistence, llm
  api/             FastAPI: роуты, DTO, обработчики исключений, lifespan (composition root)
  config/          настройки через pydantic-settings
migrations/        Alembic
```

Поток обработки:
- **аудио**: `transcribe → slides → structurize → audio_cut → export`;
- **видео**: `ingest → extract_audio → transcribe → slides → structurize → video_slides → video_cut → export`
  (`video_slides` — отбор кадров, работает только если не приложен документ-слайдов
  и не установлен `no_slides`; управляется `FRAMES_ENABLED`).

Выбор реализаций (нарезка, источник слайдов) инкапсулирован в фабриках, а не в ветвлениях
`if is_video` — `domain` не зависит от инфраструктуры, видео добавлено через реализации
тех же портов. Прогресс по стадиям инкапсулирован в `ProgressPlan`; статус персистится в
Postgres после каждого шага.
