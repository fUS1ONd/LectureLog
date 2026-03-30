# LectureLog MVP Pipeline — План реализации

## Контекст

Проект LectureLog переходит от набора скриптов (`scripts/structurize.py`, `transcribe_long.sh`) к модульному Python-микросервису. Цель MVP — полный пайплайн: аудио + слайды → конспект с аудио-фрагментами и привязанными слайдами. Без веб-интерфейса — просмотр результата в Obsidian или аналогах.

Ключевые требования:
- **Микросервис с HTTP API** — FastAPI, принимает файлы, отдаёт статус и результат
- **Прогресс по этапам** — видно где пайплайн находится, что упало и почему
- **Умная коррекция ошибок Whisper** — Gemini исправляет артефакты транскрипции по контексту
- **Мультимодальные слайды** — Gemini получает слайды как изображения и привязывает к разделам
- **Docker** — `docker compose up` и работает (ffmpeg, LibreOffice внутри)
- **Telegram-бот** — опциональный тонкий клиент поверх API
- **CLI** — локальное использование через командную строку

## Структура проекта

```
LectureLog/
├── lecturelog/                  # Python-пакет (ядро)
│   ├── __init__.py
│   ├── config.py                # Pydantic Settings, загрузка .env
│   ├── models.py                # Pydantic-модели (Task, Section, PipelineStage)
│   ├── srt.py                   # Утилиты SRT (парсинг, нарезка, извлечение)
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── runner.py            # PipelineRunner — оркестратор этапов
│   │   ├── transcribe.py        # Groq Whisper STT
│   │   ├── slides.py            # PDF/PPTX → PNG (PyMuPDF + LibreOffice)
│   │   ├── structurize.py       # Gemini: SRT + слайды → конспект
│   │   ├── audio_cut.py         # ffmpeg: нарезка аудио по разделам
│   │   └── export.py            # Сборка конспект.md + медиа → ZIP
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── key_pool.py          # KeyPool (async, из scripts/structurize.py)
│   │   └── gemini.py            # Обёртка Gemini API (текст + multimodal)
│   └── quality/
│       ├── __init__.py
│       └── eval.py              # Метрики fidelity (из summary_quality_eval.py)
├── prompts/                     # Версионируемые промпты (md-файлы)
│   ├── split_v1.md              # Разбивка на разделы
│   ├── section_v1.md            # Оформление раздела + коррекция ошибок
│   └── slide_match_v1.md        # Привязка слайдов к разделам
├── server/                      # HTTP API (FastAPI)
│   ├── __init__.py
│   ├── app.py                   # Инициализация приложения
│   ├── routes.py                # POST /tasks, GET /tasks/{id}, GET /tasks/{id}/result
│   └── task_manager.py          # In-memory хранилище задач + запуск пайплайна
├── bot/                         # Telegram-бот (aiogram 3.x)
│   ├── __init__.py
│   ├── main.py
│   └── handlers.py
├── cli/                         # CLI-клиент
│   └── main.py
├── tests/
│   ├── test_srt.py
│   ├── test_key_pool.py
│   ├── test_slides.py
│   ├── test_export.py
│   └── test_api.py
├── scripts/                     # Старые скрипты (reference)
├── Dockerfile
├── Dockerfile.bot
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── docs/plans/
```

---

## Задачи

### Задача 1. Инициализация проекта

**Файлы:** `pyproject.toml`, `.env.example`, `lecturelog/__init__.py`, `lecturelog/config.py`

- `pyproject.toml` с зависимостями: `google-genai`, `httpx`, `fastapi`, `uvicorn[standard]`, `python-multipart`, `pymupdf`, `Pillow`, `pydantic>=2.0`, `aiogram>=3.0`
- `lecturelog/config.py` — Pydantic `BaseSettings`:
  - `GROQ_API_KEY`, `GEMINI_API_KEYS` (через запятую), `GEMINI_MODEL` (default `gemini-2.5-pro`)
  - `UPLOAD_DIR` (default `/tmp/lecturelog`), `MAX_WORKERS` (default 5)
  - `TELEGRAM_BOT_TOKEN` (опционально), `API_BASE_URL` (default `http://localhost:8000`)
- `.env.example` с комментариями

**Проверка:** `python -c "from lecturelog.config import Settings"` без ошибок

---

### Задача 2. Модели данных и SRT-утилиты

**Файлы:** `lecturelog/models.py`, `lecturelog/srt.py`, `tests/test_srt.py`

- Pydantic-модели:
  - `PipelineStage` — enum: `TRANSCRIBE`, `SLIDES`, `STRUCTURIZE`, `AUDIO_CUT`, `EXPORT`
  - `PipelineStatus` — `task_id`, `stage`, `progress_pct`, `error`, `result_path`
  - `Section` — `title`, `start`, `end`, `content`, `slide_indices: list[int]`
