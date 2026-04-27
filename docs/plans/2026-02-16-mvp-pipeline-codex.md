# LectureLog MVP Pipeline — Инструкции для Codex

> Полный план с контекстом: `docs/plans/2026-02-16-mvp-pipeline.md`
> Существующий код-reference: `scripts/structurize.py`, `scripts/summary_quality_eval.py`

## Что строим

Python-микросервис: аудио + слайды (PDF/PPTX) → конспект.md + аудио-фрагменты + слайды-картинки → ZIP.
FastAPI API, Telegram-бот и CLI — тонкие клиенты. Всё в Docker.

## Структура

```
LectureLog/
├── lecturelog/
│   ├── __init__.py
│   ├── config.py               # Pydantic BaseSettings
│   ├── models.py               # PipelineStage(enum), PipelineStatus, Section
│   ├── srt.py                  # parse_srt_time, format_time, extract_plain_text, extract_srt_fragment
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── runner.py           # PipelineRunner: оркестратор этапов с прогрессом
│   │   ├── transcribe.py       # Groq Whisper STT (async, ffmpeg chunking)
│   │   ├── slides.py           # PDF→PNG (PyMuPDF), PPTX→PDF→PNG (LibreOffice)
│   │   ├── structurize.py      # Gemini: 3 этапа (split → slide_match → sections)
│   │   ├── audio_cut.py        # ffmpeg: нарезка по таймкодам
│   │   └── export.py           # Сборка конспект.md + медиа → ZIP
│   └── llm/
│       ├── __init__.py
│       ├── key_pool.py         # Async KeyPool (round-robin, rate limit)
│       └── gemini.py           # call_gemini(pool, prompt, images=None)
├── prompts/
│   ├── split_v1.md
│   ├── section_v1.md           # Включает блок коррекции ошибок Whisper
│   └── slide_match_v1.md
├── server/
│   ├── __init__.py
│   ├── app.py                  # FastAPI init, lifespan
│   ├── routes.py               # POST/GET /api/v1/tasks, GET /health
│   └── task_manager.py         # In-memory dict задач
├── bot/
│   ├── __init__.py
│   ├── main.py
│   └── handlers.py             # aiogram 3.x FSM
├── cli/
│   └── main.py                 # httpx клиент
├── tests/
│   ├── test_srt.py
│   ├── test_key_pool.py
│   ├── test_slides.py
│   ├── test_export.py
│   └── test_api.py
├── pyproject.toml
├── Dockerfile                  # python:3.12-slim + ffmpeg + libreoffice-impress
├── Dockerfile.bot              # python:3.12-slim (только HTTP-клиент)
├── docker-compose.yml
└── .env.example
```

## Зависимости (pyproject.toml)

`google-genai`, `httpx`, `fastapi`, `uvicorn[standard]`, `python-multipart`, `pymupdf`, `Pillow`, `pydantic>=2.0`, `aiogram>=3.0`

## Задачи (выполнять последовательно)

### 1. config.py + models.py

`lecturelog/config.py` — Pydantic `BaseSettings`:
- `GROQ_API_KEY: str`, `GEMINI_API_KEYS: str` (через запятую), `GEMINI_MODEL: str = "gemini-2.5-pro"`
- `UPLOAD_DIR: str = "/tmp/lecturelog"`, `MAX_WORKERS: int = 5`
- `TELEGRAM_BOT_TOKEN: str = ""`, `API_BASE_URL: str = "http://localhost:8000"`

`lecturelog/models.py`:
- `PipelineStage` — StrEnum: `TRANSCRIBE`, `SLIDES`, `STRUCTURIZE`, `AUDIO_CUT`, `EXPORT`
- `PipelineStatus` — `task_id: str`, `stage: PipelineStage | None`, `progress_pct: int`, `error: str | None`, `result_path: str | None`
- `Section` — `title: str`, `start: str`, `end: str`, `content: str`, `slide_indices: list[int]`

### 2. srt.py

