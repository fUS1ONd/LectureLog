# LectureLog MVP Implementation (2026-02-16)

Реализован базовый end-to-end пайплайн:

1. `transcribe` — async-транскрибация через Groq Whisper с нарезкой ffmpeg и сборкой SRT.
2. `slides` — конвертация PDF/PPTX в PNG (PyMuPDF + LibreOffice).
3. `structurize` — трёхэтапная обработка Gemini с промптами из `prompts/*.md`.
4. `audio_cut` — нарезка исходного аудио по разделам.
5. `export` — генерация `конспект.md`, копирование медиа и упаковка в ZIP.

Дополнительно:

- FastAPI API (`/api/v1/tasks`, `/api/v1/tasks/{id}`, `/api/v1/tasks/{id}/result`, `/api/v1/health`).
- CLI-клиент (`python -m cli.main process ...`).
- Telegram-бот (aiogram 3.x) со сценариями `WAIT_AUDIO -> WAIT_SLIDES -> PROCESSING`.
- Docker-окружение (`Dockerfile`, `Dockerfile.bot`, `docker-compose.yml`).
- Покрытие тестами для ключевых модулей: `srt`, `key_pool`, `slides`, `export`, `api`.
