# План: загрузка видео из X и ориентационно-независимый выбор разрешения

Дата: 2026-07-19

Ветка: `plan/x-video-ingestion`

База реализации: `origin/dev@dadd025bc827572e4542a4045db52c93cc378a63`

Worktree: `/root/lecturelog-core/.worktrees/x-video-ingestion`

## 1. Цель

Добавить X (`x.com` и `twitter.com`) как явно поддерживаемый источник `video_url`, не
меняя внешний API задачи и последующие стадии обработки. Одновременно убрать
зашитое в команду `yt-dlp` ограничение `height<=720`, которое ошибочно понижает
вертикальное видео до `320x568`, и заменить его на настраиваемую,
ориентационно-независимую политику качества.

После изменения одна и та же настройка `720` должна выбирать:

- `1280x720` для горизонтального видео;
- `720x1280` для вертикального видео;
- лучший доступный вариант на целевом уровне или ниже; если таких форматов нет,
  `yt-dlp` мягко деградирует к наименьшему варианту выше цели, а не роняет задачу;
- максимальное доступное разрешение, если администратор явно выбрал `best`;
- progressive HTTPS используется как tie-breaker при одинаковом разрешении, но не
  имеет приоритета над видео более высокого качества.

## 2. Подтверждённое текущее состояние

### 2.1. Контракт и пайплайн

- `POST /api/v1/tasks` уже принимает `video_url`; новый request-параметр не нужен.
- `VideoUrlSource.kind` уже равен `video_url`, поэтому БД, `source_kind`, usage и
  webhook менять не требуется.
- После ingest источник становится локальным видео. Извлечение аудио, транскрибация,
  кадры, нарезка и экспорт не зависят от исходного сайта.
- `VideoIngestor` передаёт любой HTTP(S)-URL в один метод `_download_youtube` и для
  всех сайтов применяет YouTube cookies, Deno и EJS.
- Формат зафиксирован как
  `bestvideo[height<=720]+bestaudio/best[height<=720]`. Для portrait-видео это
  ограничение физической высоты, а не уровня разрешения.
- `--no-playlist` сейчас отсутствует. Для X-поста с несколькими видео один URL может
  породить несколько entries при единственном ожидаемом файле `video.mp4`.

### 2.2. Живые проверки `yt-dlp 2026.07.04`

Проверка выполнялась без cookies и без JS runtime:

| Сценарий | Результат |
|---|---|
| X GraphQL API | работает анонимно |
| X syndication API | работает анонимно; пригоден как fallback |
| X legacy API | на проверенных постах возвращает 404; не использовать |
| Пять параллельных запросов к публичному посту | все успешны без cookies |
| Обычный X-пост с несколькими видео | возвращает playlist |
| `--no-playlist` для такого поста | недостаточно: entries всё равно несколько |
| `--playlist-items 1` | детерминированно выбирает первое вложение |
| URL с `/video/2` + `--playlist-items 1` | сохраняет явно выбранное второе вложение |

Контрольная ссылка `https://x.com/i/status/2078106556634124335`:

- длительность `2376.934` секунды;
- варианты до `1920x1080`;
- готовый progressive MP4 `1280x720` — примерно 115 MiB;
- готовый progressive MP4 `1920x1080` — примерно 333 MiB;
- cookies не нужны;
- production-selector проекта выбирает `1280x720`, но через HLS video + audio с
  последующим merge; готовый MP4 того же качества уже существует.

Контрольное portrait-видео `1080x1920`:

- текущий `height<=720` выбирает `320x568`;
- `-S res:720` выбирает `720x1280`;
- без лимита выбирается `1080x1920`.

## 3. Решения и границы

### 3.1. Что входит

1. Явное распознавание YouTube, X и остальных URL-профилей внутри media
   infrastructure.
2. Отдельные аргументы `yt-dlp` для X без YouTube credentials и JS-компонентов.
3. GraphQL как основной X backend, syndication как один ограниченный fallback.
4. Детерминированная обработка постов с несколькими видео.
5. Общая ориентационно-независимая настройка разрешения.
6. Точные unit/integration-тесты команд, fallback и API-контракта.
7. Документация конфигурации, публичных ограничений X и rollout.

### 3.2. Что не входит

- X API, платные downloader-сервисы и self-hosted Cobalt.
- Хранение X cookies или endpoint управления ими. Первая версия поддерживает
  публично доступные X-видео; protected/private/age-gated контент не обещается.
