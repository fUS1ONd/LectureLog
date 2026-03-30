# Быстрый запуск LectureLog с реальными данными

## 1. Подготовка

```bash
cd /home/krivonosov/projects/LectureLog
git checkout feature/lecturelog-http-service
```

## 2. Проверить .env

Файл `.env` в корне проекта должен содержать:

```
GROQ_API_KEY=gsk_...
GEMINI_API_KEYS=AIza...,AIza...,AIza...
GEMINI_MODEL=gemini-2.5-pro
UPLOAD_DIR=/app/data
MAX_WORKERS=5
API_BASE_URL=http://localhost:8000
```

## 3. Создать папку для данных

```bash
mkdir -p data
```

## 4. Собрать и запустить API-сервер

```bash
docker compose build api
docker compose up api
```

Дождаться строки `Uvicorn running on http://0.0.0.0:8000`.

## 5. Проверить healthcheck

В отдельном терминале:

```bash
curl http://localhost:8000/api/v1/health
# Ожидаемый ответ: {"status":"ok"}
```

## 6. Отправить аудио на обработку

### Только аудио (без слайдов):

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -F "audio=@/mnt/c/Users/koskr/database/Разработка ПО/12.02/Разработка ПО 260212_133933.m4a" \
  | tee /dev/stderr | jq .
```

Ответ: `{"task_id": "xxxx-xxxx-xxxx"}`

### С аудио + слайды (PDF):

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -F "audio=@путь/к/лекция.m4a" \
  -F "slides=@путь/к/слайды.pdf" \
  | tee /dev/stderr | jq .
```

## 7. Следить за прогрессом

```bash
# Подставить task_id из шага 6
TASK_ID="xxxx-xxxx-xxxx"

# Один раз:
curl http://localhost:8000/api/v1/tasks/$TASK_ID | jq .

# Автоматический поллинг каждые 5 секунд:
while true; do
  STATUS=$(curl -s http://localhost:8000/api/v1/tasks/$TASK_ID)
  echo "$(date +%H:%M:%S) $STATUS" | jq .

  # Проверяем завершение
  ERROR=$(echo $STATUS | jq -r '.error // empty')
  RESULT=$(echo $STATUS | jq -r '.result_path // empty')

  if [ -n "$ERROR" ]; then
    echo "ОШИБКА: $ERROR"
    break
  fi

  if [ -n "$RESULT" ]; then
    echo "ГОТОВО!"
    break
  fi

  sleep 5
done
```

## 8. Скачать результат

```bash
curl -o result.zip http://localhost:8000/api/v1/tasks/$TASK_ID/result
unzip result.zip -d result/
ls result/
```

Ожидаемая структура:

```
result/
├── конспект.md
├── audio/
│   ├── 01-введение.mp3
│   └── ...
└── slides/
    └── ...
```

## 9. Открыть в Obsidian

Открыть папку `result/` как vault в Obsidian. Файл `конспект.md` содержит ссылки на аудио и слайды.

## 10. Остановить

```bash
docker compose down
```

## Альтернатива: CLI-клиент

Если API уже запущен:

```bash
# Из venv проекта
pip install -e .
python -m cli.main process \
  --audio "/mnt/c/Users/koskr/database/Разработка ПО/12.02/Разработка ПО 260212_133933.m4a" \
  --output ./result
```

## Устранение проблем

| Симптом | Причина | Решение |
|---------|---------|---------|
| `{"status":"ok"}` не возвращается | Контейнер не поднялся | `docker compose logs api` |
| `stage: "transcribe"`, прогресс не двигается | Groq API недоступен или ключ невалидный | Проверить GROQ_API_KEY в .env |
| `error: "429..."` или `"RESOURCE_EXHAUSTED"` | Лимит API | Подождать ~60 сек, повторить |
| `error: "soffice..."` | LibreOffice не установлен (PPTX) | Работает только в Docker |
| Пустой ZIP | Gemini не ответил | Проверить GEMINI_API_KEYS |
