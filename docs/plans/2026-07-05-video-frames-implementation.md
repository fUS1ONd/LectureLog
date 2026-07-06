# Извлечение релевантных кадров из видео — план имплементации

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Стадия пайплайна, которая из видео лекции извлекает осмысленные стопкадры (слайды / доска «дописал» / точки остановки live-coding), проверяет их через VLM и привязывает к секциям конспекта по таймкодам.

**Architecture:** Трёхслойная воронка «сигналы → режимы → политики» по дизайну `docs/plans/2026-07-05-video-frames-design.md`: локальный CV (ffmpeg 1 fps → numpy/OpenCV) считает временные сигналы, таймлайн сегментируется на режимы, VLM подключается дважды точечно (классификация режимов + финальный QC), пер-режимные политики выбирают кандидатов локально. Привязка кадров к секциям — пост-хок по таймстемпам, structurize от кадров не зависит.

**Tech Stack:** Python 3.12, numpy + opencv-python-headless, ffmpeg (subprocess), `LlmClient` (OpenRouter BYOK, `google/gemini-3.1-flash-lite`), pytest + синтетические видео (numpy + ffmpeg).

**Прайс (сверено 2026-07-05, закрывает §14.2 дизайна):** flash-lite на OpenRouter — $0.25/M input, $1.50/M output (дороже оценки дизайна $0.10/$0.40). Лекция ≈ 40k in + 3k out ≈ **$0.010–0.015** — критерий «≤ 2 ¢» из §13 выполняется. Основной путь — бесплатный BYOK.

**Ветка:** имплементация — на новой ветке `feature/video-frames` от `main` (после merge дизайна). План исполняется строго по порядку задач: каждая следующая опирается на предыдущие.

**Ключевые файлы для чтения перед стартом:**
- `docs/plans/2026-07-05-video-frames-design.md` — дизайн (обязательно, целиком);
- `lecturelog/domain/ports.py` — порты `SlideProvider`, `Exporter`;
- `lecturelog/application/pipeline_service.py` — оркестрация стадий;
- `lecturelog/infrastructure/llm/llm_client.py` — контракт `LlmClient.call`;
- `lecturelog/infrastructure/srt.py` — `parse_srt_time`, `extract_srt_fragment`.

**Соглашения:** комментарии в коде — на русском. Тесты: `pytest` с `asyncio_mode = "auto"` (async-тесты без декораторов). Линт: `ruff check lecturelog tests` перед каждым коммитом. Все численные пороги — поля `FramesTuning` (Задача 4), в коде политик магических чисел нет.

---

## Задача 1: Зависимости и фиксация прайса

**Files:**
- Modify: `pyproject.toml`
- Modify: `docs/plans/2026-07-05-video-frames-design.md` (§7)

**Step 1: Добавить зависимости**

В `pyproject.toml` в `dependencies` добавить (numpy приезжает транзитивно с opencv, но фиксируем явно — код импортирует его напрямую):

```toml
    "numpy>=2.0",
    "opencv-python-headless>=4.10",
```

**Step 2: Синхронизировать окружение**

Run: `uv sync --extra dev`
Expected: успешная установка, `uv run python -c "import cv2, numpy; print(cv2.__version__)"` печатает версию.

**Step 3: Зафиксировать прайс в дизайне**

В §7 дизайн-дока заменить строку с «(сверить актуальный прайс OpenRouter при написании плана)» на актуальные цифры: `$0.25/M in, $1.50/M out → ≈ $0.010–0.015 за лекцию (сверено 2026-07-05)`. В §14 пометить вопрос 2 как закрытый.

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock docs/plans/2026-07-05-video-frames-design.md
git commit -m "chore(frames): зависимости opencv/numpy + актуальный прайс flash-lite"
```

---

## Задача 2: Фабрика синтетических видео (фундамент TDD)

Синтетика — главный инструмент тестирования стадии D (дизайн §11): numpy рисует детерминированные кадры, ffmpeg собирает видео. Все генераторы принимают `seed` и дают воспроизводимый результат.

**Files:**
- Create: `tests/support/synthetic_video.py`
- Test: `tests/unit/frames/test_synthetic_video.py`
- Create: `tests/unit/frames/__init__.py` (пустой)

**Step 1: Написать падающий тест**

```python
# tests/unit/frames/test_synthetic_video.py
from __future__ import annotations

import subprocess

import numpy as np

from tests.support.synthetic_video import (
    board_frames,
    slides_frames,
    speaker_frames,
    typing_frames,
    write_video,
)


def _duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def test_write_video_creates_playable_file(tmp_path):
    frames = [np.full((180, 320), i * 8, dtype=np.uint8) for i in range(30)]
    path = write_video(frames, tmp_path / "gray.mp4", fps=1)
    assert path.exists()
    assert abs(_duration(path) - 30.0) < 1.5


def test_slides_frames_have_step_transitions():
    frames = slides_frames(n_slides=3, secs_per_slide=10)
    assert len(frames) == 30
    # Внутри слайда кадры идентичны, на границе — заметный скачок
    assert np.array_equal(frames[3], frames[4])
    diff = np.abs(frames[10].astype(int) - frames[9].astype(int)).mean()
    assert diff > 5.0


def test_board_frames_accumulate_ink_and_erase():
    frames = board_frames(write_secs=40, erase_at=50, total_secs=70, seed=1)
    ink_early = (frames[5] > 128).sum()
    ink_late = (frames[45] > 128).sum()
    ink_after_erase = (frames[60] > 128).sum()
    assert ink_late > ink_early  # штрихи копятся
    assert ink_after_erase < ink_late * 0.5  # стирание уничтожило больше половины


def test_typing_frames_have_small_localized_diffs():
    frames = typing_frames(total_secs=30, fps=4, seed=2)
    assert len(frames) == 120
    d = np.abs(frames[41].astype(int) - frames[40].astype(int))
    # Мелкий локализованный дифф: меняется < 3% кадра
    assert (d > 20).mean() < 0.03


def test_speaker_frames_have_large_motion():
    frames = speaker_frames(total_secs=20, seed=3)
    d = np.abs(frames[11].astype(int) - frames[10].astype(int))
    assert (d > 20).mean() > 0.02  # крупный движущийся блоб
```

**Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/unit/frames/test_synthetic_video.py -x -q`
Expected: FAIL — `ModuleNotFoundError: tests.support.synthetic_video`

**Step 3: Реализация**

```python
# tests/support/synthetic_video.py
"""Детерминированные синтетические видео для тестов стадии кадров.

Кадры — numpy uint8 grayscale (H, W); write_video собирает их в mp4 через
ffmpeg rawvideo-пайп. Генераторы имитируют сигнатуры типов лекций из дизайна:
слайды (ступеньки+плато), доска (накопление ink + препод + стирание),
live-coding (мелкие диффы + курсор + скролл), спикер (крупное движение).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

H, W = 180, 320


def write_video(frames: list[np.ndarray], path: Path, fps: int = 1) -> Path:
    h, w = frames[0].shape[:2]
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        input=b"".join(np.ascontiguousarray(f).tobytes() for f in frames),
        check=True,
    )
    return path


def _text_block(rng: np.random.Generator, frame: np.ndarray, y: int, dark: bool) -> None:
    """Строка «текста»: серия тёмных (или светлых) прямоугольников-слов."""
    x = 20
    while x < W - 40:
        word_w = int(rng.integers(10, 30))
        frame[y : y + 6, x : x + word_w] = 30 if dark else 220
        x += word_w + 8


def slides_frames(
    n_slides: int = 3, secs_per_slide: int = 10, builds: bool = False, seed: int = 0
) -> list[np.ndarray]:
    """Слайды: светлый фон, статичный «текст», резкая смена между слайдами.
    builds=True — на середине слайда добавляется ещё одна строка (bullet-build)."""
    rng = np.random.default_rng(seed)
    frames: list[np.ndarray] = []
    for s in range(n_slides):
        base = np.full((H, W), 235, dtype=np.uint8)
        for row in range(3):
            _text_block(rng, base, 30 + row * 30, dark=True)
        built = base.copy()
        _text_block(rng, built, 150, dark=True)
        for t in range(secs_per_slide):
            if builds and t >= secs_per_slide // 2:
                frames.append(built.copy())
            else:
                frames.append(base.copy())
    return frames


def board_frames(
    write_secs: int = 40,
    erase_at: int | None = 50,
    total_secs: int = 70,
    with_teacher: bool = True,
    seed: int = 0,
) -> list[np.ndarray]:
    """Доска (мел): тёмный фон, каждую секунду записи добавляется штрих;
    «препод» — серый прямоугольник, медленно ездит и перекрывает доску;
    в erase_at доска очищается за 2 секунды."""
    rng = np.random.default_rng(seed)
    board = np.full((H, W), 40, dtype=np.uint8)
    frames: list[np.ndarray] = []
    for t in range(total_secs):
        if t < write_secs:
            # Новый штрих: короткая светлая линия в «зоне письма», едущей слева направо
            x0 = 20 + int((W - 80) * t / max(write_secs, 1))
            y0 = int(rng.integers(30, H - 40))
            board[y0 : y0 + 3, x0 : x0 + int(rng.integers(15, 35))] = 210
        if erase_at is not None and erase_at <= t < erase_at + 2:
            board[:, : W // 2] = 40  # стёрли левую половину
        frame = board.copy()
        if with_teacher:
            # Препод перекрывает часть доски и медленно двигается
            tx = 40 + int(30 * np.sin(t / 5.0)) + t % 3
            frame[60:170, tx : tx + 50] = 110
        frames.append(frame)
    return frames


def typing_frames(
    total_secs: int = 30,
    fps: int = 4,
    burst_ranges: list[tuple[int, int]] | None = None,
    scroll_at: int | None = None,
    seed: int = 0,
) -> list[np.ndarray]:
    """Live-coding: светлый фон, «код» печатается посимвольно (строка ширится
    на 2px за кадр) в burst-интервалах (секунды), вне них — тишина с мигающим
    курсором; scroll_at — сдвиг контента вверх на 12px (скролл)."""
    if burst_ranges is None:
        burst_ranges = [(2, 12), (18, 26)]
    screen = np.full((H, W), 230, dtype=np.uint8)
    frames: list[np.ndarray] = []
    line_y, line_x = 20, 10
    for i in range(total_secs * fps):
        t = i / fps
        in_burst = any(a <= t < b for a, b in burst_ranges)
        if in_burst:
            screen[line_y : line_y + 5, line_x : line_x + 2] = 40
            line_x += 2
            if line_x > W - 20:
                line_x = 10
                line_y += 10
        if scroll_at is not None and abs(t - scroll_at) < 1.0 / fps:
            screen = np.roll(screen, -12, axis=0)
            screen[-12:, :] = 230
            line_y = max(10, line_y - 12)
        frame = screen.copy()
        if i % (2 * max(fps // 2, 1)) == 0:  # мигающий курсор ~1 Гц
            frame[line_y : line_y + 5, line_x : line_x + 2] = 40
        frames.append(frame)
    return frames


def speaker_frames(total_secs: int = 20, seed: int = 0) -> list[np.ndarray]:
    """«Говорящая голова»: статичный фон, крупный блоб ходит по кадру."""
    rng = np.random.default_rng(seed)
    bg = np.full((H, W), 150, dtype=np.uint8)
    bg[: H // 3, :] = 170
    frames: list[np.ndarray] = []
    for t in range(total_secs):
        frame = bg.copy()
        cx = 100 + int(60 * np.sin(t / 2.0)) + int(rng.integers(-5, 6))
        frame[50:160, cx : cx + 60] = 80
        frames.append(frame)
    return frames
```

**Step 4: Прогнать тесты**

Run: `uv run pytest tests/unit/frames/test_synthetic_video.py -q`
Expected: 5 passed. Если ассерты по порогам не сходятся — крутить генератор (яркости/размеры), а не тест: тест кодирует сигнатуры, на которые дальше обопрутся политики.

**Step 5: Commit**

```bash
git add tests/support/synthetic_video.py tests/unit/frames/
git commit -m "test(frames): фабрика детерминированных синтетических видео"
```

---

## Задача 3: Порт `SlideImage` — расширение контракта слайдов

Дизайн §11: элемент результата `SlideProvider` → `(path, timestamp | None, caption | None, extracted_text | None)`. Меняем порт и всех потребителей за один проход, документные слайды получают `timestamp=None` и ведут себя как раньше.

**Files:**
- Modify: `lecturelog/domain/ports.py` (класс `SlideProvider`, +dataclass)
- Modify: `lecturelog/infrastructure/slides/document_provider.py`
- Modify: `lecturelog/infrastructure/export/obsidian_exporter.py`
- Modify: `lecturelog/application/pipeline_service.py`
- Modify: тесты `tests/unit/test_document_slide_provider.py`, `test_obsidian_exporter.py`, `test_pipeline_service.py`, `test_pipeline_service_video.py`, `test_ports_contract.py` (все места, где `get_slides`/`export` оперируют `list[Path]`)

**Step 1: Тест на новый контракт**

В `tests/unit/test_document_slide_provider.py` добавить:

```python
async def test_get_slides_returns_slide_images_without_timestamp(tmp_path):
    pdf = _make_pdf(tmp_path, pages=2)  # использовать существующий хелпер файла
    provider = DocumentSlideProvider(slides_path=pdf)
    items = await provider.get_slides(output_dir=tmp_path / "out")
    assert all(item.timestamp is None and item.caption is None for item in items)
    assert [item.path.name for item in items] == ["slide-01.png", "slide-02.png"]
```

Run: `uv run pytest tests/unit/test_document_slide_provider.py -q` → FAIL (`Path` без `.timestamp`).

**Step 2: Порт**

В `lecturelog/domain/ports.py` перед `SlideProvider`:

```python
@dataclass(frozen=True)
class SlideImage:
    """Элемент результата SlideProvider.

    timestamp — секунды от начала видео (None у документных слайдов: у них
    нет таймкода, привязка к секциям делается LLM-матчингом в structurize).
    extracted_text — задел под guide-режим (дизайн §12), в конспекте всегда None."""

    path: Path
    timestamp: float | None = None
    caption: str | None = None
    extracted_text: str | None = None
```

`SlideProvider.get_slides` → `-> list[SlideImage]`, docstring: «Вернуть слайды/кадры. Документы: timestamp=None; видеокадры: timestamp обязателен.»
`Exporter.export` → параметр `slide_images: list[SlideImage]`.

**Step 3: Потребители**

- `document_provider.py`: обернуть возврат — `return [SlideImage(path=p) for p in images]`.
- `obsidian_exporter.py`:
  - копирование: `target = slides_dir / f"slide-{idx + 1:02d}{item.path.suffix}"` (суффикс сохранять: видеокадры кода — PNG, слайды — JPEG);
  - вставка в конспект: alt-текст из подписи —
    ```python
    for slide_idx in section.slide_indices:
        pos = slide_idx - 1
        if 0 <= pos < len(slide_targets):
            rel = slide_targets[pos].relative_to(output_root).as_posix()
            alt = slide_images[pos].caption or f"Слайд {slide_idx}"
            lines.append(f"![{alt}]({rel})")
            lines.append("")
    ```
- `pipeline_service.py`: `slide_images: list[SlideImage]`; в structurize передавать пути: `slide_images=[s.path for s in slide_items]` (переименовать локальную переменную в `slide_items`, чтобы не путать уровни); в export — `slide_images=slide_items`.
- `structure.py` менять НЕ нужно (работает от `slide_targets: list[Path]` из `ExportResult`).

**Step 4: Починить существующие тесты**

Во всех фейках/моках `SlideProvider` и вызовах `export` заменить `list[Path]` на `[SlideImage(path=p) for p in paths]`. Прогнать весь пакет.

Run: `uv run pytest -q`
Expected: все зелёные.

**Step 5: Commit**

```bash
git add -A
git commit -m "refactor(slides): SlideImage — таймстемп/подпись в контракте SlideProvider"
```

---

## Задача 4: Каркас пакета frames — типы и конфиг

**Files:**
- Create: `lecturelog/infrastructure/frames/__init__.py` (пустой)
- Create: `lecturelog/infrastructure/frames/types.py`
- Modify: `lecturelog/config/settings.py` (+`FramesConfig`, поле в `AppConfig`)
- Test: `tests/unit/frames/test_types.py`, дополнение `tests/unit/test_config.py`

**Step 1: Тесты**

```python
# tests/unit/frames/test_types.py
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime


def test_tuning_defaults_are_sane():
    t = FramesTuning()
    assert t.analysis_fps == 1.0 and t.analysis_width == 320
    assert 0 < t.build_containment <= 1.0
    assert t.max_frames <= t.max_candidates


def test_regime_duration():
    r = Regime(start_s=10.0, end_s=40.0, kind="slides")
    assert r.duration_s == 30.0


def test_candidate_ordering_by_ts():
    a, b = Candidate(ts=5.0, kind="slides"), Candidate(ts=2.0, kind="board")
    assert sorted([a, b], key=lambda c: c.ts)[0] is b
```

В `tests/unit/test_config.py` добавить тест (по образцу существующих, с env-моками): `LLM_MODELS_VIDEO_SLIDES` парсится в список, дефолт начинается с `google/gemini-3.1-flash-lite`, `FRAMES_ENABLED` дефолтно `True`.

Run: `uv run pytest tests/unit/frames/test_types.py -q` → FAIL (модуля нет).

**Step 2: Реализация types.py**

```python
# lecturelog/infrastructure/frames/types.py
"""Типы стадии кадров: сигналы, режимы, кандидаты, пороги.

Все численные пороги стадии собраны в FramesTuning — единой точке калибровки
(дизайн §11: «пороги — конфиг с дефолтами»). В коде политик магических чисел нет."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class FramesTuning:
    # A: грубый проход
    analysis_fps: float = 1.0
    analysis_width: int = 320
    # B: сегментация
    window_s: int = 30
    min_regime_s: int = 20
    mad_low: float = 2.0          # плато: средний abs-diff ниже — «ничего не меняется»
    mad_high: float = 20.0        # ступенька: смена слайда/склейка
    micro_area_max: float = 0.03  # доля кадра для «мелкого локализованного диффа» (печать)
    shift_camera: float = 1.5     # px глобальной трансляции → ручная камера/панорама
    # D1: доска
    gate_k: int = 5               # кадров без движения до обновления background model
    ink_delta: int = 12           # порог штриха после нормализации освещения
    board_stable_s: int = 10      # «дописал»: ink стабилен столько секунд
    erase_drop_frac: float = 0.3  # стирание: падение ink за erase_window_s
    erase_window_s: int = 5
    novelty_frac: float = 0.2     # мин. доля нового ink для нового снимка
    board_shift_reset: float = 3.0  # px: едущая доска → сброс модели
    min_ink_px: int = 150         # не снимать почти пустую доску
    # D2: live-coding
    code_fps: float = 4.0
    code_width: int = 480
    edit_area_min: float = 0.0005  # ниже — курсор/шум, не правка
    edit_area_max: float = 0.03
    switch_area_min: float = 0.3   # выше — переключение окна
    edit_burst_s: float = 8.0
    stop_quiet_s: float = 4.0
    pair_window_s: float = 15.0
    pair_settle_s: float = 1.0
    oracle_window_s: float = 10.0
    # D3: слайды
    plateau_min_s: int = 4
    plateau_guard_s: int = 2
    build_containment: float = 0.9
    max_per_regime: int = 20      # cap для слайдов с видео-демо (плато не наступает)
    # E: выемка
    seek_window_s: float = 2.0
    board_rebuild_s: int = 60     # окно full-res реконструкции доски перед кандидатом
    # Общие
    max_candidates: int = 80
    max_frames: int = 60
    vlm_batch: int = 16


@dataclass
class SignalTrack:
    """Пер-кадровые сигналы грубого прохода (1 fps, ~320px). Индекс == секунда."""

    fps: float
    mad: np.ndarray          # float32 [N] средний abs-diff с предыдущим кадром
    motion_frac: np.ndarray  # float32 [N] доля «движущихся» пикселей (бинаризованный дифф)
    edge: np.ndarray         # float32 [N] плотность граней (доля пикселей с сильным градиентом)
    shift: np.ndarray        # float32 [N] |глобальная трансляция| в px (phase correlation)
    dhash: list[int]         # грубая идентичность содержимого

    @property
    def n_frames(self) -> int:
        return len(self.mad)

    def idx_to_ts(self, idx: int) -> float:
        return idx / self.fps


REGIME_KINDS = ("slides", "board", "code", "terminal", "camera", "speaker", "other")


@dataclass
class Regime:
    start_s: float
    end_s: float
    kind: str                      # один из REGIME_KINDS
    bbox: tuple[float, float, float, float] | None = None  # нормализованный (x, y, w, h)
    board_kind: str = "none"       # chalk | marker | none

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass
class Candidate:
    """Момент-кандидат. Для source="board_model" несёт синтезированный кадр
    (реконструкция доски в analysis-разрешении — для дедупа/отладки; финальный
    рендер пересобирает модель в full-res, см. extract)."""

    ts: float
    kind: str
    source: str = "raw_frame"      # raw_frame | board_model
    score: float = 1.0
    regime: Regime | None = None
    image: np.ndarray | None = field(default=None, repr=False)
    pair_ts: float | None = None   # live-coding: ts кадра вывода в паре «код+вывод»
```

**Step 3: Конфиг**

В `settings.py` после `LlmConfig` добавить:

```python
class FramesConfig(BaseSettings):
    # Стадия извлечения кадров из видео (дизайн 2026-07-05-video-frames-design.md).
    # Модели VLM — обычный fallback-список через ModelCooldown; flash-lite первым.
    model_config = _BASE
    enabled: bool = Field(True, alias="FRAMES_ENABLED")
    models_raw: str = Field(
        "google/gemini-3.1-flash-lite,google/gemini-3.5-flash,google/gemini-3-flash-preview",
        alias="LLM_MODELS_VIDEO_SLIDES",
    )
    effort: str = Field("low", alias="LLM_EFFORT_VIDEO_SLIDES")

    @property
    def models(self) -> list[str]:
        return _split_csv(self.models_raw)
```

В `AppConfig`: computed-поле `frames` по образцу остальных + добавить в кортеж `model_post_init`.

**Step 4: Прогнать**

Run: `uv run pytest tests/unit/frames/test_types.py tests/unit/test_config.py -q`
Expected: passed.

**Step 5: Commit**

```bash
git add lecturelog/infrastructure/frames/ lecturelog/config/settings.py tests/
git commit -m "feat(frames): типы стадии кадров и FramesConfig"
```

---## Задача 5: ffmpeg I/O — декод, тумбы, точечная выемка

**Files:**
- Create: `lecturelog/infrastructure/frames/ffmpeg_io.py`
- Test: `tests/unit/frames/test_ffmpeg_io.py`

**Step 1: Тесты**

```python
# tests/unit/frames/test_ffmpeg_io.py
import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import (
    ThumbStore,
    decode_gray,
    decode_window,
    probe_duration,
)
from tests.support.synthetic_video import slides_frames, write_video


def _video(tmp_path, secs=20):
    return write_video(slides_frames(n_slides=2, secs_per_slide=secs // 2), tmp_path / "v.mp4")


def test_probe_duration(tmp_path):
    assert abs(probe_duration(_video(tmp_path)) - 20.0) < 1.5


def test_decode_gray_yields_scaled_frames(tmp_path):
    frames = list(decode_gray(_video(tmp_path), fps=1.0, width=160))
    assert 18 <= len(frames) <= 21
    h, w = frames[0].shape
    assert w == 160 and frames[0].dtype == np.uint8


def test_decode_gray_segment(tmp_path):
    frames = list(decode_gray(_video(tmp_path), fps=1.0, width=160, start_s=5.0, end_s=10.0))
    assert 4 <= len(frames) <= 6


def test_thumb_store_roundtrip(tmp_path):
    store = ThumbStore(tmp_path / "thumbs")
    img = np.full((90, 160), 200, dtype=np.uint8)
    store.put(3, img)
    loaded = store.get(3)
    assert loaded.shape == (90, 160)
    assert abs(float(loaded.mean()) - 200.0) < 3.0  # JPEG с потерями, но близко


def test_decode_window_fullres(tmp_path):
    frames = decode_window(_video(tmp_path), ts=10.0, window_s=2.0, max_fps=5)
    assert len(frames) >= 3
    assert frames[0].shape == (180, 320)  # нативное разрешение синтетики
```

Run: → FAIL (модуля нет).

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/ffmpeg_io.py
"""Декод видео для стадии кадров: rawvideo-пайп ffmpeg → numpy.

Все функции синхронные (CPU-bound): вызывающий код заворачивает их
в asyncio.to_thread. Тумбы хранятся JPEG'ами на диске, чтобы политики
выбирали кадры без ре-декода (дизайн §5.A)."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np


def probe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def _probe_size(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _even(x: int) -> int:
    return x - (x % 2)


def decode_gray(
    video: Path,
    fps: float,
    width: int,
    start_s: float | None = None,
    end_s: float | None = None,
) -> Iterator[np.ndarray]:
    """Прочитать видео (или отрезок) как поток gray-кадров (H, W) uint8."""
    src_w, src_h = _probe_size(video)
    w = _even(min(width, src_w))
    h = _even(round(src_h * w / src_w))
    cmd = ["ffmpeg", "-loglevel", "error"]
    if start_s is not None:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(video)]
    if end_s is not None:
        cmd += ["-t", f"{end_s - (start_s or 0.0):.3f}"]
    cmd += [
        "-vf", f"fps={fps},scale={w}:{h}",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    frame_bytes = w * h
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
    finally:
        proc.stdout.close()
        proc.wait()


def decode_window(video: Path, ts: float, window_s: float, max_fps: int = 10) -> list[np.ndarray]:
    """Точечная выемка: full-res gray кадры в окне ±window_s вокруг ts
    (accurate seek: -ss перед -i у ffmpeg точный с ре-декодом от keyframe)."""
    start = max(0.0, ts - window_s)
    src_w, _ = _probe_size(video)
    return list(decode_gray(video, fps=max_fps, width=src_w,
                            start_s=start, end_s=ts + window_s))


class ThumbStore:
    """JPEG-тумбы кадров грубого прохода: политики D перечитывают кадры
    по индексу без ре-декода видео (~20 КБ × N)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, idx: int, gray: np.ndarray) -> None:
        cv2.imwrite(str(self._root / f"{idx:06d}.jpg"), gray,
                    [cv2.IMWRITE_JPEG_QUALITY, 85])

    def get(self, idx: int) -> np.ndarray:
        img = cv2.imread(str(self._root / f"{idx:06d}.jpg"), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"нет тумбы {idx}")
        return img