- SRT-утилиты (перенос из `scripts/structurize.py`):
  - `parse_srt_time()`, `format_time()`, `extract_plain_text()`, `extract_srt_fragment()`
- Тесты для SRT-функций

**Проверка:** `pytest tests/test_srt.py`

---

### Задача 3. KeyPool и Gemini-обёртка (async)

**Файлы:** `lecturelog/llm/key_pool.py`, `lecturelog/llm/gemini.py`, `tests/test_key_pool.py`

- `KeyPool` — адаптация из `scripts/structurize.py` на async (`asyncio.Lock`, `asyncio.sleep`)
  - `acquire() -> (client, idx)`, `mark_rate_limited(idx)`, параметризованный RPM
- `gemini.py`:
  - `call_gemini(pool, prompt, images=None, retries=5)` — текст или multimodal
  - Для изображений: `types.Part.from_bytes(data=png_bytes, mime_type="image/png")`
  - Retry: 429 → mark_rate_limited + retry, 503 → exponential backoff
- Тесты KeyPool с mock (без реальных API-вызовов)

**Проверка:** `pytest tests/test_key_pool.py`

---

### Задача 4. Транскрибация (Groq Whisper)

**Файлы:** `lecturelog/pipeline/transcribe.py`, `tests/test_transcribe.py`

- `async def transcribe(audio_path, output_dir, on_progress) -> Path`:
  - Нарезка через ffmpeg: `ffmpeg -i input -f segment -segment_time 1200 -c:a libmp3lame -b:a 128k chunk_%03d.mp3`
  - Параллельная отправка в Groq (`asyncio.Semaphore(6)`)
  - Модель `whisper-large-v3`, `response_format="verbose_json"`, `timestamp_granularities=["word"]`
  - Сборка SRT с offset = chunk_index × 1200, группировка по 7 слов
  - `on_progress(stage, pct)` — callback для отслеживания
- ffmpeg через `asyncio.create_subprocess_exec()`

**Проверка:** Тест с mock Groq — корректные offset-ы и SRT-формат

---

### Задача 5. Конвертация слайдов

**Файлы:** `lecturelog/pipeline/slides.py`, `tests/test_slides.py`

- `async def convert_slides(path, output_dir, on_progress) -> list[Path]`:
  - PDF → PNG: `pymupdf.open()`, `page.get_pixmap(dpi=200)`, сохранение PNG
  - PPTX → PDF → PNG: LibreOffice headless (`soffice --headless --convert-to pdf`), затем PyMuPDF
  - Возврат: список путей к PNG в порядке страниц
- Обработка ошибок: битый файл, LibreOffice не найден, пустой PDF

**Проверка:** Тест с синтетическим PDF (создание через pymupdf)

---

### Задача 6. Структурирование + коррекция ошибок + привязка слайдов

**Файлы:** `lecturelog/pipeline/structurize.py`, `prompts/split_v1.md`, `prompts/section_v1.md`, `prompts/slide_match_v1.md`

Три этапа:

**Этап 1 — Разбивка на разделы** (один запрос):
- Промпт `split_v1.md` + весь SRT → JSON `[{title, start, end}, ...]`

**Этап 2 — Привязка слайдов** (один мультимодальный запрос):
- Gemini получает: список разделов + все слайды как изображения
- Промпт `slide_match_v1.md` → JSON `{section_index: [slide_indices]}`

**Этап 3 — Параллельное оформление разделов** (N запросов):
- Каждый раздел: SRT-фрагмент + релевантные слайды + промпт `section_v1.md`
- Промпт включает блок **коррекции ошибок транскрипции**:

```markdown
## Исправление ошибок распознавания речи

Транскрипт получен из Whisper STT и содержит ошибки. Ты ДОЛЖЕН исправлять
очевидные артефакты распознавания, опираясь на контекст лекции:

### Типы ошибок:

1. **Слова не по контексту** — Whisper подставляет фонетически похожее,
   но бессмысленное слово. Пример: "программного изучения" → "программного
   обеспечения", "право на испечение" → "программное обеспечение".
   Восстанови правильное слово по смыслу предложения.

2. **Искажённые имена и термины** — фамилии учёных, названия технологий.
   Пример: "Диэкстра" → "Дейкстра", "на Ассендере" → "на ассемблере",
   "Sway Engineering Binary Knowledge" → "SWEBOK".

3. **Бессмысленные фразы** — несколько слов подряд не складываются в смысл.
   Попробуй восстановить по контексту. Если невозможно — [неясный фрагмент].

4. **Неправильные грамматические формы** — падежи, согласования.
   Исправляй на грамматически верную форму.

### Правила:
- Исправляй ТОЛЬКО очевидные ошибки распознавания
- Если сомневаешься — [возможная ошибка распознавания: ...]
- НЕ меняй авторские обороты и сленг лектора
- При наличии слайдов — сверяй термины с текстом на слайдах
```

