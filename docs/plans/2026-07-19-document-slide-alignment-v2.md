# Высокоточная привязка пользовательских PDF/PPTX-слайдов — дизайн и план реализации

Дата: 2026-07-19. Статус: черновик для строгого архитектурного ревью.

## 0. Резюме решения

Цель — заменить текущую грубую привязку «страница документа → подраздел» на
доказуемую и диагностируемую цепочку:

```text
PDF/PPTX
  → страницы + нативный текст + визуальный каталог
  → кандидаты в таймкодах SRT
  → проверка кандидатов по точным SRT-блокам
  → глобальное sequence alignment всего deck
  → section + evidence interval + confidence для каждой страницы
  → отдельная семантическая привязка к абзацам готового Markdown
  → канонические <!-- slide:N --> маркеры
  → Obsidian/structure.json
```

Главные принципы:

1. Слайд считается сопоставленным только при наличии свидетельства в транскрипте
   или надёжного визуального обнаружения в видео. Само тематическое сходство со
   слайдом недостаточно.
2. Порядок страниц используется как мягкий глобальный prior, но не как жёсткое
   правило: разрешены пропуски, возвраты и неиспользованные страницы.
3. Неуверенный слайд не вставляется в случайный подраздел. Он уходит в явно
   отделённое приложение либо подавляется как дубль.
4. Выбор подраздела и выбор позиции между абзацами — две разные задачи с разными
   моделями ошибок и отдельными confidence.
5. Результат должен быть измерим на размеченном golden corpus до включения в
   production. «Выглядит правдоподобно» не является критерием готовности.
6. Существующие API загрузки, имена файлов слайдов и обратная совместимость
   `structure.json` сохраняются.

## 1. Текущее поведение и причины ошибок

Текущая ветка пользовательских слайдов состоит из следующих шагов:

- `DocumentSlideProvider` рендерит PDF/PPTX в изображения, но не извлекает и не
  сохраняет текст страниц;
- `GeminiStructurizer` сначала делит SRT на темы и подразделы;
- один multimodal-вызов распределяет все изображения по темам;
- отдельные вызовы распределяют изображения темы по подразделам, при этом модели
  передаются названия и интервалы подразделов, но не полный текст соответствующих
  фрагментов SRT;
- `normalize_slide_mapping` принудительно делает распределение монотонным и
  назначает отсутствующие решения предыдущему подразделу;
- `backfill_missing_slides` принудительно добавляет все потерянные страницы к
  ближайшему предшественнику или в первый подраздел;
- `ObsidianExporter` не находит маркеров у документных слайдов и выводит их блоком
  перед текстом подраздела.

Из этого следуют системные проблемы:

1. Модель сопоставляет слайд в основном с коротким названием подраздела, а не с
   тем, что действительно произносилось в его временном интервале.
2. Ошибочное локальное решение исправляется только в сторону монотонности, но не в
   сторону семантической истины.
3. Титульные страницы, agenda, разделители, appendix и страницы из другого deck
   обязаны куда-то попасть, даже когда лектор их не обсуждал.
4. Нет отличия между «точно обсуждалось», «похоже по общей теме» и «назначено
   fallback-ом».
5. Нет таймкода, evidence, confidence и диагностического артефакта; ошибки почти
   невозможно воспроизводимо разбирать.
6. Даже верно найденный подраздел не даёт позиции внутри текста.
7. LLM-ответы парсятся как свободный JSON без строгого доменного контракта и без
   проверки того, что заявленное evidence действительно существует в SRT.

## 2. Scope

### 2.1 Входит в работу

- PDF и PPTX, приложенные к аудио, загруженному видео или `video_url`;
- извлечение нативного текста страниц и multimodal-анализ страниц без текста;
- привязка страницы к фактически обсуждаемому фрагменту SRT;
- точная привязка к подразделу и семантическая вставка между Markdown-блоками;
- корректная обработка титульных, служебных, неиспользованных, повторяющихся и
  progressive-build страниц;
- дополнительное визуальное сопоставление PDF↔видео, когда доступен видеоряд;
- confidence, diagnostics, golden corpus и поэтапный rollout;
- обратная совместимость ZIP, Markdown и текущего дерева секций.

### 2.2 Не входит

- изменение публичного `POST /tasks`;
- редактор ручной коррекции привязок в UI;
- извлечение новых слайдов из видео, если пользователь уже приложил документ;
- генерация текста конспекта из информации, которая есть только на слайде, но не
  произнесена лектором;
- сохранение исходного PDF в результате;
- обязательное размещение каждой страницы в основном тексте.

### 2.3 Инварианты

- Номера страниц и маркеры остаются 1-based: `slide-01.png`, `<!-- slide:1 -->`.
- Один физический slide asset экспортируется под тем же глобальным номером независимо
  от решения matcher-а.
- В основном тексте один номер слайда встречается не более одного раза.
- Маркер вставляется только между Markdown-блоками, никогда внутрь fenced code,
  callout или списка.
- Слайд, назначенный подразделу, продолжает попадать в `slide_nums`/`slide_keys`
  этого подраздела.
- Старый web, понимающий `slide_nums` и HTML-комментарии, продолжает работать.
- Ошибка дополнительного анализа слайдов не должна уничтожать уже готовую
  транскрипцию и конспект; fallback не имеет права тихо создавать заведомо ложные
  inline-привязки.

## 3. Определение качества и release gates

До реализации алгоритма создаётся размеченный golden corpus. Он должен включать:

- русскую речь + русские слайды;
- русскую речь + английские слайды;
- сканированный PDF без текстового слоя;
- формулы, графики, схемы и таблицы;
- титульные страницы, agenda, section dividers, Q&A и appendix;
- пропущенные лектором страницы;
- переставленные страницы и возврат к предыдущей странице;
- одинаковые страницы и progressive builds;
- неправильный или частично относящийся к лекции deck;
- короткую лекцию и 60–120-минутную лекцию;
- аудио+PDF и видео+PDF.

Разметка одного кейса:

```json
{
  "case_id": "algorithms-01",
  "slides": [
    {
      "slide_num": 7,
      "status": "discussed",
      "acceptable_section_ids": [3],
      "acceptable_time_ranges": [[742.0, 790.0]],
      "role": "content"
    },
    {
      "slide_num": 19,
      "status": "unmentioned",
      "role": "appendix"
    }
  ]
}
```

E2E-разметка не ссылается на номер абзаца свободно сгенерированного конспекта:
такой номер нестабилен между моделями и версиями prompt. Точность paragraph anchor
измеряется на отдельном зафиксированном наборе `rendered_markdown + slide evidence`,
где `acceptable_anchor_blocks` действительно воспроизводимы. На реальных E2E-кейсах
оцениваются evidence time range, section и ручная/семантическая корректность соседнего
текста.

Release gates на отложенной validation-части корпуса:

- precision определения `discussed` ≥ 0.95;
- recall определения `discussed` ≥ 0.90;
- exact section accuracy для `discussed` ≥ 0.90;
- section accuracy с допуском соседнего подраздела ≥ 0.97;
- median absolute anchor error ≤ 20 секунд для размеченных таймкодов;
- p90 anchor error ≤ 60 секунд;
- inline precision по допустимому Markdown-блоку ≥ 0.90;
- inline accuracy с допуском соседнего блока ≥ 0.95;
- inline coverage среди `discussed content` ≥ 0.75; оставшиеся страницы могут быть
  честным section gallery, но не ложным inline;
- gallery fallback rate среди `discussed content` ≤ 0.25;
- false-inline rate для `unmentioned`, wrong-deck и suppressed pages = 0;
- каждый inline-маркер встречается ровно один раз: 100%;
- ни одного маркера внутри fenced code/callout/list: 100%;
- ни одного `unmentioned`/`suppressed_duplicate` в основном тексте: 100%;
- unsupported slide-only claims не появляются в v2-render; измерение ведётся отдельно
  от общей полноты конспекта;
- качество текста конспекта по существующему regression corpus не хуже legacy;
- на каждом кейсе сохраняется machine-readable diagnostic, объясняющий решение.

Метрики считаются отдельно для audio+document и video+document, а также по ролям
страниц. До production gate в held-out-наборе должно быть не менее 200 обсуждавшихся
страниц, 50 unmentioned/service pages, 10 wrong-deck кейсов и представительство обеих
модальностей. Для precision/recall публикуются 95% доверительные интервалы. Legacy
paragraph-anchor metrics маркируются `N/A`: у него документные страницы являются
section gallery и inline-маркеров нет.

Пороговые значения confidence и веса alignment не фиксируются «на глаз». Они
подбираются на train-части golden corpus и один раз проверяются на отложенной части.

## 4. Новая доменная модель

Нужны отдельные понятия asset, анализ страницы, assignment и placement. Типы,
пересекающие application/infrastructure boundaries, живут в
`lecturelog/domain/slides.py`; Pydantic-схемы сырых LLM-ответов остаются внутри
`infrastructure/slides/alignment`.

```python
@dataclass(frozen=True)
class SlideAsset:
    slide_num: int
    path: Path
    origin: Literal["document", "video"]
    timestamp: float | None
    caption: str | None
    extracted_text: str | None
    native_text_quality: Literal["good", "sparse", "none"] | None


@dataclass(frozen=True)
class SlideCatalogEntry:
    slide_num: int
    role: Literal[
        "content", "title", "agenda", "section_divider",
        "closing", "appendix", "blank"
    ]
    title: str | None
    visible_text: str
    source_concepts: tuple[str, ...]
    transcript_language_terms: tuple[str, ...]
    visual_summary: str
    formulas: tuple[str, ...]


@dataclass(frozen=True)
class SlideCatalogResult:
    slide_num: int
    status: Literal["verified", "native_text_fallback", "unresolved"]
    entry: SlideCatalogEntry | None


@dataclass(frozen=True)
class SlideRelation:
    slide_num: int
    kind: Literal["exact_duplicate", "progressive_build"]
    group_id: str
    canonical_slide_num: int


@dataclass(frozen=True)
class TranscriptBlock:
    block_id: int
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class SectionRef:
    global_section_id: int
    topic_index: int
    local_section_index: int
    start_s: float
    end_s: float


@dataclass(frozen=True)
class SlideCandidate:
    slide_num: int
    global_section_id: int
    evidence_block_ids: tuple[int, ...]
    evidence_quote: str | None
    anchor_start_s: float
    anchor_end_s: float
    lexical_score: float
    semantic_tier: Literal["explicit", "strong", "weak", "none"]
    visual_score: float | None


@dataclass(frozen=True)
class SlideAssignment:
    slide_num: int
    match_status: Literal["discussed", "unmentioned", "duplicate", "deck_mismatch"]
    global_section_id: int | None
    evidence_block_ids: tuple[int, ...]
    anchor_s: float | None
    assignment_confidence: Literal["verified", "probable", "unresolved"]
    score: float
    reason_code: str


@dataclass(frozen=True)
class SlidePlacement:
    slide_num: int
    output_kind: Literal["inline", "section_gallery", "appendix", "suppressed"]
    global_section_id: int | None
    block_index: int | None
    side: Literal["before", "after"] | None
    gallery_position: Literal["before_content", "after_content"] | None
    anchor_confidence: Literal["verified", "probable", "fallback", "none"]
    fallback_reason: str | None


@dataclass(frozen=True)
class StructurizeContext:
    source_kind: Literal["audio", "video"]
    local_video_path: Path | None


@dataclass(frozen=True)
class StructurizeResult:
    topics: list[Topic]
    slide_assignments: tuple[SlideAssignment, ...]
    slide_placements: tuple[SlidePlacement, ...]
```

Числовой score используется внутри оптимизации. Пользовательское/операционное
решение принимается по категориальному confidence, который нельзя получить простым
копированием self-reported confidence модели.

`SlideAssignment` создаётся после sequence alignment и отвечает только на вопрос
«обсуждалась ли страница и где». `SlidePlacement` появляется только после render и
paragraph anchoring и отвечает только на вопрос «как её вывести». Эти состояния
нельзя объединять: `inline` ещё неизвестен в момент assignment.

`Structurizer.structurize` возвращает `StructurizeResult`, а не голый `list[Topic]`.
`PipelineService` явно передаёт `slide_placements` в `Exporter.export`. Legacy mode
также строит этот DTO: все назначенные страницы получают legacy section-gallery,
поэтому pipeline и exporter не имеют скрытых side channels или mode-specific
догадок.

Video-frame ветка формирует final placements в `PipelineService` после существующих
`bind_frames_to_sections`/`place_slides_in_sections`: кадры получают явные номера в
timestamp-order и `inline` placement по уже вставленным маркерам. Таким образом,
унифицированный exporter не вынужден угадывать origin и старый video path не ломается.

`Section.slide_indices` сохраняется как совместимая проекция только финальных
`inline|section_gallery` placements. `appendix|suppressed` в неё не попадают.
`slide_nums`, `slide_keys` и Markdown-маркеры строятся из той же финальной проекции.

Документ рендерится атомарно: если хотя бы одну страницу невозможно отрендерить,
задача завершается `BAD_INPUT`. Page-level holes и placeholders в v2 не поддерживаются.
Каждый `SlideAsset` несёт явный `slide_num`; exporter валидирует уникальную непрерывную
последовательность `1..N` и больше не перенумеровывает assets позицией списка.
`ExportResult.slide_targets` становится отображением `slide_num → Path`, а
`build_structure` ищет target по номеру, не через `slide_idx - 1`.

Per-origin invariants для `SlideAsset` валидируются на границе provider-а:

- `document`: `timestamp=None`, `native_text_quality` обязателен, `extracted_text`
  содержит строку либо пустую строку;