```

**Step 3: Прогнать**

Run: `uv run pytest tests/unit/frames/test_ffmpeg_io.py -q`
Expected: passed.

**Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/ffmpeg_io.py tests/unit/frames/test_ffmpeg_io.py
git commit -m "feat(frames): ffmpeg I/O — декод в numpy, тумбы, точечная выемка"
```

---

## Задача 6: Сигналы грубого прохода (стадия A)

**Files:**
- Create: `lecturelog/infrastructure/frames/signals.py`
- Test: `tests/unit/frames/test_signals.py`

**Step 1: Тесты**

```python
# tests/unit/frames/test_signals.py
import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import compute_signals, dhash
from tests.support.synthetic_video import board_frames, slides_frames, typing_frames


def test_dhash_identical_and_different():
    a = np.full((90, 160), 100, dtype=np.uint8)
    b = a.copy(); b[:, :80] = 200
    assert dhash(a) == dhash(a.copy())
    assert dhash(a) != dhash(b)


def test_slides_signature_steps_and_plateaus(tmp_path):
    frames = slides_frames(n_slides=3, secs_per_slide=10)
    track = compute_signals(iter(frames), fps=1.0, thumbs=ThumbStore(tmp_path))
    assert track.n_frames == 30
    # Ступеньки на границах слайдов (кадры 10 и 20), плато внутри
    assert track.mad[10] > 10.0 and track.mad[20] > 10.0
    assert float(np.median(track.mad[3:9])) < 1.0


def test_board_signature_rising_edge(tmp_path):
    frames = board_frames(write_secs=40, erase_at=None, total_secs=40, seed=1)
    track = compute_signals(iter(frames), fps=1.0, thumbs=ThumbStore(tmp_path))
    # Плотность граней растёт по мере накопления штрихов
    assert float(track.edge[5:15].mean()) < float(track.edge[30:40].mean())


def test_typing_signature_small_motion(tmp_path):
    frames = typing_frames(total_secs=30, fps=1, burst_ranges=[(0, 30)], seed=2)
    track = compute_signals(iter(frames), fps=1.0, thumbs=ThumbStore(tmp_path))
    burst = track.motion_frac[2:28]
    assert float(burst.max()) < 0.05  # мелкие локализованные диффы, не крупное движение
    assert float((burst > 0).mean()) > 0.8  # но почти в каждом кадре что-то меняется


def test_thumbs_written(tmp_path):
    frames = slides_frames(n_slides=1, secs_per_slide=5)
    store = ThumbStore(tmp_path)
    compute_signals(iter(frames), fps=1.0, thumbs=store)
    assert store.get(0).shape == frames[0].shape
```

Run: → FAIL.

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/signals.py
"""Стадия A: пер-кадровые сигналы грубого прохода (дизайн §5.A).

Один линейный проход по потоку gray-кадров; всё численно дёшево
(< 1 мин на 5400 кадров 320px). Попутно пишутся JPEG-тумбы."""
from __future__ import annotations

from collections.abc import Iterator

import cv2
import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.types import SignalTrack

_MOTION_THRESH = 15     # порог бинаризации диффа
_EDGE_THRESH = 40.0     # порог магнитуды Sobel для «пикселя-грани»
_DILATE = np.ones((3, 3), np.uint8)


def dhash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int.from_bytes(np.packbits(bits).tobytes(), "big")