Промпты загружаются из md-файлов, не хардкод.

**Проверка:** Тест с mock Gemini — JSON парсинг, привязка слайдов, структура Section

---

### Задача 7. Нарезка аудио и экспорт

**Файлы:** `lecturelog/pipeline/audio_cut.py`, `lecturelog/pipeline/export.py`, `tests/test_export.py`

- `cut_audio(audio_path, sections, output_dir)`:
  - `ffmpeg -i input -ss {start} -to {end} -c copy section_{i}.mp3`
- `export_result(sections, audio_fragments, slide_images, output_dir) -> Path`:
  - Структура выхода:
    ```
    output/
    ├── конспект.md
    ├── audio/
    │   ├── 01-введение.mp3
    │   └── 02-scrum.mp3
    └── slides/
        ├── slide-01.png
        └── slide-05.png
    ```
  - Markdown с Obsidian-совместимыми ссылками
  - Упаковка в ZIP

**Проверка:** Тест — markdown содержит правильные ссылки, ZIP содержит все файлы

---

### Задача 8. Оркестратор пайплайна

**Файлы:** `lecturelog/pipeline/runner.py`

- `PipelineRunner` — последовательный запуск: TRANSCRIBE → SLIDES → STRUCTURIZE → AUDIO_CUT → EXPORT
- In-memory `dict[str, PipelineStatus]`
- При ошибке: записывает какой этап упал, сообщение, traceback
- Каждый этап обновляет прогресс через callback

**Проверка:** Интеграционный тест с mock этапов

---

### Задача 9. HTTP API (FastAPI)

**Файлы:** `server/app.py`, `server/routes.py`, `server/task_manager.py`

- `POST /api/v1/tasks` — multipart: `audio` + `slides` (опционально) → `{task_id}`
- `GET /api/v1/tasks/{task_id}` — `{task_id, stage, progress_pct, error}`
- `GET /api/v1/tasks/{task_id}/result` — ZIP FileResponse
- `GET /api/v1/health` — healthcheck

**Проверка:** curl-запросы к API

---

### Задача 10. CLI-клиент

**Файлы:** `cli/main.py`

- HTTP-клиент (httpx): `python -m cli.main process --audio lecture.mp3 --slides slides.pdf`
- Поллинг статуса, прогресс в терминале, скачивание ZIP

---

### Задача 11. Telegram-бот

**Файлы:** `bot/main.py`, `bot/handlers.py`

- aiogram 3.x, FSM: аудио → слайды (или /skip) → запуск → обновления прогресса → ZIP
- Ограничение: файлы до 20MB на скачивание

---

### Задача 12. Docker

**Файлы:** `Dockerfile`, `Dockerfile.bot`, `docker-compose.yml`

- `Dockerfile`: `python:3.12-slim` + `ffmpeg` + `libreoffice-impress`
- `Dockerfile.bot`: `python:3.12-slim` (только HTTP-клиент)
- `docker-compose.yml`: сервисы `api` и `bot`

**Проверка:** `docker compose up --build`, healthcheck

---

### Задача 13. Метрики качества (опционально)

**Файлы:** `lecturelog/quality/eval.py`

- Перенос `compute_metrics()` из `scripts/summary_quality_eval.py`
- Опциональный шаг QA после структурирования

---

## Порядок реализации

```
1 (конфиг) → 2 (модели + SRT) → 3 (KeyPool + Gemini)
                                      ↓
                    ┌─────────────────┼─────────────────┐
                    ↓                 ↓                 ↓
              4 (транскрибация)  5 (слайды)     6 (структурирование)
                    └─────────────────┼─────────────────┘
                                      ↓
                              7 (нарезка + экспорт)
                                      ↓
                              8 (оркестратор)
                                      ↓
                              9 (HTTP API)
                                      ↓
                              ┌───────┼───────┐
                              ↓               ↓
                         10 (CLI)        11 (бот)

12 (Docker) — параллельно с 9-11
13 (метрики) — в любой момент после 2
```

## Верификация (end-to-end)

1. `docker compose up --build`
2. `curl -X POST localhost:8000/api/v1/tasks -F "audio=@lecture.mp3" -F "slides=@slides.pdf"` → `{task_id}`
3. `curl localhost:8000/api/v1/tasks/{id}` → прогресс по этапам
4. `curl -o result.zip localhost:8000/api/v1/tasks/{id}/result` → ZIP
5. Распаковать, открыть `конспект.md` в Obsidian — слайды отображаются, аудио играет
