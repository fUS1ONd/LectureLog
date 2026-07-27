# Матчер слайдов: эксперименты с моделями и фиксы каталога (26–27.07.2026)

Документ для восстановления контекста в новой сессии. Ветка
`docs/document-slide-alignment-v2-plan`, worktree
`/root/lecturelog-core/.worktrees/document-slide-alignment-v2-plan`, draft PR #12.

## Исходный вопрос

Проверить, зависит ли качество конспекта от модели Gemini. 25.07 квоты Gemini были
исчерпаны, и лекция 2026-02-12 обрабатывалась на `gemini-3.5-flash-lite`. 26.07 квоты
восстановились, и появилась возможность прогнать ту же лекцию на `gemini-3.6-flash`.

Требование к методике: вчерашние оценки делались сабагентами Codex, поэтому для
сравнения обе стороны переоцениваются сабагентами Claude с одинаковыми параметрами
(claude-opus-5, effort medium, скилл `skills/lecture-quality-judge/`).

## Результаты трёх прогонов лекции 2026-02-12

Все три оценены одним судьёй с одинаковыми настройками. Отчёты:

| | конфигурация | отчёт |
| --- | --- | --- |
| A | flash-lite (вынужденно), effort low, потолок 4096 | `benchmarks/lecture-quality/2026-07-26-judge-a-flash-lite.md` |
| B | 3.6-flash, effort low, потолок 4096 | `benchmarks/lecture-quality/2026-07-26-judge-b-flash36.md` |
| C | 3.6-flash + фиксы каталога, effort medium везде, потолок 65536 | `benchmarks/lecture-quality/2026-07-27-judge-c-fixes-medium.md` |

| Метрика | A | B | C |
| --- | ---: | ---: | ---: |
| Discussed recall | 95.2% | 95.2% | 90.5% |
| Acceptable topic accuracy | 95.2% | 85.0% | 84.2% |
| Wrong-topic rate | 4.8% | 15.0% | 15.8% |
| Best-context hit | 85.0% | 45.0% | 73.7% |
| High-confidence error rate | 5.0% | 15.0% | 15.8% |
| Collapsed-slide rate | 0% | 9.5% | 14.3% |
| Rendering correctness | 90.5% | 81.0% | 100% |
| Вердикт | `usable_with_minor_issues` | `usable_with_alignment_issues` | `usable_with_alignment_issues` |

Знаменатели различаются (21 / 20 / 19), поэтому сравнение только по общим
не-`unknown` величинам, как требует протокол сравнения в рубрике.

### Выводы

1. **Более сильная модель сама по себе результат не улучшила.** Прогон B хуже A почти
   по всем метрикам.
2. Причина оказалась не в «понимании», а в устойчивости структурированного вывода:
   3.6-flash многословнее, чаще упиралась в потолок ответа и срывала схему каталога,
   после чего каталог деградировал в native text.
3. **Фиксы каталога дали измеримый эффект**: rendering 81% → 100%, best-context
   45% → 73.7%.
4. **Лучший результат из трёх — прогон целиком на `low` effort.** Это согласуется с
   наблюдением, что reasoning мешает соблюдать схему, и является основанием для
   отдельного `LLM_EFFORT_SLIDE_MATCH=low` (коммит `50b9445`), который в прогоне C
   ещё не действовал.

## Что сделано в коде (коммиты поверх `37518b9`)

| Коммит | Суть |
| --- | --- |
| `e595b5f` | Три фикса каталога: `max_tokens` параметром вызова; однократный schema-repair с текстом ошибки вместо молчаливой деградации; фильтр колонтитулов колоды (`detect_boilerplate_lines`) из `title`, `source_concepts` и `visible_text` |
| `34a0cfe` | `LLM_MAX_TOKENS` (дефолт 65536) вместо зашитых 4096 для всех вызовов; отдельный лимит каталога удалён как избыточный |
| `50b9445` | `LLM_EFFORT_SLIDE_MATCH` (дефолт low) — матчер больше не наследует effort стадии SUBSPLIT |
| `544c5f4` | Strict `json_schema` вместо `json_object` для каталога и семантической верификации; `strict_json_schema()` выводит схему из Pydantic-моделей |

Ключевые файлы: `lecturelog/infrastructure/slides/alignment/{catalog,service,schemas}.py`,
`lecturelog/infrastructure/llm/llm_client.py`, `lecturelog/config/settings.py`,
`lecturelog/infrastructure/structurize/gemini_structurizer.py`.

Состояние: 559 тестов зелёные, ruff чист. Локально падает
`tests/unit/test_settings_llm.py::test_llm_config_effort_per_stage_defaults` — из-за
локального `.env` с `LLM_EFFORT_SPLIT=low`; без `.env` тест проходит, в CI не
воспроизводится.

## Следующие шаги

1. **Дождаться сброса квоты Gemini** (00:00 PDT = 07:00 UTC) и повторить прогон
   лекции 2026-02-12 на `gemini-3.6-flash` уже со strict-схемами и
   `LLM_EFFORT_SLIDE_MATCH=low`. Цель — проверить, исчезли ли срывы схемы.
2. Оценить результат тем же сабагентом-судьёй с теми же параметрами и добавить
   колонку D в таблицу выше.