def motion_mask(prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
    """Бинаризованный дифф, дилатированный: где движется. Используется и здесь,
    и в background model доски (D1)."""
    diff = cv2.absdiff(cur, prev)
    mask = (diff > _MOTION_THRESH).astype(np.uint8)
    return cv2.dilate(mask, _DILATE).astype(bool)


def _edge_density(gray: np.ndarray) -> float:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    return float((mag > _EDGE_THRESH).mean())


def compute_signals(
    frames: Iterator[np.ndarray], fps: float, thumbs: ThumbStore | None = None
) -> SignalTrack:
    mad: list[float] = []
    motion: list[float] = []
    edge: list[float] = []
    shift: list[float] = []
    hashes: list[int] = []
    prev: np.ndarray | None = None
    prev_f32: np.ndarray | None = None

    for idx, frame in enumerate(frames):
        if thumbs is not None:
            thumbs.put(idx, frame)
        hashes.append(dhash(frame))
        edge.append(_edge_density(frame))
        f32 = frame.astype(np.float32)
        if prev is None:
            mad.append(0.0); motion.append(0.0); shift.append(0.0)
        else:
            mad.append(float(cv2.absdiff(frame, prev).mean()))
            motion.append(float(motion_mask(prev, frame).mean()))
            (dx, dy), _resp = cv2.phaseCorrelate(prev_f32, f32)
            shift.append(float(np.hypot(dx, dy)))
        prev, prev_f32 = frame, f32

    return SignalTrack(
        fps=fps,
        mad=np.asarray(mad, dtype=np.float32),
        motion_frac=np.asarray(motion, dtype=np.float32),
        edge=np.asarray(edge, dtype=np.float32),
        shift=np.asarray(shift, dtype=np.float32),
        dhash=hashes,
    )
```

**Step 3: Прогнать** — `uv run pytest tests/unit/frames/test_signals.py -q` → passed. Пороги `_MOTION_THRESH`/`_EDGE_THRESH` при расхождении на синтетике калибровать здесь, констанами модуля (они не пер-лекционные).

**Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/signals.py tests/unit/frames/test_signals.py
git commit -m "feat(frames): сигналы грубого прохода (mad/motion/edge/shift/dhash)"
```

---

## Задача 7: Сегментация таймлайна на режимы (стадия B)

**Files:**
- Create: `lecturelog/infrastructure/frames/segmentation.py`
- Test: `tests/unit/frames/test_segmentation.py`

**Step 1: Тесты** — работаем с рукотворными `SignalTrack` (быстро и детерминированно):

```python
# tests/unit/frames/test_segmentation.py
import numpy as np

from lecturelog.infrastructure.frames.segmentation import segment_regimes
from lecturelog.infrastructure.frames.types import FramesTuning, SignalTrack


def _track(mad, motion, edge, shift):
    n = len(mad)
    return SignalTrack(
        fps=1.0,
        mad=np.asarray(mad, np.float32),
        motion_frac=np.asarray(motion, np.float32),
        edge=np.asarray(edge, np.float32),
        shift=np.asarray(shift, np.float32),
        dhash=[0] * n,
    )


def test_slides_then_board():
    # 0–120: слайды (плато + ступенька каждые 30с); 120–240: доска (edge растёт)
    mad = [0.1] * 120 + [1.0] * 120
    for i in (30, 60, 90):
        mad[i] = 30.0
    motion = [0.0] * 120 + [0.01] * 120
    edge = [0.05] * 120 + list(np.linspace(0.05, 0.25, 120))
    shift = [0.0] * 240
    regimes = segment_regimes(_track(mad, motion, edge, shift), FramesTuning())
    kinds = [r.kind for r in regimes]
    assert kinds[0] == "slides" and kinds[-1] == "board"
    assert regimes[0].end_s <= 150  # граница около 120с (окно 30с → допуск)


def test_speaker_only_and_camera():
    mad = [5.0] * 60 + [8.0] * 60
    motion = [0.10] * 60 + [0.20] * 60
    edge = [0.08] * 120
    shift = [0.2] * 60 + [4.0] * 60  # вторая половина — панорама
    regimes = segment_regimes(_track(mad, motion, edge, shift), FramesTuning())
    assert regimes[0].kind == "speaker"
    assert regimes[-1].kind == "camera"


def test_short_segments_merged():
    # 10-секундный чужеродный кусок внутри слайдов должен слиться
    mad = [0.1] * 55 + [15.0] * 10 + [0.1] * 55
    motion = [0.0] * 55 + [0.3] * 10 + [0.0] * 55
    edge = [0.05] * 120
    shift = [0.0] * 120
    regimes = segment_regimes(_track(mad, motion, edge, shift), FramesTuning())
    assert all(r.duration_s >= FramesTuning().min_regime_s for r in regimes)
```

Run: → FAIL.

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/segmentation.py
"""Стадия B: сегментация таймлайна на режимы по оконным статистикам (дизайн §5.B).

v1 — правила по сигнатурам, без change-point detection: окна window_s без
перекрытия классифицируются независимо, соседние окна одного типа сливаются,
коротыши (< min_regime_s) поглощаются более длинным соседом."""
from __future__ import annotations

import numpy as np

from lecturelog.infrastructure.frames.types import FramesTuning, Regime, SignalTrack


def _classify_window(
    mad: np.ndarray, motion: np.ndarray, edge: np.ndarray, shift: np.ndarray, t: FramesTuning
) -> str:
    if float(np.mean(shift)) > t.shift_camera:
        return "camera"
    plateau_frac = float(np.mean(mad < t.mad_low))
    steps = int(np.sum(mad > t.mad_high))
    # «Печать»: почти каждый кадр меняется, но движение мелкое и локализованное
    micro_frac = float(np.mean((mad > 0.05) & (motion < t.micro_area_max)))
    edge_slope = float(np.polyfit(np.arange(len(edge)), edge, 1)[0]) if len(edge) > 2 else 0.0
    motion_mean = float(np.mean(motion))

    if micro_frac > 0.5 and steps <= 1 and motion_mean < t.micro_area_max:
        return "code"
    if edge_slope > 1e-4 and motion_mean < 0.15:
        return "board"
    if plateau_frac > 0.6:
        return "slides"
    if motion_mean > 0.03:
        return "speaker"
    return "other"


def segment_regimes(track: SignalTrack, tuning: FramesTuning) -> list[Regime]:
    n = track.n_frames
    win = max(1, int(tuning.window_s * track.fps))
    labels: list[str] = []
    for start in range(0, n, win):
        sl = slice(start, min(start + win, n))
        labels.append(
            _classify_window(track.mad[sl], track.motion_frac[sl],
                             track.edge[sl], track.shift[sl], tuning)
        )

    # Склейка соседних окон одного типа в режимы
    regimes: list[Regime] = []
    for w_idx, kind in enumerate(labels):
        start_s = w_idx * win / track.fps
        end_s = min((w_idx + 1) * win, n) / track.fps
        if regimes and regimes[-1].kind == kind:
            regimes[-1].end_s = end_s
        else:
            regimes.append(Regime(start_s=start_s, end_s=end_s, kind=kind))

    # Поглощение коротышей: сливаем с более длинным соседом, пока все >= min_regime_s
    changed = True
    while changed and len(regimes) > 1:
        changed = False
        for i, r in enumerate(regimes):
            if r.duration_s >= tuning.min_regime_s:
                continue
            left = regimes[i - 1] if i > 0 else None
            right = regimes[i + 1] if i + 1 < len(regimes) else None
            host = max((x for x in (left, right) if x is not None),
                       key=lambda x: x.duration_s)
            if host is left:
                left.end_s = r.end_s
            else:
                right.start_s = r.start_s
            regimes.pop(i)
            changed = True
            break
    return regimes
```

**Step 3: Прогнать** — `uv run pytest tests/unit/frames/test_segmentation.py -q` → passed.

**Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/segmentation.py tests/unit/frames/test_segmentation.py
git commit -m "feat(frames): сегментация таймлайна на режимы по сигнатурам сигналов"
```

---

## Задача 8: VLM №1 — классификация режимов (стадия C)

**Files:**
- Create: `lecturelog/infrastructure/frames/vlm.py`
- Create: `prompts/frames_classify_v1.md`
- Test: `tests/unit/frames/test_vlm_classify.py`

**Step 1: Промпт**

```markdown
Ты анализируешь стопкадры из видеозаписи лекции. Для КАЖДОГО кадра определи:
- "type": что в кадре — "slides" (презентация), "board" (доска), "code" (редактор кода),
  "terminal" (терминал), "speaker" (спикер крупным планом, нет полезного контента),
  "other" (заставка, пустой кадр, «no signal», прочее);
- "content_bbox": [x, y, w, h] — нормализованный (0..1) прямоугольник рабочей области
  (слайд/доска/окно редактора) внутри кадра; если контента нет — null;
- "board_kind": "chalk" (мел, светлое на тёмном), "marker" (маркер, тёмное на светлом),
  "none" (не доска).

Кадры пронумерованы в порядке подачи начиная с 1. Верни СТРОГО JSON-массив:
[{"idx": 1, "type": "...", "content_bbox": [x, y, w, h] | null, "board_kind": "..."}]
Без пояснений и markdown.
```

**Step 2: Тесты**

```python
# tests/unit/frames/test_vlm_classify.py
import json

import numpy as np

from lecturelog.infrastructure.frames.types import FramesTuning, Regime
from lecturelog.infrastructure.frames.vlm import classify_regimes


class FakeLlm:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def call(self, prompt, models, images=None, *, on_usage=None,
                   response_json=False, effort=None, retries=5):
        self.calls.append({"prompt": prompt, "images": images})
        if on_usage is not None:
            await on_usage({"model": models[0], "prompt": 100, "output": 10})
        return self._responses.pop(0)


def _regimes():
    return [
        Regime(0, 60, "slides"),
        Regime(60, 180, "board"),
        Regime(180, 300, "slides"),  # предварительный тип; VLM скажет code
    ]


def _thumb():
    return np.full((90, 160), 128, dtype=np.uint8)


async def test_classify_applies_vlm_verdicts_and_bbox():
    resp = json.dumps([
        {"idx": 1, "type": "slides", "content_bbox": [0.1, 0.1, 0.8, 0.8], "board_kind": "none"},
        {"idx": 2, "type": "board", "content_bbox": [0.0, 0.0, 1.0, 0.9], "board_kind": "chalk"},
        {"idx": 3, "type": "code", "content_bbox": [0.05, 0.0, 0.9, 1.0], "board_kind": "none"},
    ])
    llm = FakeLlm([resp])
    regimes = _regimes()
    out = await classify_regimes(
        llm, ["m"], "low", regimes, [_thumb()] * 3,
        micro_rate=[0.0, 0.0, 0.8], tuning=FramesTuning(), on_usage=None,
    )
    assert [r.kind for r in out] == ["slides", "board", "code"]
    assert out[1].board_kind == "chalk"
    assert out[0].bbox == (0.1, 0.1, 0.8, 0.8)


async def test_tie_breaker_slides_with_code_screenshot():
    # VLM говорит code, но временнáя сигнатура «не печатает» → остаётся slides
    resp = json.dumps([
        {"idx": 1, "type": "code", "content_bbox": [0.1, 0.1, 0.8, 0.8], "board_kind": "none"},
    ])
    out = await classify_regimes(
        FakeLlm([resp]), ["m"], "low", [Regime(0, 60, "slides")], [_thumb()],
        micro_rate=[0.0], tuning=FramesTuning(), on_usage=None,
    )
    assert out[0].kind == "slides"


async def test_implausible_bbox_falls_back_to_none():
    resp = json.dumps([
        {"idx": 1, "type": "slides", "content_bbox": [0.9, 0.9, 0.05, 0.05], "board_kind": "none"},
    ])
    out = await classify_regimes(
        FakeLlm([resp]), ["m"], "low", [Regime(0, 60, "slides")], [_thumb()],
        micro_rate=[0.0], tuning=FramesTuning(), on_usage=None,
    )
    assert out[0].bbox is None  # площадь < 10% — не верим


async def test_batching_over_16_regimes():
    n = 20
    r1 = json.dumps([{"idx": i + 1, "type": "slides", "content_bbox": None,
                      "board_kind": "none"} for i in range(16)])
    r2 = json.dumps([{"idx": i + 1, "type": "slides", "content_bbox": None,
                      "board_kind": "none"} for i in range(4)])
    llm = FakeLlm([r1, r2])
    out = await classify_regimes(
        llm, ["m"], "low", [Regime(i * 30, (i + 1) * 30, "other") for i in range(n)],
        [_thumb()] * n, micro_rate=[0.0] * n, tuning=FramesTuning(), on_usage=None,
    )
    assert len(llm.calls) == 2 and len(out) == n
```

Run: → FAIL.

**Step 3: Реализация (классификация; QC добавится в Задаче 14 в этот же модуль)**

```python
# lecturelog/infrastructure/frames/vlm.py
"""Точки касания VLM (дизайн §5.C, §5.F): классификация режимов и QC кадров.

Оба вызова батчевые (≤ vlm_batch картинок), JSON-mode, flash-lite первым в
fallback-списке. Ошибки VLM НЕ пробрасываются политикам — вызывающий код
(provider) деградирует до временных сигнатур / пропуска QC (дизайн §10)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from lecturelog.infrastructure.frames.types import FramesTuning, Regime

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path("prompts")
_MIN_BBOX_AREA = 0.10


def _encode_jpeg(gray_or_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", gray_or_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("не удалось закодировать кадр в JPEG")
    return buf.tobytes()


def _parse_json(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.startswith("```")).strip()
    return json.loads(text)


def _valid_bbox(bbox: Any) -> tuple[float, float, float, float] | None:
    """Неправдоподобный bbox (площадь < 10% или выход за кадр) → None (полный кадр)."""
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return None
    try:
        x, y, w, h = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
        return None
    if x + w > 1.001 or y + h > 1.001 or w * h < _MIN_BBOX_AREA:
        return None
    return (x, y, w, h)


async def classify_regimes(
    llm: Any,
    models: list[str],
    effort: str,
    regimes: list[Regime],
    rep_frames: list[np.ndarray],
    micro_rate: list[float],
    tuning: FramesTuning,
    on_usage: Any = None,
    prompts_dir: Path = _PROMPTS_DIR,
) -> list[Regime]:
    """VLM уточняет/перебивает предварительный тип из сегментации.

    Тай-брейкер (дизайн §5.C): «слайды с кодом» не печатают — если VLM сказал
    code, а micro_rate режима низкий, оставляем slides."""
    prompt = (prompts_dir / "frames_classify_v1.md").read_text(encoding="utf-8")
    for start in range(0, len(regimes), tuning.vlm_batch):
        batch = regimes[start : start + tuning.vlm_batch]
        images = [_encode_jpeg(f) for f in rep_frames[start : start + tuning.vlm_batch]]
        raw = await llm.call(
            prompt=prompt, models=models, images=images,
            on_usage=on_usage, response_json=True, effort=effort,
        )
        verdicts = _parse_json(raw)
        if not isinstance(verdicts, list):
            raise ValueError("ответ классификации режимов должен быть JSON-массивом")
        by_idx = {int(v.get("idx", 0)): v for v in verdicts if isinstance(v, dict)}
        for offset, regime in enumerate(batch):
            v = by_idx.get(offset + 1)
            if v is None:
                continue
            vlm_kind = str(v.get("type", regime.kind))
            if vlm_kind in ("code", "terminal") and micro_rate[start + offset] < 0.2:
                vlm_kind = regime.kind  # временнáя сигнатура — тай-брейкер
            regime.kind = vlm_kind
            regime.bbox = _valid_bbox(v.get("content_bbox"))
            bk = str(v.get("board_kind", "none"))
            regime.board_kind = bk if bk in ("chalk", "marker") else "none"
    return regimes
```

**Step 4: Прогнать** — `uv run pytest tests/unit/frames/test_vlm_classify.py -q` → passed.

**Step 5: Commit**

```bash
git add lecturelog/infrastructure/frames/vlm.py prompts/frames_classify_v1.md tests/unit/frames/test_vlm_classify.py
git commit -m "feat(frames): VLM-классификация режимов с bbox и тай-брейкером"
```

---

## Задача 9: Background model доски (фундамент D1)

**Files:**
- Create: `lecturelog/infrastructure/frames/board.py` (начало)
- Test: `tests/unit/frames/test_board_model.py`

**Step 1: Тесты**

```python
# tests/unit/frames/test_board_model.py
import numpy as np

from lecturelog.infrastructure.frames.board import BackgroundModel
from lecturelog.infrastructure.frames.signals import motion_mask
from tests.support.synthetic_video import board_frames


def test_model_reconstructs_board_without_teacher():
    frames = board_frames(write_secs=40, erase_at=None, total_secs=60,
                          with_teacher=True, seed=1)
    clean = board_frames(write_secs=40, erase_at=None, total_secs=60,
                         with_teacher=False, seed=1)
    model = BackgroundModel(frames[0], gate_k=5)
    for prev, cur in zip(frames, frames[1:]):
        m = model.update(cur, motion_mask(prev, cur))
    # Модель ближе к чистой доске, чем сырой кадр (препод стёрт)
    err_model = np.abs(m.astype(int) - clean[-1].astype(int)).mean()
    err_raw = np.abs(frames[-1].astype(int) - clean[-1].astype(int)).mean()
    assert err_model < err_raw * 0.5


def test_stationary_teacher_freezes_region_not_corrupts():
    base = np.full((90, 160), 40, dtype=np.uint8)
    base[30:33, 10:60] = 210  # штрих
    with_teacher = base.copy()
    with_teacher[20:70, 80:110] = 110  # препод встал и замер
    model = BackgroundModel(base, gate_k=5)
    prev = base
    for _ in range(30):  # стоит неподвижно 30 кадров
        model.update(with_teacher, motion_mask(prev, with_teacher))
        prev = with_teacher
    # ВАЖНО: неподвижный препод в итоге въедет в модель (это честно — гейт
    # по движению, не по семантике), но штрих вне препода не пострадал
    m = model.snapshot()
    assert (m[30:33, 10:60] > 180).all()
```

Run: → FAIL.

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/board.py
"""Стадия D1: политика доски — background model, ink-метрики, «дописал» (дизайн §5.D1).

Ключевое: все метрики считаются ПО МОДЕЛИ, а не по сырым кадрам — вставший
перед доской препод иначе выглядит как стирание."""
from __future__ import annotations

import numpy as np


class BackgroundModel:
    """Реконструкция доски без человека: пиксель обновляется, только если
    в нём не было движения gate_k кадров подряд. Наивная медиана ломается,
    когда препод стоит на месте дольше полуокна, — гейт по движению нет."""

    def __init__(self, first: np.ndarray, gate_k: int = 5) -> None:
        self._model = first.copy()
        self._still = np.zeros(first.shape, dtype=np.int32)
        self._gate_k = gate_k

    def update(self, frame: np.ndarray, motion: np.ndarray) -> np.ndarray:
        self._still = np.where(motion, 0, self._still + 1)
        ready = self._still >= self._gate_k
        self._model[ready] = frame[ready]
        return self._model

    def snapshot(self) -> np.ndarray:
        return self._model.copy()

    def reset(self, frame: np.ndarray) -> None:
        """Сброс при смене поверхности (едущая доска, панорама)."""
        self._model = frame.copy()
        self._still[:] = 0
```

**Step 3: Прогнать** — passed. **Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/board.py tests/unit/frames/test_board_model.py
git commit -m "feat(frames): background model доски с гейтом по движению"
```

---

## Задача 10: Политика доски — ink, «дописал», стирание (D1)

**Files:**
- Modify: `lecturelog/infrastructure/frames/board.py`
- Test: `tests/unit/frames/test_board_policy.py`

**Step 1: Тесты**

```python
# tests/unit/frames/test_board_policy.py
from lecturelog.infrastructure.frames.board import board_candidates
from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import compute_signals
from lecturelog.infrastructure.frames.types import FramesTuning, Regime
from tests.support.synthetic_video import board_frames


def _prepare(tmp_path, **kw):
    frames = board_frames(**kw)
    store = ThumbStore(tmp_path / "thumbs")
    track = compute_signals(iter(frames), fps=1.0, thumbs=store)
    regime = Regime(0.0, float(len(frames)), "board", board_kind="chalk")
    return track, store, regime


def test_written_pause_produces_candidate(tmp_path):
    # Пишет 30с, потом 25с ничего не меняется → один кандидат «дописал»
    track, store, regime = _prepare(
        tmp_path, write_secs=30, erase_at=None, total_secs=55, seed=1)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 1
    assert 30 <= cands[0].ts <= 50  # после остановки письма, с учётом окна стабильности
    assert cands[0].source == "board_model" and cands[0].image is not None


def test_erase_snapshots_last_stable_state(tmp_path):
    # Пишет 40с, на 50-й стирание → кандидат пред-стирания с ts до 50с
    track, store, regime = _prepare(
        tmp_path, write_secs=40, erase_at=50, total_secs=70, seed=2)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert any(c.ts < 50 for c in cands)


def test_no_candidates_on_empty_board(tmp_path):
    track, store, regime = _prepare(
        tmp_path, write_secs=0, erase_at=None, total_secs=40, seed=3)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert cands == []  # min_ink_px: пустую доску не снимаем


def test_novelty_gate_no_duplicate_shots(tmp_path):
    # Одна доска, две длинные паузы БЕЗ дописывания между ними → один кандидат
    track, store, regime = _prepare(
        tmp_path, write_secs=25, erase_at=None, total_secs=80, seed=4)
    cands = board_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 1
```

Run: → FAIL.

**Step 2: Реализация** — дописать в `board.py`:

```python
import cv2

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import motion_mask
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime


def ink_mask(model: np.ndarray, board_kind: str, delta: int) -> np.ndarray:
    """Штриховые пиксели: нормализация освещения делением на сильно размытую
    версию, затем порог с полярностью по типу доски (мел — светлое на тёмном)."""
    blur = cv2.GaussianBlur(model, (0, 0), sigmaX=15)
    norm = cv2.divide(model.astype(np.float32), np.maximum(blur, 1).astype(np.float32))
    if board_kind == "chalk":
        return norm > 1.0 + delta / 128.0
    return norm < 1.0 - delta / 128.0


def _roi_slice(shape: tuple[int, int], bbox) -> tuple[slice, slice]:
    if bbox is None:
        return slice(None), slice(None)
    h, w = shape
    x, y, bw, bh = bbox
    return (slice(int(y * h), int((y + bh) * h)), slice(int(x * w), int((x + bw) * w)))


def board_candidates(
    regime: Regime, track, store: ThumbStore, tuning: FramesTuning
) -> list[Candidate]:
    """Проход по кадрам режима доски: копим модель, следим за ink(t).

    Кандидат «дописал»: ink стабилен >= board_stable_s И вырос на novelty_frac
    с прошлого снимка. Стирание: падение ink на erase_drop_frac за erase_window_s
    → фиксируем последнее стабильное состояние ДО падения (оно уже в модели)."""
    i0 = int(regime.start_s * track.fps)
    i1 = int(regime.end_s * track.fps)
    if i1 - i0 < 2:
        return []
    ys, xs = _roi_slice(store.get(i0).shape, regime.bbox)
    kind = regime.board_kind if regime.board_kind != "none" else "chalk"

    model = BackgroundModel(store.get(i0), gate_k=tuning.gate_k)
    prev = store.get(i0)
    candidates: list[Candidate] = []
    ink_hist: list[int] = []
    last_shot_ink: np.ndarray | None = None
    stable_run = 0
    # Последнее стабильное состояние — для снимка пред-стирания
    last_stable: tuple[float, np.ndarray, np.ndarray] | None = None  # (ts, model, ink)

    def emit(ts: float, snap: np.ndarray, ink: np.ndarray, score: float) -> None:
        nonlocal last_shot_ink
        candidates.append(Candidate(ts=ts, kind="board", source="board_model",
                                    score=score, regime=regime, image=snap))
        last_shot_ink = ink

    for i in range(i0 + 1, i1):
        cur = store.get(i)
        m = model.update(cur, motion_mask(prev, cur))
        prev = cur
        if track.shift[i] > tuning.board_shift_reset:
            # Едущая доска/панорама: зафиксировать накопленное и сбросить модель
            if last_stable is not None and _is_novel(last_stable[2], last_shot_ink, tuning):
                emit(last_stable[0], last_stable[1], last_stable[2], score=1.0)
            model.reset(cur)
            ink_hist.clear(); stable_run = 0; last_stable = None
            continue

        ink = ink_mask(m[ys, xs], kind, tuning.ink_delta)
        count = int(ink.sum())
        ink_hist.append(count)
        ts = i / track.fps

        # Стирание: резкое падение против максимума недавнего окна
        win = ink_hist[-(tuning.erase_window_s + 1):]
        if (len(win) > tuning.erase_window_s and max(win) > tuning.min_ink_px
                and count < max(win) * (1 - tuning.erase_drop_frac)):
            if last_stable is not None and _is_novel(last_stable[2], last_shot_ink, tuning):
                emit(last_stable[0], last_stable[1], last_stable[2], score=1.2)
            ink_hist.clear(); stable_run = 0; last_stable = None
            continue

        # Стабильность: |Δink| в пределах эпсилона
        if len(ink_hist) >= 2 and abs(ink_hist[-1] - ink_hist[-2]) <= max(
                2, int(0.02 * max(count, 1))):
            stable_run += 1
        else:
            stable_run = 0

        if stable_run >= tuning.board_stable_s and count >= tuning.min_ink_px:
            last_stable = (ts, model.snapshot(), ink)
            if _is_novel(ink, last_shot_ink, tuning):
                emit(ts, model.snapshot(), ink, score=1.0)
            stable_run = 0  # не эмитить каждую секунду той же стабильности

    # Хвост режима: доска, дописанная к самому концу (граница режима — снимаем)
    if last_stable is not None and _is_novel(last_stable[2], last_shot_ink, tuning):
        emit(last_stable[0], last_stable[1], last_stable[2], score=0.9)
    return candidates


def _is_novel(ink: np.ndarray, last_shot: np.ndarray | None, tuning: FramesTuning) -> bool:
    """Новизна: >= novelty_frac нового ink с прошлого снимка (иначе — дубль доски)."""
    total = int(ink.sum())
    if total < tuning.min_ink_px:
        return False
    if last_shot is None:
        return True
    new = int((ink & ~last_shot).sum())
    return new / max(total, 1) >= tuning.novelty_frac
```

**Step 3: Прогнать** — `uv run pytest tests/unit/frames/test_board_policy.py -q`. Это самый калибровочный тест плана: при расхождениях крутить пороги `FramesTuning` (или генератор), фиксируя инварианты тестов. Ожидаемо passed.

**Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/board.py tests/unit/frames/test_board_policy.py
git commit -m "feat(frames): политика доски — ink, «дописал», стирание, новизна"
```

---

## Задача 11: Политика слайдов — плато и builds (D3)

**Files:**
- Create: `lecturelog/infrastructure/frames/slides_policy.py`
- Test: `tests/unit/frames/test_slides_policy.py`

**Step 1: Тесты**

```python
# tests/unit/frames/test_slides_policy.py
from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.signals import compute_signals
from lecturelog.infrastructure.frames.slides_policy import slide_candidates
from lecturelog.infrastructure.frames.types import FramesTuning, Regime
from tests.support.synthetic_video import slides_frames


def _prepare(tmp_path, **kw):
    frames = slides_frames(**kw)
    store = ThumbStore(tmp_path / "t")
    track = compute_signals(iter(frames), fps=1.0, thumbs=store)
    return track, store, Regime(0.0, float(len(frames)), "slides")


def test_one_candidate_per_slide(tmp_path):
    track, store, regime = _prepare(tmp_path, n_slides=3, secs_per_slide=15)
    cands = slide_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 3
    # Кандидат — конец плато с отступом guard от следующей ступеньки
    assert 10 <= cands[0].ts <= 13


def test_builds_dedup_keeps_final_version(tmp_path):
    # 2 слайда с builds: плато «до» и «после» билда, дедуп по вложенности масок
    track, store, regime = _prepare(tmp_path, n_slides=2, secs_per_slide=20, builds=True)
    cands = slide_candidates(regime, track, store, FramesTuning())
    assert len(cands) == 2  # по одному на слайд — финальные версии builds
    # Финальная версия — из второй половины слайда (после билда)
    assert cands[0].ts >= 10


def test_cap_per_regime(tmp_path):
    tuning = FramesTuning(max_per_regime=2)
    track, store, regime = _prepare(tmp_path, n_slides=5, secs_per_slide=10)
    cands = slide_candidates(regime, track, store, tuning)
    assert len(cands) <= 2
```

Run: → FAIL.

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/slides_policy.py
"""Стадия D3: политика слайдов — плато и дедуп прогрессивных builds (дизайн §5.D3)."""
from __future__ import annotations

import cv2
import numpy as np

from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime

_EDGE_THRESH = 40.0


def _content_mask(gray: np.ndarray) -> np.ndarray:
    """Бинаризованная маска контента по градиентам — общая для светлых и тёмных тем."""
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy) > _EDGE_THRESH