Перенести из `scripts/structurize.py` (строки 111-169):
- `extract_plain_text(srt: str) -> str`
- `parse_srt_time(time_str: str) -> float`
- `format_time(time_str: str) -> str`
- `extract_srt_fragment(srt: str, start: str, end: str) -> str`

Написать `tests/test_srt.py` — минимум 3 теста.

### 3. llm/key_pool.py + llm/gemini.py

**key_pool.py** — адаптация `KeyPool` из `scripts/structurize.py` (строки 176-234) на async:
- `asyncio.Lock` вместо `threading.Lock`, `asyncio.sleep` вместо `time.sleep`
- `async acquire() -> (client, idx)`, `mark_rate_limited(idx)`, `alive_count()`
- RPM параметризован через конструктор

**gemini.py**:
```python
async def call_gemini(pool: KeyPool, prompt: str, images: list[bytes] | None = None, retries: int = 5) -> str
```
- Текст: `client.models.generate_content(model=model, contents=prompt)`
- Multimodal: `contents=[*[types.Part.from_bytes(data=img, mime_type="image/png") for img in images], prompt]`
- Retry: 429 → `pool.mark_rate_limited(idx)`, 503 → `asyncio.sleep(10 * attempt)`

Написать `tests/test_key_pool.py` с mock.

### 4. pipeline/transcribe.py

```python
async def transcribe(audio_path: Path, output_dir: Path, groq_api_key: str, on_progress: Callable) -> Path
```
- ffmpeg нарезка: `ffmpeg -i {audio} -f segment -segment_time 1200 -c:a libmp3lame -b:a 128k {output_dir}/chunk_%03d.mp3`
- Параллельная отправка в Groq через `asyncio.Semaphore(6)`, httpx POST to `https://api.groq.com/openai/v1/audio/transcriptions`
- Формат: `model=whisper-large-v3`, `response_format=verbose_json`, `timestamp_granularities[]=word`
- Сборка SRT: offset = chunk_index × 1200, группировка по 7 слов
- ffmpeg через `asyncio.create_subprocess_exec()`
- Возвращает путь к SRT

### 5. pipeline/slides.py

```python
async def convert_slides(path: Path, output_dir: Path, on_progress: Callable) -> list[Path]
```
- PDF: `pymupdf.open(path)`, `page.get_pixmap(dpi=200)`, сохранить как PNG
- PPTX: `soffice --headless --convert-to pdf --outdir {tmpdir} {path}`, потом PDF→PNG
- Возвращает `list[Path]` к PNG в порядке страниц

### 6. prompts/ + pipeline/structurize.py

Создать 3 файла промптов (загружаются из файлов при старте, НЕ хардкод):

**prompts/split_v1.md** — взять SPLIT_PROMPT из `scripts/structurize.py` (строки 30-63).

**prompts/section_v1.md** — взять SECTION_PROMPT из `scripts/structurize.py` (строки 66-108), добавить в конец:

```
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

**prompts/slide_match_v1.md**:
```
Тебе даны разделы лекции (JSON) и слайды презентации (изображения).
Для каждого раздела определи, какие слайды к нему относятся по содержанию.

Формат ответа — строго JSON:
{"0": [1, 2], "1": [3], "2": [], "3": [4, 5]}