3. **Заменить мёртвый BYOK-ключ.** Один из четырёх ключей отдаёт
   `401 The bound service account is deleted or disabled`; пока он в ротации, часть
   запросов уходит впустую. Именно из-за него 27.07 стала недоступна 3.6-flash.
   Фикс, чтобы задача при этом не падала, влит в `dev` отдельным PR #13 (`3c587c2`),
   но сам мёртвый ключ убирается только из панели OpenRouter.
4. Незакрытые дефекты матчера, подтверждённые независимо в нескольких прогонах:
   - слайд 21 (SWEBOK) — устойчивый ложный `unmentioned`;
   - слайд 13 («Команда») — либо неверная секция, либо ложный `unmentioned`;
   - навигационные и визуальные слайды: политика ролей из §6.8 плана не реализована;
   - `visual=0.000` во всех `reason_code` во всех прогонах — `video_evidence.py`
     не подключён к сервису (задача 11 плана).
5. Дефект faithfulness, найденный 27.07: конспект выдумал «Course Hub», хотя на
   слайде 2 в том же разделе написано `HwProj` и `hwproj.ru`. Причина архитектурная —
   рендер секций в v2 не получает изображения слайдов, поэтому не может чинить
   ASR-искажения имён собственных.
6. Диагностику стоит дополнить счётчиком срывов схемы каталога: сейчас деградация
   видна только в логах контейнера (`LLM slide catalog ... native fallback`), а не в
   `document-slide-alignment.json`.

## Долг по обработке упавших задач

Обнаружено 27.07 при разборе прод-инцидента с задачей
`935b93a26f39479e9a7240723cd945d2`. Оба пункта не относятся к матчеру, но входят в
план ближайших работ.

### TTL-sweeper для workspace упавших задач

Cleanup (`worker.py`) удаляет workspace только после подтверждённого
`results/<task_id>/` в MinIO, поэтому у `failed` задач сохраняется всё. Одна упавшая
задача оставила 494 МБ:

```
video_src/video.mp4        371M
extracted_audio/audio.mp3   62M
transcribe/ (чанки + SRT)   62M
```

Это осознанное решение при внедрении cleanup 23.07 — материалы не выбрасываются,
чтобы их можно было изучить. Но автоматической очистки по возрасту нет, и каждая
упавшая задача навсегда занимает полгигабайта. Диск прода на 27.07 — 90% (3.6 ГБ
свободно), при том что чистили его 23.07.

Нужен sweeper с TTL: удалять workspace `failed` задач старше N суток, оставляя
запись в БД.

### Возобновление задачи со стадии structurize

У упавшей задачи `transcribe/transcript.srt` был готов: видео скачано, аудио извлечено
и нарезано, Deepgram отработал полностью и квота потрачена. Задача умерла на
`structurize`, то есть на LLM-стадии.

Повторный запуск создаёт новую задачу с нуля: заново скачивает видео и заново платит
за транскрипцию, хотя валидный SRT лежит на диске. Механизма resume нет.

Нужна возможность стартовать с готовых артефактов workspace — как минимум со
`structurize` при наличии валидного SRT. Экономит трафик, квоту Deepgram и время;
особенно заметно, когда падения по BYOK идут серией.

## Как воспроизвести прогон

Стенд изолирован от прода: проект `lecturelog-matcher-v2`, порт 18082, свои
Postgres и MinIO. Прод (`/opt/lecturelog-core`, порт 8000) не затрагивается.

```
docker compose -p lecturelog-matcher-v2 \
  -f docker-compose.yml -f /tmp/lecturelog-matcher-v2.override.yml build api
docker compose -p lecturelog-matcher-v2 \
  -f docker-compose.yml -f /tmp/lecturelog-matcher-v2.override.yml up -d api

D=test-data/document-slide-alignment/2026-02-12
LECTURELOG_URL=http://127.0.0.1:18082/api/v1 \
  python3 scripts/submit_task.py submit --audio $D/lecture.m4a --slides $D/slides.pdf
LECTURELOG_URL=http://127.0.0.1:18082/api/v1 python3 scripts/submit_task.py poll <task_id>
LECTURELOG_URL=http://127.0.0.1:18082/api/v1 \
  python3 scripts/submit_task.py result <task_id> -o result.zip
```

Override стенда (`/tmp/lecturelog-matcher-v2.override.yml`, вне git) задаёт
`DOCUMENT_SLIDE_ALIGNMENT_MODE=v2`, `LLM_MAX_TOKENS`, `LLM_EFFORT_*`. Прогоны
последовательные — параллельный запуск сжигает суточную квоту BYOK.

Результаты прогонов лежат в `test-data/document-slide-alignment/runs/` (в git не
входят): `2026-07-26-matcher-v2-final` (A), `2026-07-26-models-3.6-flash` (B),
`2026-07-27-catalog-fixes` (промежуточный, не оценивался),
`2026-07-27-fixes-medium` (C).

## Оценка качества

Единственный принятый метод — скилл `skills/lecture-quality-judge/`, исполняемый
сабагентом. Протокол: Pass A (собственный ground truth) полностью до открытия
`document-slide-alignment.json`; запрет читать прошлые отчёты, baseline и списки
известных багов; метрики с числителями и знаменателями.

Известное ограничение метода: разброс между судьями измерим и заметен. Один и тот же
артефакт (прогон A) Codex и Claude оценили как `usable_with_alignment_issues` против
`usable_with_minor_issues`, с расхождением до 10 п.п. по отдельным метрикам. Поэтому
все сравнения делаются одним судьёй с одинаковыми параметрами, а различия меньше
примерно 5 п.п. содержательно не интерпретируются.