def _plateau_runs(mad: np.ndarray, low: float, min_len: int) -> list[tuple[int, int]]:
    """Индексные интервалы [a, b) с mad < low длиной >= min_len."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(mad):
        if v < low and start is None:
            start = i
        elif v >= low and start is not None:
            if i - start >= min_len:
                runs.append((start, i))
            start = None
    if start is not None and len(mad) - start >= min_len:
        runs.append((start, len(mad)))
    return runs


def slide_candidates(
    regime: Regime, track, store: ThumbStore, tuning: FramesTuning
) -> list[Candidate]:
    i0 = int(regime.start_s * track.fps)
    i1 = int(regime.end_s * track.fps)
    mad = track.mad[i0:i1]
    min_len = max(1, int(tuning.plateau_min_s * track.fps))
    guard = max(1, int(tuning.plateau_guard_s * track.fps))

    raw: list[Candidate] = []
    for a, b in _plateau_runs(mad, tuning.mad_low, min_len):
        # Последний кадр плато, но >= guard до следующей ступеньки (transition-смаз)
        idx = max(a, b - 1 - guard) + i0
        raw.append(Candidate(ts=idx / track.fps, kind="slides", regime=regime))

    # Дедуп builds: вложенность масок контента — |prev ∧ next| / |prev| > порога
    # и контент вырос → это build того же слайда, держим позднюю (финальную) версию.
    deduped: list[Candidate] = []
    prev_mask: np.ndarray | None = None
    for cand in raw:
        mask = _content_mask(store.get(int(cand.ts * track.fps)))
        if prev_mask is not None:
            containment = float((prev_mask & mask).sum()) / max(int(prev_mask.sum()), 1)
            if containment > tuning.build_containment and mask.sum() >= prev_mask.sum():
                deduped[-1] = cand  # поздний вытесняет ранний
                prev_mask = mask
                continue
        deduped.append(cand)
        prev_mask = mask

    # Cap на режим: слайд со встроенным видео/демо может дать шквал плато
    if len(deduped) > tuning.max_per_regime:
        deduped = deduped[: tuning.max_per_regime]
    return deduped
```

**Step 3: Прогнать** → passed. **Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/slides_policy.py tests/unit/frames/test_slides_policy.py
git commit -m "feat(frames): политика слайдов — плато и дедуп builds"
```

---

## Задача 12: Политика live-coding — точки остановки (D2)

**Files:**
- Create: `lecturelog/infrastructure/frames/coding.py`
- Test: `tests/unit/frames/test_coding_policy.py`

**Step 1: Тесты** — политика работает на ПЕРЕДЕКОДИРОВАННЫХ кадрах режима (code_fps), поэтому тестируем функцию от списка кадров, без видеофайла:

```python
# tests/unit/frames/test_coding_policy.py
from lecturelog.infrastructure.frames.coding import coding_candidates_from_frames
from lecturelog.infrastructure.frames.types import FramesTuning, Regime
from tests.support.synthetic_video import typing_frames

FPS = 4


def _cands(frames, srt_blocks=(), start_s=0.0):
    regime = Regime(start_s, start_s + len(frames) / FPS, "code")
    return coding_candidates_from_frames(
        frames, fps=FPS, regime=regime, tuning=FramesTuning(),
        srt_blocks=list(srt_blocks),
    )


def test_stop_point_after_burst():
    # Печать 2–12с, тишина 12–30с → одна точка остановки ~13–17с
    frames = typing_frames(total_secs=30, fps=FPS, burst_ranges=[(2, 12)], seed=1)
    cands = _cands(frames)
    assert len(cands) == 1
    assert 12 <= cands[0].ts <= 18


def test_cursor_blink_is_not_edit():
    # Только курсор мигает — вообще нет кандидатов (нет edit-burst)
    frames = typing_frames(total_secs=20, fps=FPS, burst_ranges=[], seed=2)
    assert _cands(frames) == []


def test_two_bursts_two_candidates():
    frames = typing_frames(total_secs=40, fps=FPS,
                           burst_ranges=[(2, 12), (20, 32)], seed=3)
    cands = _cands(frames)
    assert len(cands) == 2


def test_transcript_trigger_boosts_score():
    frames = typing_frames(total_secs=30, fps=FPS, burst_ranges=[(2, 12)], seed=4)
    plain = _cands(frames)[0]
    boosted = _cands(frames, srt_blocks=[(13.0, "а теперь запустим и посмотрим")])[0]
    assert boosted.score > plain.score


def test_scroll_mid_is_not_candidate():
    # Скролл в середине тишины не должен породить кандидата и не должен
    # сбросить уже найденную точку остановки
    frames = typing_frames(total_secs=30, fps=FPS, burst_ranges=[(2, 12)],
                           scroll_at=20, seed=5)
    cands = _cands(frames)
    assert len(cands) == 1 and cands[0].ts < 20
```

Run: → FAIL.

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/coding.py
"""Стадия D2: live-coding — «точки остановки» (дизайн §5.D2).