- Объединение нескольких вложенных видео в одну лекцию. При URL без `/video/N`
  берётся первое видео; пользователь может выбрать другое суффиксом `/video/N`.
- Изменение `POST /tasks`, таблицы `tasks`, `usage`, result layout или webhook.
- Полная защита generic URL ingest от SSRF. Текущий контракт также обещает обычные
  HTTP-video URL, а безопасное закрытие redirect/DNS-rebinding требует отдельного
  решения. В этой задаче нельзя незаметно удалить существующую generic-функцию.

### 3.3. Политика качества

Добавить `MediaConfig` и переменную:

```env
VIDEO_TARGET_RESOLUTION=720
```

Значение — строка: целое число от `144` до `4320` либо `best`.

- Дефолт `720` сохраняет текущий объём трафика и диска, но исправляет portrait.
- `1080` разрешает Full HD в обеих ориентациях.
- `best` снимает лимит и выбирает максимум источника.
- В `yt-dlp` числовое значение реализуется через мягкую сортировку
  `-S res:<value>,proto:https`, а не через фильтр `height<=...`.
- Это target, не строгий ceiling: если источник не предлагает ни одного формата на
  целевом уровне или ниже, выбирается наименьший формат выше цели. Такое поведение
  лучше полного отказа ingest и должно быть отражено в тестах и документации.
- Для `best` используется quality-first сортировка `-S res,proto:https`: сначала
  максимальное разрешение, затем предпочтение готового progressive HTTPS среди
  вариантов того же уровня.
- Настройка применяется ко всем URL-источникам, чтобы YouTube и X не имели разных
  трактовок `720p`.
- Загруженные пользователем файлы не перекодируются и этой настройке не подчиняются.

Дефолт намеренно остаётся `720`: контрольное 39-минутное видео занимает около
115 MiB в 720p и 333 MiB в 1080p, а затем участвует в извлечении кадров и нарезке.
Это настройка эксплуатационного профиля, а не скрытый hard cap: prod может перейти
на `1080` или `best` отдельным env-изменением после замера диска и времени обработки.

## 4. Проектирование реализации

### 4.1. Классификация URL

В `lecturelog/infrastructure/media/url_utils.py` добавить чистую классификацию
`VideoUrlKind` (`youtube`, `x`, `generic`) по нормализованному hostname.

X-hostnames:

- `x.com`, `www.x.com`, `mobile.x.com`, `m.x.com`;
- `twitter.com`, `www.twitter.com`, `mobile.twitter.com`, `m.twitter.com`.

YouTube-hostnames сохраняют существующие `youtube.com`-поддомены и `youtu.be`.
Сравнение должно работать без учёта регистра, игнорировать завершающую точку DNS и
не принимать suffix-ловушки вроде `x.com.evil.example`.

API продолжает проверять лишь корректность HTTP(S)-URL и не меняет wire-контракт.
Классификация нужна для выбора downloader profile, а не для запрета generic URL.

### 4.2. Конфигурация

В `lecturelog/config/settings.py`:

- добавить `MediaConfig` с `target_resolution: str`;
- валидировать `best` или числовой диапазон `144..4320`;
- включить `media` в eager validation `AppConfig.model_post_init`;
- передать `cfg.media.target_resolution` в `VideoIngestor` из lifespan.

Обновить `.env.example` и `deploy/env.core.example`. В compose новое поле явно
пробрасывать не требуется: оба deployment-варианта уже используют `env_file`.

### 4.3. Профили команд `yt-dlp`

Переименовать `_download_youtube` в нейтральный `_download_url` и выделить чистые
builders аргументов, чтобы unit-тесты проверяли контракт без сети.

Общие аргументы:

```text
--no-playlist
--playlist-items 1
--merge-output-format mp4
--no-progress
--print after_move:filepath
-S res:<N>,proto:https     # числовая настройка
-S res,proto:https         # настройка best
-o <isolated attempt dir>/video.%(ext)s
```

Фиксированное имя `video.mp4` не использовать: оно мешает доказать, что extractor
создал ровно один final output, и может скрыть второй playlist entry.

Профиль YouTube:

```text
-f bv*+ba/b
--js-runtimes deno
--remote-components ejs:github
--cookies <private temp file>   # только если YouTube CookieStore непуст
```

Профиль X:

```text
-f bv*+ba/b
--extractor-args twitter:api=graphql
```

Так качество и целевое разрешение остаются первым критерием. `proto:https` отдаёт
готовому progressive MP4 предпочтение лишь среди равноценных по разрешению
вариантов; если лучший вариант существует только как HLS/split streams, он не будет
потерян ради progressive. YouTube cookies, Deno и EJS в X-команду не попадают.