Ключ — индекс раздела (с 0), значение — список номеров слайдов (с 1).
Слайд может относиться к нескольким разделам. Если к разделу нет слайдов — пустой массив.
Верни ТОЛЬКО JSON.
```

**pipeline/structurize.py**:
```python
async def structurize(srt_path: Path, slide_images: list[Path], output_dir: Path, pool: KeyPool, model: str, on_progress: Callable) -> list[Section]
```
Три этапа:
1. Загрузить промпт `split_v1.md`, добавить SRT → `call_gemini()` → JSON разделов
2. Если есть слайды: загрузить `slide_match_v1.md`, отправить JSON разделов + все PNG как bytes → `call_gemini(multimodal)` → JSON привязки
3. Параллельно для каждого раздела: загрузить `section_v1.md`, подставить title/start/end, добавить SRT-фрагмент + релевантные слайды → `call_gemini(multimodal)` → текст раздела

### 7. pipeline/audio_cut.py + pipeline/export.py

**audio_cut.py**:
```python
async def cut_audio(audio_path: Path, sections: list[Section], output_dir: Path) -> list[Path]
```
- `ffmpeg -i {audio} -ss {start} -to {end} -c copy {output_dir}/section_{i:02d}.mp3`

**export.py**:
```python
async def export_result(sections: list[Section], audio_fragments: list[Path], slide_images: list[Path], output_dir: Path) -> Path
```
Выход:
```
output/
├── конспект.md
├── audio/
│   ├── 01-название.mp3
│   └── ...
└── slides/
    ├── slide-01.png
    └── ...
```
Markdown формат раздела:
```markdown
## Название раздела
[00:00:00 - 00:05:30] | [Аудио](audio/01-название.mp3)

![Слайд 3](slides/slide-03.png)

Текст раздела...
```
Упаковать в ZIP, вернуть путь к ZIP.

### 8. pipeline/runner.py

```python
class PipelineRunner:
    def __init__(self, config: Settings, pool: KeyPool): ...
    async def run(self, task_id: str, audio_path: Path, slides_path: Path | None) -> Path: ...
```
- Последовательно: TRANSCRIBE → SLIDES → STRUCTURIZE → AUDIO_CUT → EXPORT
- `self.statuses: dict[str, PipelineStatus]` — in-memory
- Каждый этап обновляет `statuses[task_id]` через callback
- При ошибке: записать stage + error message + traceback в статус
- Возвращает путь к ZIP

### 9. server/ (FastAPI)

**app.py**: FastAPI app, lifespan (создать KeyPool при старте из config)

**routes.py**:
- `POST /api/v1/tasks` — multipart `audio: UploadFile`, `slides: UploadFile | None` → `{"task_id": "..."}`
- `GET /api/v1/tasks/{task_id}` → `PipelineStatus` как JSON
- `GET /api/v1/tasks/{task_id}/result` → `FileResponse(zip_path)`, 404 если не готово
- `GET /api/v1/health` → `{"status": "ok"}`

**task_manager.py**: сохранить файлы в `UPLOAD_DIR/{task_id}/`, запустить `runner.run()` через `asyncio.create_task()`

### 10. cli/main.py

```
python -m cli.main process --audio lecture.mp3 [--slides slides.pdf] [--api-url http://localhost:8000] [--output ./result]
```
- POST файлы → получить task_id
- Поллить GET /tasks/{id} каждые 3 сек, печатать прогресс
- Скачать ZIP, распаковать в --output

### 11. bot/ (aiogram 3.x)

FSM состояния: `WAIT_AUDIO` → `WAIT_SLIDES` → `PROCESSING`
1. Юзер шлёт аудио → бот: "Прикрепите слайды или /skip"
2. Юзер шлёт PDF/PPTX или /skip
3. Бот POST файлы на API, поллит статус каждые 5 сек
4. Обновления: "⏳ Транскрибация... 40%", "⏳ Структурирование... 70%"
5. Готово → отправить ZIP файлом

### 12. Docker

**Dockerfile**:
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libreoffice-impress && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY . .
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Dockerfile.bot**:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY . .
CMD ["python", "-m", "bot.main"]
```

**docker-compose.yml**:
```yaml
services:
  api:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    volumes: ["./data:/app/data"]
  bot:
    build: { context: ., dockerfile: Dockerfile.bot }
    env_file: .env
    depends_on: [api]
```

## Важные детали

- Весь пайплайн async (asyncio). ffmpeg и LibreOffice через `asyncio.create_subprocess_exec()`
- Промпты читать из файлов `prompts/*.md`, НЕ хардкодить в Python
- Комментарии в коде на русском
- Тесты запускать: `python -m pytest tests/`
- Существующие скрипты в `scripts/` не трогать