Режимы code передекодируются на code_fps (см. provider): 1 fps не видит
паттерн печати. Здесь — чистая логика от списка кадров: state machine
edit-burst → тишина → кандидат; транскрипт — бесплатный оракул (буст score)."""
from __future__ import annotations

import cv2
import numpy as np

from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime

_DIFF_THRESH = 20

# Словарь триггеров завершённости из транскрипта (дизайн §5.D2)
_TRIGGERS = (
    "запустим", "запускаем", "скомпилируем", "компилируем", "сохраняем",
    "сохраним", "вот и всё", "вот и все", "готово", "смотрите, что получилось",
    "посмотрим, что получилось", "выполним", "проверим",
)


def _area_frac(prev: np.ndarray, cur: np.ndarray) -> float:
    return float((cv2.absdiff(cur, prev) > _DIFF_THRESH).mean())


def _vertical_shift(prev: np.ndarray, cur: np.ndarray) -> float:
    (_dx, dy), _ = cv2.phaseCorrelate(prev.astype(np.float32), cur.astype(np.float32))
    return abs(float(dy))


def coding_candidates_from_frames(
    frames: list[np.ndarray],
    fps: float,
    regime: Regime,
    tuning: FramesTuning,
    srt_blocks: list[tuple[float, str]],
) -> list[Candidate]:
    """srt_blocks — [(start_sec, text)] реплики транскрипта (для оракула)."""
    if len(frames) < 3:
        return []
    burst_frames_needed = tuning.edit_burst_s * fps
    quiet_frames_needed = tuning.stop_quiet_s * fps

    candidates: list[Candidate] = []
    edit_accum = 0.0      # накопленные кадры-правки текущего burst
    quiet_run = 0.0
    burst_done = False    # был ли burst, ждущий точку остановки
    last_switch_ts: float | None = None

    for i in range(1, len(frames)):
        ts = regime.start_s + i / fps
        area = _area_frac(frames[i - 1], frames[i])
        is_scroll = area > tuning.edit_area_max and _vertical_shift(
            frames[i - 1], frames[i]) > 2.0

        if area >= tuning.switch_area_min and not is_scroll:
            # Переключение окна: буст последнего кандидата + кадр вывода (пара)
            last_switch_ts = ts
            if candidates and ts - candidates[-1].ts <= tuning.pair_window_s:
                candidates[-1].score += 1.0
                candidates[-1].pair_ts = ts + tuning.pair_settle_s
            edit_accum = 0.0; quiet_run = 0.0; burst_done = False
        elif is_scroll:
            # Середина скролла — не кандидат и не тишина; burst не сбрасываем
            quiet_run = 0.0
        elif tuning.edit_area_min <= area <= tuning.edit_area_max:
            edit_accum += 1
            quiet_run = 0.0
            if edit_accum >= burst_frames_needed:
                burst_done = True
        elif area < tuning.edit_area_min:
            quiet_run += 1
            if burst_done and quiet_run >= quiet_frames_needed:
                cand_ts = ts - quiet_run / fps  # начало тишины = точка остановки
                score = 1.0 + _oracle_boost(cand_ts, srt_blocks, tuning)
                candidates.append(Candidate(ts=cand_ts, kind="code",
                                            regime=regime, score=score))
                burst_done = False
                edit_accum = 0.0
    _ = last_switch_ts
    return candidates


def _oracle_boost(ts: float, srt_blocks: list[tuple[float, str]], t: FramesTuning) -> float:
    """Триггер («запустим», «сохраняем»…) в окне ±oracle_window_s → +0.5 к score."""
    for block_ts, text in srt_blocks:
        if abs(block_ts - ts) <= t.oracle_window_s:
            lowered = text.lower()
            if any(trig in lowered for trig in _TRIGGERS):
                return 0.5
    return 0.0
```

**Step 3: Прогнать** → passed (пороги `edit_area_*` калибровать по синтетике при необходимости).

**Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/coding.py tests/unit/frames/test_coding_policy.py
git commit -m "feat(frames): политика live-coding — edit-burst, точки остановки, оракул"
```

---

## Задача 13: Качественная выемка стопкадров (стадия E)

**Files:**
- Create: `lecturelog/infrastructure/frames/extract.py`
- Test: `tests/unit/frames/test_extract.py`

**Step 1: Тесты**

```python
# tests/unit/frames/test_extract.py
import numpy as np

from lecturelog.infrastructure.frames.extract import (
    render_candidates,
    sharpest_frame,
    whiteboard_cleanup,
)
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime
from tests.support.synthetic_video import board_frames, slides_frames, write_video


def test_sharpest_frame_picks_non_blurred():
    import cv2
    sharp = slides_frames(n_slides=1, secs_per_slide=1)[0]
    blurred = cv2.GaussianBlur(sharp, (11, 11), 5)
    assert sharpest_frame([blurred, sharp, blurred]) is sharp


def test_whiteboard_cleanup_marker_whitens_background():
    img = np.full((90, 160), 180, dtype=np.uint8)  # сероватый фон
    img[40:43, 20:100] = 60  # штрих маркером
    out = whiteboard_cleanup(img, "marker")
    assert float(np.median(out)) > 200  # фон побелел
    assert out[41, 50] < 128  # штрих остался тёмным


def test_render_candidates_formats(tmp_path):
    video = write_video(slides_frames(n_slides=2, secs_per_slide=10),
                        tmp_path / "v.mp4")
    cands = [
        Candidate(ts=5.0, kind="slides", regime=Regime(0, 20, "slides")),
        Candidate(ts=15.0, kind="code", regime=Regime(0, 20, "code")),
    ]
    frames = render_candidates(video, cands, tmp_path / "out", FramesTuning())
    # Слайды — JPEG q90, код — PNG (JPEG-артефакты убивают мелкий текст)
    assert frames[0].path.suffix == ".jpg" and frames[1].path.suffix == ".png"
    assert all(f.path.exists() for f in frames)
    assert frames[0].timestamp == 5.0


def test_render_board_from_model_snapshot(tmp_path):
    video = write_video(board_frames(write_secs=20, erase_at=None, total_secs=30),
                        tmp_path / "v.mp4")
    snap = board_frames(write_secs=20, erase_at=None, total_secs=30,
                        with_teacher=False)[-1]
    cands = [Candidate(ts=25.0, kind="board", source="board_model", image=snap,
                       regime=Regime(0, 30, "board", board_kind="chalk"))]
    frames = render_candidates(video, cands, tmp_path / "out", FramesTuning())
    assert len(frames) == 1 and frames[0].path.suffix == ".png"
```

Run: → FAIL.

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/extract.py
"""Стадия E: качественная выемка стопкадров (дизайн §5.E).

Отобранный ts — момент; кадр выбирается отдельно: точечный accurate-seek,
внутри окна ±seek_window_s — кадр максимальной резкости. Доска отдаётся из
background model: full-res реконструкция хвостового окна перед кандидатом
(человек стёрт) + whiteboard cleanup."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from lecturelog.infrastructure.frames.board import BackgroundModel
from lecturelog.infrastructure.frames.ffmpeg_io import decode_gray, decode_window, _probe_size
from lecturelog.infrastructure.frames.signals import motion_mask
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning
from lecturelog.domain.ports import SlideImage


def sharpest_frame(frames: list[np.ndarray]) -> np.ndarray:
    """Максимальная резкость = variance of Laplacian; I-frames выигрывают сами."""
    return max(frames, key=lambda f: float(cv2.Laplacian(f, cv2.CV_64F).var()))


def whiteboard_cleanup(gray: np.ndarray, board_kind: str) -> np.ndarray:
    """Маркер: деление на размытый фон → фон белеет, штрихи контрастнее.
    Мел: CLAHE — локальный контраст читается заметно лучше."""
    if board_kind == "chalk":
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        return clahe.apply(gray)
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=25)
    norm = cv2.divide(gray.astype(np.float32), np.maximum(blur, 1).astype(np.float32))
    return np.clip(norm * 230.0, 0, 255).astype(np.uint8)


def _rebuild_board_fullres(video: Path, cand: Candidate, tuning: FramesTuning) -> np.ndarray:
    """Full-res background model по хвостовому окну перед кандидатом:
    analysis-модель (320px) хранится в cand.image как fallback."""
    start = max(0.0, cand.ts - tuning.board_rebuild_s)
    src_w, _ = _probe_size(video)
    frames = decode_gray(video, fps=1.0, width=src_w, start_s=start, end_s=cand.ts + 1.0)
    model: BackgroundModel | None = None
    prev: np.ndarray | None = None
    for frame in frames:
        if model is None:
            model = BackgroundModel(frame, gate_k=tuning.gate_k)
        else:
            model.update(frame, motion_mask(prev, frame))
        prev = frame
    if model is None:  # видео короче окна — деградация на analysis-снимок
        return cand.image
    return model.snapshot()


def render_candidates(
    video: Path, candidates: list[Candidate], out_dir: Path, tuning: FramesTuning
) -> list[SlideImage]:
    """Кандидаты (по ts) → файлы кадров. Код — PNG в нативном разрешении,
    слайды — JPEG q90, доска — PNG из модели + cleanup."""
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[SlideImage] = []
    ordered = sorted(candidates, key=lambda c: c.ts)
    idx = 0
    for cand in ordered:
        idx += 1
        items.append(_render_one(video, cand, cand.ts, out_dir, idx, tuning))
        if cand.pair_ts is not None:
            # Пара «код+вывод»: второй кадр после переключения окна
            idx += 1
            paired = Candidate(ts=cand.pair_ts, kind=cand.kind,
                               regime=cand.regime, source="raw_frame")
            items.append(_render_one(video, paired, cand.pair_ts, out_dir, idx, tuning))
    return items


def _render_one(
    video: Path, cand: Candidate, ts: float, out_dir: Path, idx: int, tuning: FramesTuning
) -> SlideImage:
    if cand.source == "board_model":
        kind = cand.regime.board_kind if cand.regime else "chalk"
        img = whiteboard_cleanup(_rebuild_board_fullres(video, cand, tuning), kind)
        path = out_dir / f"frame-{idx:02d}-board.png"
        cv2.imwrite(str(path), img)
        return SlideImage(path=path, timestamp=ts)

    window = decode_window(video, ts, tuning.seek_window_s)
    img = sharpest_frame(window) if window else None
    if img is None:
        raise RuntimeError(f"не удалось вынуть кадр на ts={ts}")
    if cand.kind in ("code", "terminal"):
        path = out_dir / f"frame-{idx:02d}-{cand.kind}.png"
        cv2.imwrite(str(path), img)  # PNG: JPEG-артефакты убивают мелкий текст
    else:
        path = out_dir / f"frame-{idx:02d}-{cand.kind}.jpg"
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return SlideImage(path=path, timestamp=ts)
```

**Step 3: Прогнать** → passed. **Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/extract.py tests/unit/frames/test_extract.py
git commit -m "feat(frames): выемка стопкадров — резкость, board-композит, PNG/JPEG"
```

---

## Задача 14: VLM №2 — QC и подписи (стадия F)

**Files:**
- Modify: `lecturelog/infrastructure/frames/vlm.py`
- Create: `prompts/frames_qc_v1.md`
- Test: `tests/unit/frames/test_vlm_qc.py`

**Step 1: Промпт**

```markdown
Ты — контроль качества стопкадров из видео лекции. Для каждого кадра даны его номер,
таймкод и ближайшая реплика транскрипта. Реши для КАЖДОГО кадра:
- "keep": true/false — false для мусора: смаз, полустёртая доска, дубль соседнего кадра,
  пустой кадр / «no signal», крупный план спикера без контента;
- "caption": короткая (до 15 слов) подпись по-русски, что на кадре (null если keep=false);
- "dup_group": номер группы семантических дублей (кадры одного и того же состояния
  экрана/доски получают одинаковый номер; уникальные кадры — null).

Верни СТРОГО JSON-массив: [{"idx": 1, "keep": true, "caption": "...", "dup_group": null}]
Без пояснений и markdown.
```

**Step 2: Тесты**

```python
# tests/unit/frames/test_vlm_qc.py
import json

from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.frames.types import FramesTuning
from lecturelog.infrastructure.frames.vlm import qc_frames
from tests.unit.frames.test_vlm_classify import FakeLlm


def _items(tmp_path, n):
    import cv2
    import numpy as np
    items = []
    for i in range(n):
        p = tmp_path / f"frame-{i:02d}.jpg"
        cv2.imwrite(str(p), np.full((90, 160), 100 + i, dtype=np.uint8))
        items.append(SlideImage(path=p, timestamp=float(i * 60)))
    return items


async def test_qc_drops_garbage_and_captions(tmp_path):
    resp = json.dumps([
        {"idx": 1, "keep": True, "caption": "Титульный слайд", "dup_group": None},
        {"idx": 2, "keep": False, "caption": None, "dup_group": None},
        {"idx": 3, "keep": True, "caption": "Схема архитектуры", "dup_group": None},
    ])
    out = await qc_frames(FakeLlm([resp]), ["m"], "low", _items(tmp_path, 3),
                          srt_text_at=lambda ts: "реплика", tuning=FramesTuning())
    assert len(out) == 2
    assert out[0].caption == "Титульный слайд"


async def test_qc_dedup_groups_keep_first(tmp_path):
    resp = json.dumps([
        {"idx": 1, "keep": True, "caption": "Доска: определение", "dup_group": 1},
        {"idx": 2, "keep": True, "caption": "Доска: определение (дубль)", "dup_group": 1},
        {"idx": 3, "keep": True, "caption": "Код примера", "dup_group": None},
    ])
    out = await qc_frames(FakeLlm([resp]), ["m"], "low", _items(tmp_path, 3),
                          srt_text_at=lambda ts: "", tuning=FramesTuning())
    assert len(out) == 2  # из группы дублей остаётся один (лучший = первый keep)


async def test_qc_malformed_response_keeps_all(tmp_path):
    out = await qc_frames(FakeLlm(["не json"]), ["m"], "low", _items(tmp_path, 2),
                          srt_text_at=lambda ts: "", tuning=FramesTuning())
    assert len(out) == 2  # деградация: кадры чуть грязнее, но стадия работает
```

Run: → FAIL.

**Step 3: Реализация** — дописать в `vlm.py`:

```python
from collections.abc import Callable

from lecturelog.domain.ports import SlideImage


async def qc_frames(
    llm: Any,
    models: list[str],
    effort: str,
    items: list[SlideImage],
    srt_text_at: Callable[[float], str],
    tuning: FramesTuning,
    on_usage: Any = None,
    prompts_dir: Path = _PROMPTS_DIR,
) -> list[SlideImage]:
    """QC + подписи (дизайн §5.F): keep/drop, caption, группы семантических
    дублей (из группы остаётся первый keep). Ошибка парсинга батча —
    деградация: батч возвращается как есть, без подписей."""
    base_prompt = (prompts_dir / "frames_qc_v1.md").read_text(encoding="utf-8")
    result: list[SlideImage] = []
    for start in range(0, len(items), tuning.vlm_batch):
        batch = items[start : start + tuning.vlm_batch]
        legend = "\n".join(
            f"{i + 1}. ts={item.timestamp:.0f}с; реплика: «{srt_text_at(item.timestamp)}»"
            for i, item in enumerate(batch)
        )
        images = [_encode_jpeg(cv2.imread(str(item.path))) for item in batch]
        try:
            raw = await llm.call(
                prompt=f"{base_prompt}\n\nКадры:\n{legend}", models=models,
                images=images, on_usage=on_usage, response_json=True, effort=effort,
            )
            verdicts = _parse_json(raw)
            by_idx = {int(v["idx"]): v for v in verdicts if isinstance(v, dict)}
        except Exception as error:  # noqa: BLE001 — деградация QC на любом сбое батча
            logger.warning("QC-батч не распарсен (%s): кадры остаются без QC", error)
            result.extend(batch)
            continue
        seen_groups: set[int] = set()
        for i, item in enumerate(batch):
            v = by_idx.get(i + 1)
            if v is None:
                result.append(item)
                continue
            if not v.get("keep", True):
                continue
            group = v.get("dup_group")
            if isinstance(group, int):
                if group in seen_groups:
                    continue
                seen_groups.add(group)
            caption = v.get("caption")
            result.append(SlideImage(
                path=item.path, timestamp=item.timestamp,
                caption=str(caption) if caption else None,
            ))
    return result
```

**Step 4: Прогнать** → passed. **Step 5: Commit**

```bash
git add lecturelog/infrastructure/frames/vlm.py prompts/frames_qc_v1.md tests/unit/frames/test_vlm_qc.py
git commit -m "feat(frames): VLM QC — keep/drop, подписи, дедуп семантических дублей"
```

---

## Задача 15: Привязка кадров к секциям (стадия G)

**Files:**
- Create: `lecturelog/infrastructure/frames/binding.py`
- Test: `tests/unit/frames/test_binding.py`

**Step 1: Тесты**

```python
# tests/unit/frames/test_binding.py
from pathlib import Path

from lecturelog.domain.models import Section, Topic
from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.frames.binding import bind_frames_to_sections


def _topic(sections):
    return Topic(title="Тема", start=sections[0].start, end=sections[-1].end,
                 sections=sections)


def _sections():
    return [
        Section(title="A", start="00:00:00", end="00:05:00", content=""),
        Section(title="B", start="00:05:00", end="00:10:00", content=""),
        Section(title="C", start="00:10:00", end="00:15:00", content=""),
    ]


def _img(ts):
    return SlideImage(path=Path(f"f{ts}.jpg"), timestamp=float(ts))


def test_frames_land_in_their_sections():
    topics = [_topic(_sections())]
    bind_frames_to_sections([_img(30), _img(400), _img(700)], topics)
    secs = topics[0].sections
    assert secs[0].slide_indices == [1]
    assert secs[1].slide_indices == [2]
    assert secs[2].slide_indices == [3]
    assert topics[0].slide_indices == [1, 2, 3]


def test_ts_beyond_last_section_clamps_to_last():
    topics = [_topic(_sections())]
    bind_frames_to_sections([_img(9999)], topics)
    assert topics[0].sections[2].slide_indices == [1]


def test_monotonic_no_backward_jumps():
    # Кадры отсортированы по ts → привязка не скачет назад по секциям
    topics = [_topic(_sections())]
    bind_frames_to_sections([_img(400), _img(410), _img(420)], topics)
    assert topics[0].sections[1].slide_indices == [1, 2, 3]


def test_document_slides_are_rejected():
    import pytest
    topics = [_topic(_sections())]
    with pytest.raises(ValueError):
        bind_frames_to_sections([SlideImage(path=Path("s.png"))], topics)
```

Run: → FAIL.

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/binding.py
"""Стадия G: привязка кадров к секциям structurize по таймкодам (дизайн §5.G).

Кадр рождается с таймстемпом; привязка = поиск секции по интервалу +
монотонизация (кадры не скачут назад по секциям). LLM-матчинг не нужен —
он остаётся только документным слайдам."""
from __future__ import annotations

import bisect

from lecturelog.domain.models import Topic
from lecturelog.domain.ports import SlideImage
from lecturelog.infrastructure.srt import parse_srt_time


def bind_frames_to_sections(items: list[SlideImage], topics: list[Topic]) -> None:
    """Проставить section.slide_indices (1-based, в порядке items по ts).

    items должны быть отсортированы по timestamp (провайдер это гарантирует);
    поэтому монотонность следует из монотонности интервалов секций, отдельный
    прижим prev_section — страховка от пересекающихся интервалов LLM."""
    if any(item.timestamp is None for item in items):
        raise ValueError("привязка по таймкодам требует timestamp у каждого кадра")
    sections = [s for t in topics for s in t.sections]
    if not sections:
        return
    starts = [parse_srt_time(s.start) for s in sections]

    prev_idx = 0
    for order, item in enumerate(sorted(items, key=lambda x: x.timestamp), start=1):
        # Последняя секция, начавшаяся не позже ts; до первой секции → секция 0
        idx = max(0, bisect.bisect_right(starts, item.timestamp) - 1)
        idx = max(idx, prev_idx)          # монотонизация
        idx = min(idx, len(sections) - 1)  # хвост за последней секцией → последняя
        sections[idx].slide_indices.append(order)
        prev_idx = idx

    # Продублировать привязку на уровень тем (как делает structurizer для документов)
    flat_pos = 0
    for topic in topics:
        acc: list[int] = []
        for section in topic.sections:
            acc.extend(section.slide_indices)
            flat_pos += 1
        topic.slide_indices = sorted(set(acc))
```

**Step 3: Прогнать** → passed. **Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/binding.py tests/unit/frames/test_binding.py
git commit -m "feat(frames): привязка кадров к секциям по таймкодам с монотонизацией"
```

---

## Задача 16: `VideoFrameProvider` — оркестрация A–F

**Files:**
- Create: `lecturelog/infrastructure/frames/provider.py`
- Test: `tests/unit/frames/test_provider.py`

**Step 1: Тесты** — сквозной прогон на склеенной синтетике (слайды + спикер), VLM мокается:

```python
# tests/unit/frames/test_provider.py
import json

from lecturelog.infrastructure.frames.provider import VideoFrameProvider
from lecturelog.infrastructure.frames.types import FramesTuning
from tests.support.synthetic_video import slides_frames, speaker_frames, write_video
from tests.unit.frames.test_vlm_classify import FakeLlm

SRT = """1
00:00:00,000 --> 00:00:30,000
вступление

2
00:00:30,000 --> 00:02:00,000
смотрим слайды
"""


def _video(tmp_path):
    # 30с спикер + 90с слайды (3 слайда по 30с)
    frames = speaker_frames(total_secs=30, seed=1) + slides_frames(
        n_slides=3, secs_per_slide=30)
    return write_video(frames, tmp_path / "lecture.mp4", fps=1)


def _srt(tmp_path):
    p = tmp_path / "t.srt"
    p.write_text(SRT, encoding="utf-8")
    return p


def _classify_resp(kinds):
    return json.dumps([
        {"idx": i + 1, "type": k, "content_bbox": None, "board_kind": "none"}
        for i, k in enumerate(kinds)
    ])


def _qc_keep_all(n):
    return json.dumps([
        {"idx": i + 1, "keep": True, "caption": f"Слайд {i + 1}", "dup_group": None}
        for i in range(n)
    ])


async def test_end_to_end_slides_lecture(tmp_path):
    llm = FakeLlm([_classify_resp(["speaker", "slides"]), _qc_keep_all(3)])
    provider = VideoFrameProvider(
        video_path=_video(tmp_path), srt_path=_srt(tmp_path),
        llm=llm, models=["m"], effort="low", tuning=FramesTuning(),
    )
    usage_events = []
    items = await provider.get_slides(tmp_path / "out",
                                      on_usage=lambda p: usage_events.append(p))
    assert 2 <= len(items) <= 3          # по кандидату на слайд
    assert all(i.timestamp is not None and i.timestamp >= 30 for i in items)
    assert all(i.caption for i in items)  # подписи из QC
    assert items == sorted(items, key=lambda i: i.timestamp)
    assert len(usage_events) == 2         # classify + qc


async def test_vlm_down_degrades_to_signatures(tmp_path):
    class BrokenLlm:
        async def call(self, *a, **kw):
            raise RuntimeError("free tier исчерпан")

    provider = VideoFrameProvider(
        video_path=_video(tmp_path), srt_path=_srt(tmp_path),
        llm=BrokenLlm(), models=["m"], effort="low", tuning=FramesTuning(),
    )
    items = await provider.get_slides(tmp_path / "out")
    # Классификация по сигнатурам B, QC пропущен — кадры есть, подписей нет
    assert len(items) >= 1
    assert all(i.caption is None for i in items)


async def test_speaker_only_video_returns_empty(tmp_path):
    video = write_video(speaker_frames(total_secs=60, seed=2), tmp_path / "v.mp4")
    llm = FakeLlm([_classify_resp(["speaker"])])
    provider = VideoFrameProvider(
        video_path=video, srt_path=_srt(tmp_path),
        llm=llm, models=["m"], effort="low", tuning=FramesTuning(),
    )
    assert await provider.get_slides(tmp_path / "out") == []  # это норма (дизайн §1)
```

Run: → FAIL.

**Step 2: Реализация**

```python
# lecturelog/infrastructure/frames/provider.py
"""VideoFrameProvider — реализация SlideProvider для видеокадров.

Оркеструет воронку A–F (дизайн §4); привязку G делает pipeline после
structurize. CPU-стадии выполняются в to_thread, VLM-сбои деградируют
(дизайн §10), но исключения инфраструктуры (ffmpeg) пробрасываются —
их гасит стадия в pipeline (философия no_slides)."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from lecturelog.domain.ports import ProgressCallback, SlideImage, SlideProvider, UsageCallback
from lecturelog.infrastructure.frames import vlm
from lecturelog.infrastructure.frames.board import board_candidates
from lecturelog.infrastructure.frames.coding import coding_candidates_from_frames
from lecturelog.infrastructure.frames.extract import render_candidates
from lecturelog.infrastructure.frames.ffmpeg_io import ThumbStore, decode_gray
from lecturelog.infrastructure.frames.segmentation import segment_regimes
from lecturelog.infrastructure.frames.signals import compute_signals
from lecturelog.infrastructure.frames.slides_policy import slide_candidates
from lecturelog.infrastructure.frames.types import Candidate, FramesTuning, Regime, SignalTrack
from lecturelog.infrastructure.srt import parse_srt_time

logger = logging.getLogger(__name__)

_SRT_TIME = re.compile(r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->")


def _parse_srt_blocks(srt_text: str) -> list[tuple[float, str]]:
    """[(start_sec, text)] — реплики для оракула D2 и легенды QC."""
    blocks: list[tuple[float, str]] = []
    for block in re.split(r"\n\s*\n+", srt_text.strip()):
        m = _SRT_TIME.search(block)
        if not m:
            continue
        lines = [l.strip() for l in block.splitlines()
                 if l.strip() and not l.strip().isdigit() and "-->" not in l]
        blocks.append((parse_srt_time(m.group(1)), " ".join(lines)))
    return blocks


class VideoFrameProvider(SlideProvider):
    def __init__(
        self,
        video_path: Path,
        srt_path: Path,
        llm,
        models: list[str],
        effort: str,
        tuning: FramesTuning | None = None,
        prompts_dir: Path = Path("prompts"),
    ) -> None:
        self._video = Path(video_path)
        self._srt = Path(srt_path)
        self._llm = llm
        self._models = models
        self._effort = effort
        self._tuning = tuning or FramesTuning()
        self._prompts_dir = prompts_dir

    async def get_slides(
        self,
        output_dir: Path,
        on_progress: ProgressCallback | None = None,
        on_usage: UsageCallback | None = None,
    ) -> list[SlideImage]:
        t = self._tuning
        output_dir.mkdir(parents=True, exist_ok=True)
        srt_blocks = _parse_srt_blocks(self._srt.read_text(encoding="utf-8"))

        # A: грубый проход — сигналы + тумбы
        store = ThumbStore(output_dir / "thumbs")
        track: SignalTrack = await asyncio.to_thread(
            lambda: compute_signals(
                decode_gray(self._video, fps=t.analysis_fps, width=t.analysis_width),
                fps=t.analysis_fps, thumbs=store,
            )
        )
        # B: сегментация
        regimes = segment_regimes(track, t)

        # C: VLM-классификация; сбой → остаёмся на сигнатурах B (деградация)
        try:
            reps, micro = self._representatives(regimes, track, store)
            regimes = await vlm.classify_regimes(
                self._llm, self._models, self._effort, regimes, reps, micro,
                t, on_usage=on_usage, prompts_dir=self._prompts_dir,
            )
        except Exception as error:  # noqa: BLE001 — деградация по дизайну §10
            logger.warning("VLM-классификация недоступна (%s): типы из сигнатур", error)

        # D: пер-режимные политики
        candidates = await asyncio.to_thread(
            self._collect_candidates, regimes, track, store, srt_blocks)
        if len(candidates) > t.max_candidates:
            candidates = sorted(candidates, key=lambda c: c.score, reverse=True)
            candidates = sorted(candidates[: t.max_candidates], key=lambda c: c.ts)
        if not candidates:
            return []

        # E: качественная выемка
        items = await asyncio.to_thread(
            render_candidates, self._video, candidates, output_dir, t)

        # F: QC + подписи; сбой → кадры без QC (чуть грязнее, но стадия работает)
        try:
            items = await vlm.qc_frames(
                self._llm, self._models, self._effort, items,
                srt_text_at=lambda ts: self._nearest_text(srt_blocks, ts),
                tuning=t, on_usage=on_usage, prompts_dir=self._prompts_dir,
            )
        except Exception as error:  # noqa: BLE001 — деградация по дизайну §10
            logger.warning("VLM QC недоступен (%s): кадры без подписей", error)

        items = sorted(items, key=lambda i: i.timestamp)[: t.max_frames]
        return items

    def _representatives(
        self, regimes: list[Regime], track: SignalTrack, store: ThumbStore
    ):
        """Репрезентативный кадр режима — самый резкий из 3 сэмплов середины."""
        import cv2
        reps, micro = [], []
        for r in regimes:
            mid = int((r.start_s + r.end_s) / 2 * track.fps)
            idxs = [max(0, min(track.n_frames - 1, mid + d)) for d in (-2, 0, 2)]
            frames = [store.get(i) for i in idxs]
            reps.append(max(frames, key=lambda f: float(cv2.Laplacian(f, cv2.CV_64F).var())))
            i0, i1 = int(r.start_s * track.fps), max(int(r.end_s * track.fps), 1)
            seg_mad = track.mad[i0:i1]
            seg_motion = track.motion_frac[i0:i1]
            micro.append(float(((seg_mad > 0.05) & (seg_motion < self._tuning.micro_area_max)).mean())
                         if len(seg_mad) else 0.0)
        return reps, micro

    def _collect_candidates(
        self, regimes, track, store, srt_blocks
    ) -> list[Candidate]:
        t = self._tuning
        out: list[Candidate] = []
        for regime in regimes:
            if regime.kind == "slides":
                out.extend(slide_candidates(regime, track, store, t))
            elif regime.kind == "board":
                out.extend(board_candidates(regime, track, store, t))
            elif regime.kind in ("code", "terminal"):
                # Передекод отрезка на code_fps: 1 fps печать не видит
                frames = list(decode_gray(self._video, fps=t.code_fps, width=t.code_width,
                                          start_s=regime.start_s, end_s=regime.end_s))
                out.extend(coding_candidates_from_frames(
                    frames, fps=t.code_fps, regime=regime, tuning=t,
                    srt_blocks=srt_blocks))
            elif regime.kind == "camera":
                # Ручная камера: разреженный отбор — плато-политика, всё решит QC
                out.extend(slide_candidates(regime, track, store, t))
            # speaker / other → 0 кандидатов (это норма, дизайн §1)
        return sorted(out, key=lambda c: c.ts)

    @staticmethod
    def _nearest_text(srt_blocks: list[tuple[float, str]], ts: float) -> str:
        if not srt_blocks:
            return ""
        return min(srt_blocks, key=lambda b: abs(b[0] - ts))[1]
```