Профиль generic сохраняет текущую возможность `yt-dlp`, но получает новую
ориентационно-независимую сортировку. YouTube cookies generic-профилю не передаются.

### 4.4. X fallback

Выбирать X backend в отдельной metadata/extraction-only фазе до скачивания:

1. Выполнить GraphQL preflight через `yt-dlp --simulate` с тем же format selector,
   quality sort, `--no-playlist` и `--playlist-items 1`.
2. Если GraphQL extraction неуспешен, выполнить ровно один syndication preflight.
3. Выбранный успешным preflight backend использовать для единственного download
   subprocess.
4. Ошибка download/merge/write после успешного preflight не переключает backend и
   не начинает скачивание заново.
5. Если оба preflight упали, вернуть одну типизированную ошибку с санитизированной
   диагностикой обеих попыток.

Fallback запускается только для X. `legacy` не используется. Бесконечных ретраев
нет; транспортные ретраи внутри самого `yt-dlp` остаются его ответственностью.
`FileNotFoundError` при запуске бинаря обрабатывается до fallback. Поскольку fallback
заканчивается до download phase, `ENOSPC`, permission denied и ошибка ffmpeg merge
по определению не могут запустить вторую полную загрузку.

Preflight добавляет один metadata round-trip к X, зато делает failure domains
наблюдаемыми и не требует угадывать фазу по общему stderr одного subprocess.

### 4.5. Файл результата и multi-video

- Входной URL без `/video/N`: `--playlist-items 1` выбирает первое видео поста.
- URL с `/video/N`: extractor возвращает конкретное вложение; это поведение
  покрывается тестом.
- Download выполняется в новом isolated attempt directory с template
  `video.%(ext)s`; каталог принадлежит только этому subprocess.
- Итоговый путь брать из единственной строки `--print after_move:filepath`, а не из
  сортировки `glob()`.
- Напечатанный путь должен существовать, находиться внутри attempt directory и быть
  ровно один. Ноль или несколько final paths — явная ошибка.
- После проверки файл атомарно переносится в корень `output_dir` как
  `video.<actual_ext>`; насильно переименовывать WebM/MKV в `.mp4` нельзя.
- Directory scan остаётся только защитной сверкой с напечатанным final path, а не
  механизмом выбора `candidates[0]`.
- `.part`, `.ytdl` и файлы неуспешной попытки не должны попасть в дальнейший pipeline.

### 4.6. Ошибки и публичный контракт

Не расширять `ErrorCode` в этой задаче:

- YouTube bot-check остаётся `cookies_invalid`, но только если ошибка пришла из
  YouTube profile.
- X HTTP 429 / rate-limit → `rate_limit`.
- удалённый пост, пост без видео, protected/private/age-gated без доступа →
  `bad_input` с безопасным понятным текстом;
- отсутствие бинаря, диск, permission, merge и неизвестные ошибки → `internal`.

Добавить domain-level `MediaIngestError`, не зависящий от `yt-dlp`, с полями
`source_kind`, `reason`, `public_message` и `diagnostic`. `reason` как минимум
различает `not_found`, `auth_required`, `rate_limit`, `tool_missing`, `local_io`,
`extractor` и `invalid_output`. `classify_error` маппит только `reason`, а не raw
stderr. `str(error)` возвращает только `public_message`, потому что `PipelineService`
сохраняет строку исключения и traceback в `Task.error`.

Диагностика логируется отдельно после удаления полного source URL/query string,
cookie paths и потенциальных bearer/cookie значений и ограничивается по длине.
Нельзя классифицировать X-фразу `sign in` как требование обновить YouTube cookies.

Wire-формы `GET /tasks/{id}` и webhook не меняются. OpenAPI snapshot после работы
должен остаться без diff; если генератор всё же изменит его, изменение нужно
объяснить, а не коммитить автоматически.

### 4.7. Воспроизводимые зависимости

Зафиксировать проверенный runtime-профиль:

```toml
"yt-dlp[default]==2026.7.4"
```

Extra `default` включает `yt-dlp-ejs`, необходимый актуальному YouTube extractor.
Docker builder ставит project dependency один раз; отдельный
`pip install --upgrade yt-dlp` в runtime удалить, иначе локальные тесты, metadata
package и production image используют разные версии.