- `video`: `timestamp` обязателен, `native_text_quality=None`,
  `extracted_text=None`; caption остаётся опциональным.

`SlideCatalogResult` также валидируется как discriminated contract:
`entry is None` разрешён только при `status=unresolved`; для `verified` и
`native_text_fallback` entry обязателен. Поэтому catalog failure не требует
выдумывать semantic role/title/concepts.

## 5. Целевая архитектура

Новые компоненты размещаются отдельно от `GeminiStructurizer`:

```text
lecturelog/infrastructure/slides/alignment/
  schemas.py            только Pydantic-схемы ответов LLM
  catalog.py            page text + VLM → SlideCatalogEntry
  transcript.py         SRT → стабильные TranscriptBlock
  retrieval.py          локальный candidate retrieval
  semantic.py           проверка кандидатов LLM/VLM
  sequence.py           глобальное sequence alignment
  confidence.py         evidence tiers и release-calibrated thresholds
  anchoring.py          Markdown blocks → SlidePlacement
  markers.py            единственная каноническая вставка маркеров
  diagnostics.py        сохранение alignment report
  video_evidence.py     опциональный PDF↔видео visual timestamp channel
  service.py            DocumentSlideAlignmentService
```

`GeminiStructurizer` остаётся оркестратором split/subsplit/render, но делегирует
сопоставление сервису. Это не должно превратиться в ещё один монолитный метод.
Сквозной контракт имеет вид:

```text
Structurizer → StructurizeResult
  → PipelineService
    → Exporter(topics, slide_assets, slide_placements)
```

Порядок выполнения:

```text
transcribe
  ├─ document render + native text extraction
  └─ SRT blocks
       ↓
topic split → section split → timeline validation → global SectionRef
       ↓
slide catalog
       ↓
candidate retrieval → semantic verification → sequence alignment
       ↓
render section только из SRT + verified terminology hints
       ↓
paragraph anchoring → marker injection
       ↓
export + diagnostics
```

Каталог можно позже выполнять параллельно с topic/subsplit. В первой реализации
приоритет — детерминированность и диагностируемость, не оптимизация wall-clock.

## 6. Алгоритм подробно

### 6.1 Рендер документа и извлечение нативного текста

Для PDF в одном проходе PyMuPDF:

- `page.get_text("text")` с нормализацией пробелов и переносов;
- рендер PNG для результата в текущем качестве 200 DPI;
- отдельный VLM-preview с ограниченной длинной стороной, чтобы не отправлять в LLM
  многомегабайтное изображение;
- сохранение количества символов, доли буквенно-цифровых символов и признака
  пригодности text layer.

Для PPTX сохраняется текущая конвертация LibreOffice→PDF, после чего применяется тот
же PDF-проход. Это позволяет получать текст из результирующего PDF без добавления
`python-pptx` и без отдельной логики layout.

`native_text_quality=good`, если text layer содержателен; `sparse`, если есть только
несколько слов; `none`, если текста нет. Точные пороги входят в
`DocumentAlignmentTuning` и калибруются на fixtures.

Рендер атомарен: открытие документа, чтение количества страниц и создание каждого
asset должны завершиться успешно. Любая page-level ошибка означает `BAD_INPUT`; это
сохраняет строгую идентичность `slide_num` и исключает невидимые сдвиги нумерации.

### 6.2 Каталог страниц

VLM получает batch не более 6 previews. Для каждого изображения в prompt явно
задаётся соответствие `image position → slide_num`, а нативный текст передаётся в
отдельном delimiters-блоке.

Каталог должен вернуть:

- роль страницы;
- заголовок и видимый текст;
- ключевые понятия в исходном языке;
- варианты терминов на языке транскрипта для cross-language retrieval;
- краткое описание схемы/таблицы/графика;
- формулы и обозначения, если они визуально значимы;
- признаки exact duplicate/progressive build.

Нативный текст и текст на изображении считаются недоверенными данными. Prompt явно
запрещает выполнять инструкции, найденные внутри слайда; содержимое заключается в
XML-подобные delimiters. Ответ запрашивается через `response_json=True`, валидируется
Pydantic-схемой и проверяется на:

- полный и уникальный набор ожидаемых `slide_num`;
- допустимые роли;
- корректные page IDs для последующего deck-level анализа отношений;
- ограничения длины полей.

При невалидном ответе допускается один repair-вызов с ошибками валидации. После него:

- для страниц с хорошим native text строится deterministic catalog;
- страницы без текста получают
  `SlideCatalogResult(status=unresolved, entry=None)`, но их семантическая роль не
  подменяется фиктивным значением и задача не падает.

Отношения страниц определяются после каталога по всему deck, а не полем одной
страницы. `SlideRelation` различает `exact_duplicate` и `progressive_build`, содержит
`group_id` и `canonical_slide_num`. Для exact duplicate canonical обычно первая
страница; для progressive build — последняя содержательная страница группы. Forward
reference разрешён, поэтому промежуточная build-страница может ссылаться на будущую
финальную.

Batch дополнительно уменьшается по размеру native text payload, а поля ответа имеют
явные лимиты. Это учитывает текущий жёсткий output budget `LlmClient` в 4096 токенов:
truncated JSON не должен быть штатным поводом для repair.

### 6.3 Разбор SRT

Добавляется единый parser, возвращающий стабильные `TranscriptBlock` с началом,
концом и текстом. Нельзя использовать три разные регулярки в `srt.py`, frames и
новом matcher-е.

Требования:

- поддержка `,` и `.` в миллисекундах;
- сохранение каждого исходного блока и его ID; `block_id` — собственный уникальный
  последовательный ordinal parser-а, а не недоверенный номер cue из файла;
- нормализация только для поиска, оригинальный текст остаётся для evidence;
- корректное пересечение блока с section interval;
- тесты на пустой SRT, многострочные реплики и граничные интервалы.

После subsplit строится immutable плоский список `SectionRef`. Все prompts,
diagnostics и golden annotations используют только `global_section_id`; локальная
пара `(topic_index, local_section_index)` остаётся для обратного преобразования.
Timeline валидируется до retrieval: времена parseable, `start < end`, section лежит
в topic, start не убывает, существенные overlaps запрещены. При невалидном ответе
subsplit разрешён один repair; повторный сбой даёт безопасный fallback «вся тема =
один section». Gaps допустимы и не заполняются выдуманными интервалами.

### 6.4 Локальный candidate retrieval

Полный deck нельзя сопоставлять одним огромным LLM-вызовом: он плохо проверяем,
дорог и деградирует на длинных лекциях. Сначала локально строится ограниченный набор
кандидатов для каждой страницы.

Индекс включает:

- заголовки тем и подразделов;
- полный текст SRT-блоков каждого подраздела;
- окна из соседних SRT-блоков;
- нормализованные word tokens;
- character n-grams для русских падежей, STT-ошибок и смешанных языков.

Для каждого слайда в candidate pool входят:

1. top-K подразделов по BM25/word overlap;
2. top-K по character n-gram similarity;
3. подразделы вокруг ожидаемой позиции по порядку deck;
4. соседние подразделы каждого найденного кандидата;
5. кандидаты из visual timestamps, если источник — видео.

Таким образом, слайд с английским заголовком и русским объяснением не потеряется
только из-за lexical retrieval: его поддержат concepts на языке транскрипта,
order prior и расширительный second pass.

Если первый semantic pass не находит strong evidence, выполняется один broadened
pass на уровне всей темы либо всех section summaries. Бесконечного расширения нет.

### 6.5 Semantic verification по SRT evidence

LLM/VLM получает ограниченный batch слайдов и только candidate sections с полными
SRT-блоками и стабильными ID. Для каждого слайда модель обязана вернуть максимум
три варианта:

```json
{
  "slide_num": 7,
  "candidates": [
    {
      "global_section_id": 3,
      "evidence_block_ids": [91, 92, 93],
      "evidence_quote": "при релаксации ребра мы обновляем расстояние до вершины",
      "tier": "explicit",
      "reason": "лектор объясняет релаксацию и использует обозначения со слайда"
    }
  ],
  "unmentioned": false
}
```

Prompt различает:

- `explicit`: произнесены уникальные термины, заголовок, формула или элементы
  изображения;
- `strong`: та же идея подробно объясняется другими словами;
- `weak`: совпадает только широкая тема;
- `none`: evidence отсутствует;
- «перечислено в agenda» и «содержательно объяснено»;
- «информация есть на слайде» и «информация прозвучала в лекции».

После ответа выполняется deterministic validation:

- section и block ID существуют;
- блок действительно входит в section interval;
- evidence образует разумный локальный временной кластер;
- для `explicit` обязательны конкретный evidence span/quote и block IDs; цитата
  fuzzy-match-ится с оригинальным cue text;
- после этого quote обязан иметь deterministic grounding в title, visible text,
  formula либо validated transcript-language alias конкретного слайда; иначе
  кандидат понижается до `strong` и проходит independent judge;
- `strong` без буквального совпадения принимается только после отдельного
  calibrated semantic judge либо согласия независимого verifier-вызова;
- модель не назначила один слайд одновременно взаимоисключающим sections;
- `unmentioned=true` не сосуществует с strong/explicit candidate.

Self-reported tier — один сигнал, а не окончательная истина. Order prior и lexical
score сами по себе никогда не повышают assignment до `verified`.

### 6.6 Визуальный канал для видео + пользовательский документ

Если доступен `local_video`, документные страницы получают дополнительный независимый
источник evidence — фактическое появление страницы в кадре.

Первая реализация не вызывает VLM на каждом кадре:

1. Низкоразмерный проход по видео переиспользует `compute_signals`/`ThumbStore`.
2. Берутся стабильные plateau/change-point кадры и разреженный fallback-sample.
3. Для previews PDF и video thumbs считаются ORB descriptors.
4. Дескрипторы deck объединяются в индекс; для каждого video frame выбираются top
   slide candidates, затем ORB+RANSAC homography проверяет, что страница действительно
   присутствует даже внутри экрана под перспективой.
5. Только неоднозначные top pairs отправляются VLM на verification.
6. Единичный match не считается доказательством: требуется устойчивый run либо очень
   сильная homography с соседним подтверждением.
7. Из run получается `VisualOccurrence(slide_num, start_s, end_s, score)`.

Визуальный timestamp имеет больший вес, чем порядок deck, но не уничтожает semantic
evidence. Если видео показывает одну страницу, а речь в этот момент уже обсуждает
следующую, match сохраняет оба сигнала и paragraph anchor выбирает смысловую позицию;
диагностика отмечает конфликт.

Если визуальный канал не нашёл страницу — это не означает `unmentioned`: камера могла
не показывать экран. Семантическая ветка продолжает работать как для аудио.

`local_video_path` приходит только через `StructurizeContext`; visual channel не
создаёт `VideoFrameProvider` assets и не смешивается с `video_frames`. Все его
multimodal-вызовы получают тот же `structurize_usage`, поэтому текущий
`UsageAccumulator.compute_total` учитывает их внутри `structurize`.

### 6.7 Глобальное sequence alignment

Локальные решения оптимизируются совместно для всего deck динамическим
программированием/Viterbi. Для каждой страницы состояниями служат её кандидаты плюс
`UNMATCHED` и `SUPPRESSED_DUPLICATE`.

Node score собирается из:

- semantic tier;
- lexical/character score;
- валидности и компактности evidence interval;
- visual score;
- типа страницы;
- согласованности native text и visual catalog;
- penalties за противоречия.

Transition score:

- не штрафует сохранение порядка и несколько страниц в одном section;
- мягко штрафует небольшой возврат;
- сильно штрафует большой возврат без explicit/visual evidence;
- разрешает пропуск любого количества неиспользованных страниц;
- не заставляет appendix/blank/duplicate получать section;
- допускает повторное обсуждение, но для основного Markdown выбирает первое
  содержательное появление страницы.

Строгая монотонизация, как в текущем `normalize_slide_mapping`, удаляется из нового
пути. Legacy-функция остаётся только для legacy mode до завершения rollout.

Численные веса хранятся в `DocumentAlignmentTuning`. Первоначальные значения лишь
запускают эксперимент; release-значения фиксируются после golden evaluation.

Assignment confidence считается per-slide, а не общим margin лучшего пути. Для
слайда сравнивается лучший полный путь с лучшим constrained path, в котором ему
запрещено выбранное состояние; допустим эквивалентный forward-backward marginal.
Большой выигрыш других страниц deck не может сделать слабый локальный assignment
`verified`.

### 6.8 Confidence и deck-level guard

Категории:

- `verified`: валидное explicit/strong evidence и достаточный отрыв лучшего
  per-slide constrained alternative либо сильный visual run;
- `probable`: раздел надёжен, но точный anchor или evidence неоднозначен;
- `unresolved`: только слабая тема/order prior, конфликт каналов или маленький
  per-slide alternative margin.

Политика вывода:

| Assignment | Итоговый placement |
|---|---|
| verified content + verified/probable anchor | inline возле абзаца |
| verified content + неудачный anchor | section gallery |
| probable content при любом anchor | section gallery; anchor не повышает assignment |
| unresolved/unmentioned | appendix |
| title/agenda/divider с assignment | section gallery в начале подраздела |
| closing/Q&A с assignment | gallery в конце подраздела |
| appendix/unmentioned | appendix |
| exact duplicate/build predecessor | suppressed |

Anchor confidence не может повысить assignment confidence: paragraph matcher видит
уже выбранный section и не является независимым доказательством того, что слайд
вообще обсуждался. Поэтому `probable assignment` всегда остаётся gallery. Если позже
golden corpus докажет безопасный `probable+verified anchor → inline`, это будет
отдельное изменение политики с отдельным precision gate.