**Step 3: Прогнать** — `uv run pytest tests/unit/frames/ -q` → все зелёные.

**Step 4: Commit**

```bash
git add lecturelog/infrastructure/frames/provider.py tests/unit/frames/test_provider.py
git commit -m "feat(frames): VideoFrameProvider — оркестрация воронки A–F с деградацией"
```

---

## Задача 17: Интеграция в пайплайн

Стадия `VIDEO_SLIDES` возрождается: провайдер создаётся ПОСЛЕ transcribe (ему нужен SRT), кадры не идут в structurize, привязка — после него. Стадия никогда не роняет задачу.

**Files:**
- Modify: `lecturelog/application/worker.py` (тип фабрики)
- Modify: `lecturelog/application/pipeline_service.py`
- Modify: `lecturelog/api/lifespan.py`, `lecturelog/api/routes.py`
- Test: `tests/unit/test_pipeline_service_video.py`

**Step 1: Тесты** (добавить в `test_pipeline_service_video.py`, используя существующие фейки файла):

```python
async def test_video_frames_stage_runs_after_transcribe_and_binds(...):
    # Фабрика (video_path, srt_path) -> FakeFrameProvider, возвращающий
    # [SlideImage(path=..., timestamp=30.0, caption="Слайд")].
    # Проверить: task.stage проходил VIDEO_SLIDES; structurizer получил
    # slide_images == []; у секций после run проставлены slide_indices;
    # exporter получил кадры; usage имеет slides_origin == "video_extracted".

async def test_video_frames_stage_failure_does_not_fail_task(...):
    # Фабрика возвращает провайдер, чей get_slides кидает RuntimeError.
    # Задача доходит до DONE, слайдов нет (философия no_slides).

async def test_document_slides_still_win_over_video_frames(...):
    # slide_provider (документ) задан → фабрика кадров НЕ вызывается,
    # structurizer получает пути документных слайдов (старый путь).
```

Написать их полностью по образцу существующих тестов файла (фейковый repo/transcriber/structurizer/cutter/exporter там уже есть). Run: → FAIL.

**Step 2: `worker.py`** — тип фабрики теперь принимает видео и SRT:

```python
    # Отложенный видео-провайдер: строится после transcribe из (video_path, srt_path) —
    # стадии кадров нужен транскрипт (оракул live-coding, легенда QC).
    video_slide_provider_factory: Callable[[Path, Path], SlideProvider] | None = None
```

**Step 3: `pipeline_service.py`** — правки:

1. Удалить блок отложенного создания провайдера после ingest (строки с `if slide_provider is None and video_slide_provider_factory is not None: slide_provider = video_slide_provider_factory(local_video)`), оставив `local_video`.
2. После `await self._persist_usage(task, acc)` за transcribe вставить стадию кадров:

```python
            # Стадия кадров из видео: только когда нет документа (документ приоритетнее)
            # и источник — видео. Кадры НЕ влияют на структуризацию (дизайн §4):
            # привязка к секциям — после structurize по таймкодам.
            video_frames: list[SlideImage] = []
            if (
                slide_provider is None
                and video_slide_provider_factory is not None
                and is_video
            ):
                acc.set_mode(source="video", slides_origin="video_extracted")
                await self._set(
                    task,
                    stage=PipelineStage.VIDEO_SLIDES,
                    progress=plan.stage_start(PipelineStage.VIDEO_SLIDES),
                )

                async def frames_usage(payload: dict):
                    acc.record_llm("video_slides", payload)

                frames_provider = video_slide_provider_factory(local_video, srt_path)
                try:
                    video_frames = await frames_provider.get_slides(
                        output_dir=work_dir / "frames",
                        on_usage=frames_usage,
                    )
                except Exception as frames_error:  # noqa: BLE001 — стадия кадров
                    # никогда не роняет задачу (философия no_slides, дизайн §10)
                    logger.warning(
                        "Стадия кадров упала для task=%s, конспект без кадров: %s",
                        task.task_id, frames_error,
                    )
                    video_frames = []
                await self._persist_usage(task, acc)
```

3. Документная ветка: существующий блок `if slide_provider is not None` остаётся, но `slide_images = await slide_provider.get_slides(...)` теперь возвращает `list[SlideImage]` (переименовано в `slide_items` Задачей 3).
4. В structurize передавать только документные пути:

```python
            topics = await self._structurizer.structurize(
                srt_path=srt_path,
                slide_images=[s.path for s in slide_items],  # только документ; кадры — мимо
                ...
            )
```

5. После structurize — привязка и объединение:

```python
            if video_frames:
                # G: привязка кадров к секциям по таймкодам + монотонизация
                bind_frames_to_sections(video_frames, topics)
                slide_items = video_frames
```

6. Импорты: `from lecturelog.domain.ports import SlideImage`, `from lecturelog.infrastructure.frames.binding import bind_frames_to_sections`.

**Step 4: Wiring (`lifespan.py`, `routes.py`)**

`lifespan.py` после создания `llm`:

```python
    # Фабрика провайдера кадров из видео: (video_path, srt_path) -> SlideProvider.
    # None при FRAMES_ENABLED=false — тогда видео идёт как аудио-лекция без кадров.
    frames_factory = None
    if cfg.frames.enabled:
        def frames_factory(video_path: Path, srt_path: Path) -> VideoFrameProvider:
            return VideoFrameProvider(
                video_path=video_path, srt_path=srt_path,
                llm=llm, models=cfg.frames.models, effort=cfg.frames.effort,
            )
    app.state.frames_provider_factory = frames_factory
```

`routes.py`: заменить блок `video_slide_provider_factory = None` (с комментарием про отключение) на:

```python
        # Стадия кадров из видео: фабрика создаётся в lifespan (None, если выключена).
        video_slide_provider_factory = request.app.state.frames_provider_factory
```

(параметр `request: Request` в обработчике уже есть или добавляется по образцу соседних обработчиков). `no_slides` по-прежнему гасит оба провайдера.

**Step 5: Прогнать всё**

Run: `uv run pytest -q && uv run ruff check lecturelog tests`
Expected: все зелёные, линт чистый. Проверить существующие тесты `test_progress_plan_video.py` — полоса `VIDEO_SLIDES` (25, 40) уже есть, менять не нужно.

**Step 6: Commit**

```bash
git add -A
git commit -m "feat(frames): стадия VIDEO_SLIDES в пайплайне — кадры из видео с привязкой к секциям"
```

---

## Задача 18: E2E на смешанной синтетике + отладочный скрипт

**Files:**
- Test: `tests/integration/test_frames_e2e.py`
- Create: `scripts/frames_debug.py`

**Step 1: E2E-тест** — склейка «спикер + слайды + доска» (3–4 минуты синтетики), полный `VideoFrameProvider.get_slides` с FakeLlm: проверить, что кадры пришли из слайдовых и досочных интервалов, ни одного из спикерских, тайминги монотонны, файлы существуют, суффиксы верные. Пометить `@pytest.mark.slow`, если прогон > 30 с.

**Step 2: Отладочный скрипт** для калибровки на реальных лекциях (критерии приёмки §13):

```python
# scripts/frames_debug.py
"""Прогон стадии кадров на реальном видео с дампом отладки.

Использование:
    uv run python scripts/frames_debug.py lecture.mp4 lecture.srt out/
Пишет в out/: кадры, thumbs, regimes.json (таймлайн режимов), signals.npz
(кривые mad/motion/edge/shift для построения графиков), candidates.json."""
```

Скрипт собирает `VideoFrameProvider` с реальным `LlmClient` из env (или `--no-vlm` для чистого CV), печатает сводку: режимы, кандидаты по типам, время каждой стадии, расход токенов. Реализация — прямые вызовы тех же функций, что в provider, с сохранением промежуточных артефактов.

**Step 3: Прогнать** — `uv run pytest tests/integration/test_frames_e2e.py -q` → passed.

**Step 4: Commit**

```bash
git add tests/integration/test_frames_e2e.py scripts/frames_debug.py
git commit -m "test(frames): e2e на смешанной синтетике + скрипт калибровки"
```

---

## Задача 19: Финализация

**Step 1: Полная проверка**

Run: `uv run pytest -q && uv run ruff check lecturelog tests && uv run ruff format --check lecturelog tests`
Expected: всё зелёное. REQUIRED SUB-SKILL: superpowers:verification-before-completion.

**Step 2: Документация** — через субагента (правило пользователя: README обновляет отдельный субагент с контекстом изменений): передать ему сводку — новая стадия `VIDEO_SLIDES`, env-переменные `FRAMES_ENABLED` / `LLM_MODELS_VIDEO_SLIDES` / `LLM_EFFORT_VIDEO_SLIDES`, новые зависимости opencv/numpy, поведение `no_slides` и приоритет документа, деградация без VLM.

**Step 3: Ручная приёмка (§13 дизайна, вне CI)** — прогнать `scripts/frames_debug.py` на 5 реальных лекциях (по одной на тип + смешанная), проверить:
- ≥ 90 % «очевидных» визуальных состояний в выдаче;
- ≤ 10 % мусора после QC;
- runtime ≤ 15 мин на 1.5 ч видео (4 vCPU);
- расход ≤ 2 ¢ по платному прайсу.
Пороги `FramesTuning` калибровать по результатам; калибровка коммитится отдельно.

**Step 4: Commit + завершение ветки**

```bash
git add -A
git commit -m "docs(frames): документация стадии кадров из видео"
```

REQUIRED SUB-SKILL: superpowers:finishing-a-development-branch (merge/PR в `main`).

---

## Не входит в v1 (зафиксировано дизайном)

- guide-режим (`output_mode: guide`, `extracted_text`) — отдельный дизайн (§12);
- captions в render-промпт structurize — фаза 2 (§14.1);
- параллельность стадии кадров со structurize — оптимизация, контракт не фиксирует (§14.3);
- OCR code-режима, PELT change-point detection, keyframe-only первый проход (§8 «резервы»).