Deno также закрепить на проверенной версии `2.9.3`, передавая версию официальному
installer, вместо нефиксированного latest. Существование pin подтверждено официальным
релизом [`v2.9.3`](https://github.com/denoland/deno/releases/tag/v2.9.3),
опубликованным 2026-07-15. Версии yt-dlp/Deno обновляются отдельным dependency PR с
smoke matrix; срочное обновление extractor остаётся маленьким явным изменением, а не
скрытым результатом очередной Docker-сборки.

## 5. Карта изменений по файлам

| Файл | Изменение |
|---|---|
| `lecturelog/config/settings.py` | `MediaConfig`, валидация resolution, wiring в `AppConfig` |
| `lecturelog/api/lifespan.py` | передача media config в ingestor |
| `lecturelog/infrastructure/media/url_utils.py` | точная классификация YouTube/X/generic hostname |
| `lecturelog/infrastructure/media/video_ingestor.py` | source-aware profiles, quality sort, X fallback, multi-video invariant |
| `lecturelog/application/error_classifier.py` | source-aware media error mapping |
| `lecturelog/domain/exceptions.py` или media-local exceptions | типизированная ошибка downloader без изменения публичного enum |
| `.env.example` | документировать `VIDEO_TARGET_RESOLUTION` |
| `deploy/env.core.example` | production-конфиг resolution |
| `README.md` | X support, public-only scope, multi-video и quality config |
| `pyproject.toml` | закрепить `yt-dlp[default]==2026.7.4` |
| `Dockerfile` | единая project install, удалить floating upgrade, закрепить Deno `2.9.3` |
| `tests/unit/test_url_utils.py` | hostname matrix и suffix traps |
| `tests/unit/test_config.py` | default, numeric, `best`, invalid values |
| `tests/unit/test_video_ingestor*.py` | точные argv, cookies isolation, fallback, cleanup, multi-video |
| `tests/unit/test_error_classifier*.py` | X/YouTube/error-phase classification |
| `tests/integration/test_api_video_contract.py` | `x.com/i/status/...` проходит неизменный API |

## 6. Последовательность реализации

### Шаг 1. Конфигурация и чистые контракты

1. Добавить `MediaConfig` и его unit-тесты.
2. Добавить `VideoUrlKind` и hostname matrix.
3. Ввести типизированную внутреннюю media download error.
4. Подключить config к lifespan без изменения API.

Критерий: config и URL tests зелёные; текущие YouTube tests ещё сохраняют поведение.

### Шаг 2. Рефакторинг без X fallback

1. Переименовать нейтральные методы.
2. Выделить builders общих/YouTube/X/generic arguments.
3. Заменить `height<=720` на format sorting.
4. Ограничить чтение CookieStore только YouTube profile.
5. Сделать invariant одного итогового файла.

Критерий: YouTube unit-тесты зелёные; portrait unit-case ожидает `res:720`, а не
`height<=720`; API и pipeline tests без регрессий.

### Шаг 3. X и fallback

1. Добавить quality-first X selector с progressive tie-breaker.
2. Добавить GraphQL metadata preflight и syndication preflight fallback.
3. Скачать ровно один раз выбранным backend в isolated attempt directory.
4. Валидировать единственный `after_move:filepath` и безопасно перенести final.
5. Добавить deterministic first media и `/video/N` contracts.
6. Доработать structured error classification и sanitization.

Критерий: orchestration unit-тесты с замоканным subprocess доказывают порядок
preflight/download, отсутствие YouTube cookies/JS flags и отсутствие повторного
download при local failure. Отдельный hermetic format-matrix test проверяет реальное
поведение закреплённого `yt-dlp`, а не только состав argv.

### Шаг 4. Dependencies и документация

1. Закрепить `yt-dlp[default]==2026.7.4`, удалить runtime floating upgrade и
   закрепить Deno `2.9.3`.
2. Обновить env examples и README.
3. Проверить отсутствие незапланированного OpenAPI diff.

### Шаг 5. Валидация

Запустить из worktree:

```bash
ruff check .
ruff format --check .
pytest -q
python scripts/export_openapi.py
git diff --exit-code -- docs/openapi.json
git diff --check
```

Добавить hermetic format matrix на synthetic/pinned info JSON с горизонтальными и
вертикальными formats, progressive/HLS на разных уровнях, отсутствием формата ниже
target и режимом `best`. Тест должен прогонять реальную логику format selection
закреплённого `yt-dlp` и доказывать:

- target `720` выбирает `1280x720` и `720x1280`;
- при отсутствии `<=720` выбирается наименьший формат выше target;
- более низкий progressive не побеждает более высокий HLS;
- при одинаковом разрешении предпочтителен progressive HTTPS;
- `best` выбирает максимальное разрешение.

Также добавить sentinel CookieStore, чей `get()` падает: X и generic ingest не должны
его вызывать. Fake subprocess должен уметь напечатать ноль, один и два
`after_move:filepath`, чтобы проверить output invariant и path containment.

Затем собрать runtime image и проверить точные версии: `yt-dlp 2026.07.04`,
`yt-dlp-ejs 0.8.0`, Deno `2.9.3`, а также наличие ffmpeg/ffprobe. Live X smoke не
включать в обычный CI: внешний пост может быть удалён, а X может временно rate-limit
CI IP.

Ручная acceptance matrix:

1. Публичный X single-video URL без cookies, `VIDEO_TARGET_RESOLUTION=720`.
2. Тот же URL с `1080` и проверкой `ffprobe`.
3. Portrait X URL: выбран `720x1280`, не `320x568`.
4. Multi-video URL без индекса: скачано первое и ровно одно вложение.
5. Multi-video URL `/video/2`: скачано второе вложение.
6. Искусственный GraphQL preflight failure: syndication preflight выполняется один
   раз, после чего идёт ровно один download.
7. Удалённый/закрытый X post: задача завершается `bad_input`, не
   `cookies_invalid` и не зависает в `video_ingest`.
8. Обычный YouTube URL с текущими cookies: поведение не регрессировало.
9. YouTube bot-check: по-прежнему `cookies_invalid`.
10. Generic HTTP video URL: существующая совместимость сохранена.

Для длинного контрольного X-видео достаточно проверить ingest и `ffprobe`; полный
LLM pipeline запускать один раз на dev-стенде, чтобы не дублировать платные вызовы.

## 7. Rollout и откат

1. Реализация идёт в отдельной feature-ветке от актуального `dev`.
2. После локальных тестов — отдельное code review с акцентом на subprocess,
   credentials isolation, error mapping и multi-video.
3. Push ветки и draft PR в `dev`.
4. После зелёного CI собрать `:dev`, подтвердить digest и обновить только сервис
   `api` штатным compose flow.
5. На dev-стенде оставить `VIDEO_TARGET_RESOLUTION=720`; выполнить acceptance matrix.
6. Отдельно измерить scratch peak, размер result и время frames/cut на 1080p.
7. Поднять prod до `1080` или `best` только явным решением после замера.

Откат не требует миграций:

- вернуть предыдущий image digest/tag;
- либо временно оставить новый image с `VIDEO_TARGET_RESOLUTION=720` — это снижает
  ресурсный профиль, но не отключает X;
- незавершённые задачи после рестарта будут обработаны существующим механизмом
  `interrupted`.

## 8. Риски и защитные меры

| Риск | Мера |
|---|---|
| X меняет GraphQL | metadata-only syndication fallback, явный yt-dlp bump, smoke перед deploy |
| X требует login | v1 обещает только public media; X cookies не смешиваются с YouTube |
| Portrait снова деградирует | отдельный argv test и live `ffprobe` acceptance |
| 1080p увеличивает disk/CPU | дефолт 720, env opt-in, замер перед повышением |
| Multi-video создаёт несколько файлов | `--playlist-items 1` + invariant одного candidate |
| Fallback скрывает disk/permission error | fallback завершается до download; structured reason mapping |
| GraphQL extraction нестабилен | metadata-only syndication fallback до единственного download |
| Output подменён/множественный | isolated dir + единственный санитизированный after-move path |
| Dependency устаревает | точные версии + явный dependency bump PR + image smoke |
| Generic URL сохраняет SSRF-риск | не расширять обещание; завести отдельный security design для redirect/DNS controls |

## 9. Definition of Done

- X single-video URL проходит существующий `POST /tasks` без нового API-поля.
- Для публичного X URL cookies не читаются и не передаются.
- GraphQL работает основным путём, syndication вызывается не более одного раза.
- Multi-video детерминирован: первое вложение либо явный `/video/N`.
- `VIDEO_TARGET_RESOLUTION=720` выбирает `1280x720` и `720x1280`, когда эти уровни
  доступны, и документированно выбирает наименьший larger fallback иначе.
- `VIDEO_TARGET_RESOLUTION=best` выбирает максимальное доступное разрешение X.
- YouTube cookies и `cookies_invalid` не регрессировали.
- Полный test/lint/OpenAPI gate зелёный.
- README и оба env example описывают новую поддержку и её ограничения.
- Изменения не требуют миграции БД и имеют проверенный image rollback.
