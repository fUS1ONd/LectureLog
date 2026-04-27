# LectureLog

MVP-пайплайн для обработки лекций: аудио + PDF/PPTX слайды → структурированный конспект + аудио-фрагменты + изображения слайдов в ZIP.

## Компоненты

- `lecturelog/` — ядро пайплайна (транскрибация, обработка слайдов, структурирование, экспорт)
- `server/` — FastAPI API
- `cli/` — CLI-клиент для запуска через API
- `bot/` — Telegram-бот (aiogram 3.x)
- `prompts/` — версии промптов для Gemini

## Быстрый старт

1. Установите зависимости:

```bash
python3 -m pip install -e .
```

2. Создайте `.env` на основе `.env.example`.

3. Запустите API:

```bash
uvicorn server.app:app --reload --host 0.0.0.0 --port 8000
```

4. Обработайте лекцию через CLI:

```bash
python3 -m cli.main process --audio lecture.mp3 --slides slides.pdf --api-url http://localhost:8000 --output ./result
```

## API

- `POST /api/v1/tasks` — создать задачу (`audio`, `slides?`)
- `GET /api/v1/tasks/{task_id}` — статус пайплайна
- `GET /api/v1/tasks/{task_id}/result` — скачать ZIP
- `GET /api/v1/health` — healthcheck

## Тесты

```bash
python3 -m pytest tests/
```

## Docker

```bash
docker compose up --build
```