Deck-level mismatch guard срабатывает, если содержательная доля страниц с evidence
аномально мала или лучшие scores неотличимы от фоновых. В этом случае matcher не
рассыпает deck по лекции: основной конспект остаётся без документных слайдов, а
страницы выводятся в приложении с reason `deck_mismatch`.

### 6.9 Рендер подразделов

До render уже известны verified/probable assignments. В v2 изображения страниц не
передаются `_render_section`: prompt не является достаточной защитой от появления
slide-only фактов. Содержание генерируется только из SRT.

Для исправления STT-написания разрешён узкий `SupportedTerminology` список:

- canonical spelling берётся из catalog;
- каждый термин связан с конкретными evidence block IDs;
- в render prompt явно сказано исправлять написание, а не добавлять определение,
  формулу или факт;
- unresolved/unmentioned catalog entries не участвуют.

Renderer не вставляет `<!-- slide:N -->`; marker placement выполняется отдельной
стадией после render. Golden evaluation отдельно проверяет unsupported slide-only
claims. Если в будущем изображения вернутся в render, это требует отдельного
factual-support verifier и нового release gate.

### 6.10 Семантическая привязка к Markdown-блокам

После render Markdown разбивается на top-level blocks. Существующая логика
`split_paragraphs` становится общей утилитой, но получает полноценные тесты на:

- fenced code с пустыми строками;
- callouts;
- списки;
- таблицы;
- вложенные quote-блоки;
- уже присутствующие HTML comments.

Для каждого section с документными слайдами text-only anchor call получает:

- нумерованные Markdown-блоки;
- каталог каждого слайда;
- проверенные SRT evidence blocks;
- anchor interval;
- тип слайда.

Он возвращает `before/after block_index`. Это отдельный вызов, потому что section
assignment и paragraph placement имеют разные критерии. Несколько слайдов одного
section обрабатываются одним batch-вызовом.

Ответ валидируется:

- индекс существует;
- каждый ожидаемый slide_num дан не более одного раза;
- порядок нескольких слайдов не нарушается без явного evidence;
- structural slide не вклинивается в середину предложения;
- позиция находится между atomic Markdown blocks.

Fallback при отказе anchor LLM:

1. Verified/probable assignment становится section gallery.
2. Inline fallback допустим только после появления отдельного проверяемого
   provenance-контракта `rendered block → source SRT block IDs` либо надёжного
   deterministic semantic evidence↔Markdown matcher.
3. Существующая length/time эвристика остаётся только для видеокадров и никогда не
   применяется к документным страницам.
4. Для unresolved assignment inline-вставки нет.

Перед вставкой удаляются любые случайно сгенерированные моделью
`<!-- slide:\d+ -->`; затем `markers.py` единственным способом строит канонические
маркеры. После вставки выполняется invariant check по всему документу.

### 6.11 Неиспользованные страницы и приложение

Все неподтверждённые страницы сохраняются как assets, но не смешиваются с лекцией.
В `конспект.md` после основного текста создаётся раздел:

```md
# Дополнительные слайды

Эти страницы не удалось надёжно связать с конкретным фрагментом лекции.

![Слайд 19](slides/slide-19.png)
```

Exact duplicates и промежуточные progressive builds не выводятся даже в приложение;
диагностика указывает canonical page. Финальная страница build-группы сохраняется.

На первом rollout `structure.json` остаётся обратно совместимым: assigned slides
попадают в существующие `slide_keys`/`slide_nums`, приложение гарантированно есть в
Markdown. Добавление top-level `unassigned_slides` делается отдельным additive
контрактом только после проверки потребителя `lecturelog-web`.

### 6.12 Внешняя fail-safe граница alignment

Локальные fallback-и не покрывают неожиданный дефект orchestration, DP или marker
code. Поэтому вся document-alignment orchestration — pre-render
`DocumentSlideAlignmentService` плюс post-render anchoring/markers — имеет внешнюю
границу на уровне structurizer, отделённую от базовой структуризации:

- `shadow`: неожиданное исключение логируется, сохраняется fallback diagnostic и
  возвращается полностью legacy result;
- `v2`: конспект всё равно рендерится только из SRT, а все document assets получают
  `appendix` placement с reason `alignment_internal_fallback`;
- topic split, subsplit и SRT-only render остаются критическими стадиями: их ошибки
  не маскируются как slide fallback;
- ошибка открытия/атомарного рендера входного документа остаётся `BAD_INPUT` и не
  превращается в пустое приложение;
- marker invariant failure откатывает только document inline placements в gallery,
  не готовый текст.

Этот boundary покрывается отдельным integration test с неожиданным исключением из
alignment service, а не только scripted LLM failures.

## 7. Ошибки и fallback matrix

| Сбой | Поведение |
|---|---|
| PDF/PPTX не открывается | задача `FAILED/BAD_INPUT`, как сейчас |
| text layer пуст | VLM catalog по изображению |
| catalog batch невалиден после repair | native-text catalog; без текста → unresolved |
| local retrieval пуст | order-neighborhood + broadened semantic pass |
| semantic LLM недоступен | verified visual matches остаются; остальное в приложение |
| sequence alignment не имеет надёжного пути | deck mismatch guard, без inline |
| anchor LLM недоступен | section gallery; length/time fallback для документов запрещён |
| diagnostics write failed | warning; результат не падает |
| visual video channel failed | semantic audio-like path продолжает работу |
| invariant marker check failed | document markers откатываются в section gallery; конспект сохраняется |
| unexpected alignment exception | shadow→legacy; v2→SRT-only + весь deck в appendix |

Важно: fallback должен быть консервативным. «Завершить задачу без inline-слайдов»
лучше, чем уверенно показать неправильные страницы внутри текста.

## 8. Диагностика и наблюдаемость

В scratch `structurize/slide-alignment.json` сохраняется отчёт:

```json
{
  "version": 2,
  "mode": "v2",
  "deck_guard": "ok",
  "slides": [
    {
      "slide_num": 7,
      "role": "content",
      "match_status": "discussed",
      "global_section_id": 3,
      "evidence_block_ids": [91, 92],
      "anchor_s": 754.4,
      "assignment_confidence": "verified",
      "placement": {
        "output_kind": "inline",
        "block_index": 2,
        "side": "after",
        "anchor_confidence": "verified"
      },
      "score_components": {
        "semantic": 0.9,
        "lexical": 0.6,
        "visual": null,
        "sequence": 0.2
      },
      "reason_code": "explicit_srt_evidence"
    }
  ]
}
```

В production-лог пишутся только агрегаты, без текста слайдов/SRT:

- total/inline/gallery/unmentioned/duplicate counts;
- verified/probable/unresolved counts;
- deck mismatch;
- число catalog/semantic/anchor calls;
- fallback reason counters;
- duration каждого substage.

LLM usage продолжает учитываться в `structurize.by_model`, чтобы не ломать API.
При необходимости позднее добавляется внутренняя детализация `operation`, но не
новая публичная PipelineStage.

## 9. Конфигурация и rollout mode

Добавляется один основной env-контракт:

```text
DOCUMENT_SLIDE_ALIGNMENT_MODE=legacy|shadow|v2
```

- `legacy`: текущие prompts + normalize/backfill + section gallery;
- `shadow`: вычислить v2 и diagnostics, но экспортировать legacy;
- `v2`: экспортировать новое решение.

Во время промежуточных implementation-коммитов до готовности anchoring/export
валидны только `legacy|shadow`; значение `v2` добавляется в settings schema и
становится допустимым лишь в Задаче 10. Поэтому ни один промежуточный коммит не
публикует наполовину реализованный режим.

На первом deploy default=`legacy`. После golden gate и production shadow-а default
меняется на `v2`. Rollback не требует миграции БД или отката образа — достаточно
вернуть `legacy`.

Численные параметры собираются в `DocumentAlignmentTuning`, аналогично
`FramesTuning`: batch sizes, top-K, n-gram sizes, score weights, transition penalties,
confidence thresholds, deck guard. Не создавать десятки env-переменных до реальной
необходимости операторского тюнинга.

Новые prompts:

- `prompts/document_slide_catalog_v1.md`;
- `prompts/document_slide_semantic_match_v1.md`;
- `prompts/document_slide_anchor_v1.md`;
- `prompts/document_slide_visual_verify_v1.md`.

Версии prompts входят в diagnostics.

Чтобы не размножать model-конфигурацию до измерений, catalog и semantic verifier
используют текущие `subsplit_models/effort_subsplit`, anchor —
`render_models/effort_render`, visual verifier — `subsplit_models`. Если golden usage
покажет конфликт качества/стоимости, отдельные env model lists добавляются отдельным
решением. `FRAMES_ENABLED=false` не отключает visual document evidence: канал не
создаёт видеокадры и относится к document alignment.

## 10. Производительность и стоимость

Ориентир: лекция 90 минут, deck 50 страниц.

- document render/native text: CPU, десятки секунд;
- catalog: примерно 9 batched multimodal calls при batch≤6;
- local retrieval/sequence alignment: локально, секунды;
- semantic verification: примерно 7–12 calls в зависимости от ambiguity;
- paragraph anchor: только sections со слайдами, text-only batches;
- video visual evidence: low-res CPU pass + VLM только для неоднозначных пар.

Ограничения:

- previews имеют ограничение разрешения и байтов;
- native text обрезается только в prompt-копии, полный текст остаётся локально;
- catalog/semantic concurrency по умолчанию 2, чтобы не конкурировать с render за
  RPM бесплатного BYOK;
- batches имеют стабильный порядок и детерминированную группировку;
- максимальный размер deck документируется и проверяется на входе; при превышении
  возвращается `BAD_INPUT`, а не случайный OOM;
- точная стоимость измеряется usage на golden corpus и фиксируется перед rollout.

Release budget: p95 дополнительного wall-clock для audio+50 slides ≤ 5 минут без
учёта транскрибации; paid-equivalent LLM cost отдельно утверждается после измерения,
а не оценивается по устаревающим прайсам в этом документе.

## 11. План реализации

Каждая задача завершается отдельным проверяемым коммитом. Реализация ведётся через
TDD: сначала failing test, затем минимальный код, затем полный regression suite.
Сам этот файл находится под игнорируемым `docs/plans/`, поэтому до первого коммита
его необходимо явно добавить через
`git add -f docs/plans/2026-07-19-document-slide-alignment-v2.md`.

### Задача 1. Golden corpus и evaluation harness

Файлы:

- создать `tests/golden/document_slides/schema.json`;
- создать `tests/golden/document_slides/cases/` с небольшими синтетическими PDF/SRT;
- создать `scripts/evaluate_document_slides.py`;
- создать `tests/unit/test_document_slide_evaluation.py`.

Шаги:

1. Зафиксировать annotation schema из §3.
2. Добавить минимум шесть полностью синтетических committed cases.
3. Поддержать private manifest с абсолютными/внешними путями без коммита media.
4. Реализовать discussed precision/recall, section accuracy, anchor time error,
   fixed-Markdown inline precision/coverage, gallery rate, false-inline rate,
   marker invariants, unsupported-claims audit и per-role/per-modality breakdown.
5. Для proportions рассчитывать 95% confidence intervals и проверять минимальный
   размер held-out набора.
6. Снять baseline legacy и сохранить отчёт в `docs/progress`, пометив document
   paragraph-anchor metrics как `N/A`.

Gate: evaluator воспроизводимо выдаёт одинаковые метрики и ошибается на намеренно
испорченном prediction fixture.

### Задача 2. Единый SRT parser

Файлы:

- изменить `lecturelog/infrastructure/srt.py`;
- изменить `lecturelog/infrastructure/frames/provider.py`;
- добавить `tests/unit/test_srt_blocks.py`;
- обновить существующие SRT/frames tests.

Шаги:

1. Ввести `TranscriptBlock` и `parse_srt_blocks`.
2. Перевести `extract_srt_fragment` и frames nearest-text на единый parser.
3. Сохранить прежние публичные helpers как wrappers.
4. Проверить граничные интервалы и миллисекунды.

Gate: все старые SRT и frames tests проходят; больше нет приватного второго parser-а.

### Задача 3. Сквозные slide/result contracts и atomic document extraction

Файлы:

- создать `lecturelog/domain/slides.py`;
- изменить `lecturelog/domain/ports.py`;
- изменить `lecturelog/domain/exceptions.py`,
  `lecturelog/application/error_classifier.py`;
- изменить `lecturelog/infrastructure/slides/document_provider.py`;
- изменить `lecturelog/application/pipeline_service.py`;
- изменить `lecturelog/infrastructure/structurize/gemini_structurizer.py`;
- изменить `lecturelog/infrastructure/export/obsidian_exporter.py`, `structure.py`;
- обновить contracts/tests всех потребителей `SlideImage`/`Structurizer`/`Exporter`.

Шаги:

1. Ввести `SlideAsset`, `SlideAssignment`, `SlidePlacement`, `StructurizeContext` и
   `StructurizeResult` из §4.
2. `SlideProvider` возвращает assets с явным уникальным `slide_num`; документный
   provider заполняет `extracted_text`, video provider сохраняет timestamp/caption.
3. `Structurizer` принимает assets+context и возвращает `StructurizeResult`.
4. `PipelineService` передаёт placements в `Exporter`; exporter принимает полный
   deck и решения отдельно.
5. В legacy adapter построить assignments/section-gallery placements из текущих
   `Topic.slide_indices`, чтобы результат не изменился.
6. После существующего video binding построить явные номера и inline placements для
   video frames; exporter не ветвится по origin.
7. `ExportResult.slide_targets` и `build_structure` перевести на явное отображение
   `slide_num → Path`, исключив неявное `idx - 1`.
8. Для PDF/PPTX сделать атомарный render, подтвердить page order и text layer.
9. Оборачивать ошибки PyMuPDF/LibreOffice/page render в отдельный
   `InvalidSlidesDocument`, явно классифицируемый как `BAD_INPUT`.
10. Добавить preview helper, независимый от экспортного 200-DPI asset.
11. Передавать `StructurizeContext(source_kind, local_video_path)`; визуальный канал
   пока ничего с ним не делает.

Gate: legacy mode создаёт побайтно/структурно эквивалентный layout результата; все
новые порты сквозные и нет side channel для appendix/duplicates.

### Задача 4. Slide catalog

Файлы:

- создать `alignment/schemas.py`, `alignment/catalog.py`;
- добавить `prompts/document_slide_catalog_v1.md`;
- добавить `tests/unit/slides/test_catalog.py`.

Шаги:

1. Pydantic response schema и defensive parser.
2. Deterministic batching максимум по шесть страниц с явной image→slide mapping,
   ограничениями длины output и payload-aware уменьшением batch.
3. Prompt-injection delimiters.
4. Один repair-вызов.
5. Native-text fallback через discriminated `SlideCatalogResult`, отдельные source
   concepts и transcript-language aliases.
6. Role validation и deck-level `SlideRelation` для exact
   duplicates/progressive builds.

Gate: shuffled/partial/hallucinated responses не проходят в доменную модель.

### Задача 5. Retrieval index и candidate generation

Файлы:

- создать `alignment/transcript.py`, `alignment/retrieval.py`;
- добавить `tests/unit/slides/test_retrieval.py`.

Шаги:

1. После subsplit строить и валидировать глобальные `SectionRef`; один repair, затем
   fallback темы в один section.
2. Нормализация word tokens и character n-grams без тяжёлой ML-зависимости.
3. BM25/ngram score по sections и SRT windows.
4. Order-neighborhood и neighbor expansion.
5. Cross-language и STT-error fixtures.
6. Bounded broadened pass.

Gate: правильный section присутствует в candidate pool минимум в 98% размеченных
`discussed` golden slides; `unmentioned` не участвуют в retrieval-recall denominator.

### Задача 6. Semantic verifier

Файлы:

- создать `alignment/semantic.py`;
- добавить `prompts/document_slide_semantic_match_v1.md`;
- добавить `tests/unit/slides/test_semantic.py`.

Шаги:

1. Stable-ID prompt и строгий JSON.
2. Batch planner по contiguous slides/candidate sections.
3. Валидация global section/block IDs, evidence cluster и обязательной цитаты для
   `explicit`, включая grounding quote↔slide claim/alias.
4. Независимый verifier/calibrated judge для `strong` без literal evidence.
5. Различение explicit/strong/weak/none и agenda-vs-discussed.
6. Repair/fallback без принудительного назначения.

Gate: fake LLM не может сослаться на несуществующий SRT block или вынудить matcher
принять `unmentioned` слайд.

### Задача 7. Sequence alignment и confidence

Файлы:

- создать `alignment/sequence.py`, `alignment/confidence.py`;
- добавить `tests/unit/slides/test_sequence.py`;
- перестать использовать `normalize_slide_mapping`/`backfill_missing_slides` в v2.

Шаги:

1. DP со state `candidate|unmatched|duplicate`.
2. Soft monotonic transition penalties.
3. Per-slide constrained-path margin либо forward-backward marginals и
   evidence-based confidence tiers.
4. Deck mismatch guard.
5. Fixtures: skip, backtrack, repeated topic, wrong deck, duplicate/build.

Gate: DP даёт глобальный optimum на исчерпывающе проверяемых маленьких matrices;
unmentioned страницы не получают section только из-за order prior.

### Задача 8. Shadow-интеграция alignment в structurizer

Файлы:

- создать `alignment/service.py`;
- создать `alignment/diagnostics.py` с минимальной versioned schema/writer;
- изменить `gemini_structurizer.py`;
- изменить `api/lifespan.py`, `settings.py`;
- обновить `tests/unit/test_gemini_structurizer.py`, `test_config.py`,
  `test_pipeline_service.py`.

Шаги:

1. В settings разрешить только `legacy|shadow`; default=`legacy`.
2. Alignment после validated subsplit, до render.
3. Shadow вычисляет assignments/diagnostics, но render, placements и exporter
   получают legacy-решение; result bytes не меняются.
4. Все новые VLM-вызовы получают `structurize_usage`; отдельная публичная стадия не
   добавляется.
5. Progress внутри существующей `STRUCTURIZE` шкалы остаётся монотонным.
6. Явно протестировать mode matrix: `legacy=работает`, `shadow=работает`, `v2`
   отклоняется settings validation до Задачи 10.
7. Добавить outer-boundary integration test: неожиданное исключение alignment в
   shadow логируется и возвращает legacy result.

Gate: no-slides и video-frames paths не изменились; shadow не меняет result bytes,
а diagnostic остаётся только в scratch.

### Задача 9. Paragraph anchoring и marker injector

Файлы:

- создать `alignment/anchoring.py`, `alignment/markers.py`;
- добавить `prompts/document_slide_anchor_v1.md`;
- расширить `tests/unit/frames/test_placement.py` либо вынести общие Markdown-block
  tests в `tests/unit/slides/test_markers.py`.

Шаги:

1. Общий безопасный Markdown block parser.
2. Text-only paragraph anchor batch.
3. Валидация и order normalization.
4. Только section-gallery fallback для документов; length/time fallback остаётся
   исключительно в video-frame коде.
5. Strip случайных markers + canonical injection + final invariant check.

Gate: property-style tests подтверждают один marker на slide и отсутствие insertion
внутри atomic Markdown constructs.

### Задача 10. V2 render, export, appendix и structure compatibility

Файлы:

- изменить `gemini_structurizer.py`, `obsidian_exporter.py`, `structure.py`;
- изменить `docs/api-contract.md`, `README.md`;
- расширить `test_obsidian_exporter.py`, `test_structure.py`.

Шаги:

1. V2 render не получает изображения; он получает только evidence-backed
   `SupportedTerminology` и исходный SRT fragment.
2. Из assignments+anchors сформировать ровно один final `SlidePlacement` на asset;
   `probable assignment` всегда становится section gallery.
3. Документные markers заменяются inline тем же механизмом, что video frames.
4. Section gallery остаётся явным fallback.
5. Unresolved assets выводятся в Markdown appendix.
6. Duplicates/build predecessors получают `suppressed` и не дублируются.
7. Existing `slide_nums` и `slide_keys` строятся из финальных placements и остаются
   согласованными с явным `slide_num → target`.
8. Добавить `v2` в settings schema только в этом коммите; протестировать полный mode
   matrix и быстрый rollback.
9. Добавить integration test внешней fail-safe границы: неожиданная ошибка alignment
   в v2 даёт SRT-only конспект и весь deck в appendix.
10. Отдельно подготовить, но не включать без web-аудита additive
   `unassigned_slides` contract.

Gate: старый structure consumer получает прежнюю форму; ZIP содержит все ожидаемые
assets и не содержит битых ссылок; v2 впервые является полностью рабочим режимом.

### Задача 11. Visual evidence для video+document

Файлы:

- создать `alignment/video_evidence.py`;
- использовать уже переданный `StructurizeContext.local_video_path`;
- добавить `prompts/document_slide_visual_verify_v1.md`;
- добавить synthetic video+PDF fixtures и unit/integration tests.

Шаги:

1. Stable/change-point sampling на существующих frame primitives.
2. ORB deck index и top candidate retrieval.
3. RANSAC homography validation.
4. Temporal run aggregation.
5. VLM verification только ambiguous pairs.
6. Fusion visual occurrences с semantic candidates без создания video-frame assets.
7. Все VLM usage events записывать как `structurize`, чтобы `compute_total` не терял
   токены.

Gate: полноэкранные и перспективно снятые synthetic pages находятся в допустимом
time range; talking head и похожий фон не дают false positive.

### Задача 12. Diagnostics, golden tuning и полный regression

Файлы:

- расширить `alignment/diagnostics.py`;
- расширить evaluator;
- обновить docs/progress;
- добавить optional slow/private test marker.

Шаги:

1. Расширить минимальную diagnostic schema без нарушения её versioning/compatibility,
   добавить версии prompts/tuning и score components.
2. Aggregate logs без пользовательского контента.
3. Подбор thresholds только на train corpus.
4. Однократная оценка на held-out corpus.
5. Проверка inline precision/coverage, gallery rate, false-inline и unsupported
   slide-only claims отдельно по audio/video.
6. Полный `pytest`, `ruff`, API/OpenAPI contract suite.
7. Проверка пиков RAM, wall-clock и usage на длинном deck.

Gate: все метрики §3 и performance budget §10 выполнены.

### Задача 13. Production rollout

1. Deploy `legacy`, проверить отсутствие регрессий.
2. Включить `shadow` на ограниченной доле document-slide задач.
3. Собирать только агрегаты и вручную разобрать согласованный набор diagnostics.
4. Сравнить legacy/v2 на одних задачах, особенно false inline placements.
5. Включить `v2` selectively для audio+document.
6. После отдельного video evidence gate включить для video+document.
7. Сохранить мгновенный rollback через env mode.
8. После стабильного периода удалить legacy prompts/backfill path отдельным PR.

## 12. Тестовая матрица

### Unit

- PDF text layer: good/sparse/none;
- `SlideAsset` per-origin invariants и atomic page numbering;
- PPTX→PDF text preservation;
- catalog batching/numbering/repair/injection resistance;
- SRT block parser;
- BM25/ngram/order candidates;
- invalid evidence IDs;
- DP monotonic/skip/backtrack/duplicate/unmatched;
- per-slide constrained-path confidence;
- global `SectionRef` identity и invalid timeline repair/fallback;
- explicit quote verification и strong-judge disagreement;
- deck mismatch;
- Markdown atomic blocks;
- marker uniqueness/order;
- appendix and asset references;
- visual ORB/homography/run aggregation.

### Integration с fake LLM

- audio+PDF happy path до итогового `конспект.md`;
- scanned PDF;
- English slides/Russian SRT;
- semantic verifier failure;
- unexpected alignment exception в shadow и v2;
- anchor failure;
- wrong deck;
- video+PDF visual hint;
- `legacy`, `shadow`, `v2` equivalence rules;
- usage/progress remain valid.

### Golden/private evaluation

- реальные университетские лекции разных дисциплин;
- длинные decks;
- poor STT;
- частично использованный deck;
- лекция, не соответствующая deck;
- ручная оценка места картинки, а не только section ID.

### Full regression

```bash
uv run pytest -q
uv run ruff check lecturelog tests scripts
```

Интеграционные тесты с реальным OpenRouter не входят в обычный CI и запускаются
только с явно заданным ключом/маркером.

## 13. Риски и контрмеры

| Риск | Контрмера |
|---|---|
| LLM уверенно выдумывает evidence | stable block IDs + deterministic validation + unresolved |
| Русская речь, английский deck | bilingual concepts + char n-grams + semantic broadened pass |
| Формулы/схемы без текста | VLM visual catalog; не полагаться только на OCR/text layer |
| Неправильный deck | deck-level guard + appendix вместо forced mapping |
| Лектор меняет порядок | soft, а не strict monotonic DP; strong evidence разрешает backtrack |
| Слайды влияют на факты конспекта | v2-render не получает изображения; только evidence-backed terminology |
| Markdown ломается | atomic block insertion + invariant checker |
| Слишком много LLM-вызовов | local candidate retrieval, batching, bounded second pass |
| Длинный PDF вызывает OOM | preview limits, bounded batch, max pages input validation |
| Shadow удваивает стоимость | ограниченная выборка и измеренный срок shadow rollout |
| Web не понимает unmatched | Markdown appendix сейчас; additive JSON только после web-аудита |
| ORB ошибается на похожих шаблонах | homography + temporal run + semantic/VLM verification |
| Неожиданная ошибка alignment кода | outer boundary: shadow→legacy, v2→SRT-only+appendix |

## 14. Definition of Done

Работа завершена только когда:

- существует reproducible golden corpus и baseline legacy;
- новый matcher выдаёт evidence и reason для каждой страницы;
- release gates §3 выполнены на held-out данных;
- document slides появляются inline в семантически правильных местах;
- неупомянутые/чужие страницы не загрязняют основной конспект;
- audio+document и video+document имеют проверенные пути;
- старые no-slides/video-frames/API contracts не сломаны;
- есть legacy/shadow/v2 rollout и быстрый rollback;
- README/API contract отражают новое поведение;
- полный test/lint suite зелёный;
- production shadow подтверждает лабораторные метрики на реальном трафике.

## 15. Чек-лист строгого ревью этого плана

Ревьюер должен отдельно проверить:

1. Не смешаны ли page asset, match и placement.
2. Есть ли скрытый путь, который снова заставит назначить каждый слайд.
3. Может ли LLM сослаться на несуществующий evidence и пройти validation.
4. Не ломает ли изменение `Structurizer` video-frame ветку.
5. Согласованы ли markers, `slide_indices`, `slide_nums`, `slide_keys` и appendix.
6. Не может ли marker injector повредить Markdown.
7. Реалистичен ли visual PDF↔video channel по CPU и false positives.
8. Не используются ли self-reported confidence как объективная вероятность.
9. Достаточны ли release gates для запрета красивых, но ложных вставок.
10. Есть ли безопасный fallback и rollback на каждой внешней границе.
11. Не требует ли план неявной миграции/изменения lecturelog-web.
12. Можно ли реализовать задачи по порядку без временно сломанного main.
