# audiopreproc v2 — Шумоподавление через Resemble Enhance (HF Space API)

**Goal:** Снести текущий `pkg/audiopreproc`, сделать простой пакет: бьёт аудио на чанки по 60 сек, отправляет в Resemble Enhance через HuggingFace Space API, склеивает обратно. Больше ничего.

**Architecture:** Go-пакет `pkg/audiopreproc`. Единственная задача — шумоподавление. Нарезка/склейка чанков через ffmpeg. Перед отправкой в API вырезаем длинные паузы (тишину) из каждого чанка. Обработка через Python `gradio_client` → HF Space API (ResembleAI/resemble-enhance). Нормализация, пресеты, TimeMap — убраны из этого пакета.

**Tech Stack:** Go 1.25, ffmpeg (`exec.Command`), Python 3.11+ + `gradio_client` (для вызова HF API), `testify` для тестов

**Решение по шумоподавлению:** DeepFilterNet портит голос, RNNoise — тоже. Resemble Enhance через HF Space API показал лучшее качество и работает бесплатно. Ограничение — чанки ~30-60 сек (нужно протестировать точный лимит).

---

## Что сносим

Удаляем `pkg/audiopreproc/` полностью:
- `normalize.go` — убираем
- `silence.go` — убираем
- `pipeline.go` — убираем
- `preset.go` — убираем
- `audiopreproc.go` (типы TimeMap, Segment, Result) — убираем
- `denoise.go` (DeepFilterNet) — убираем
- `models/` — убираем
- `cmd/audiopreproc/` — убираем

## Новый пакет

### Принцип работы

```
input (любой формат)
  │
  ▼
ffmpeg: нарезка на чанки по 60 сек (WAV 44.1kHz mono)
  │
  ▼
ffmpeg: вырезание тишины из чанков
  │  (silencedetect/atrim, дефолт: -35dB, 5s)
  │
  ▼
Python gradio_client: отправка каждого чанка в HF Space API
  │  (ResembleAI/resemble-enhance, endpoint /predict, denoise only)
  │
  ▼
ffmpeg: склейка обработанных чанков → output
```

### Структура

```
pkg/audiopreproc/
├── audiopreproc.go        # Process(ctx, input, output) — единственная публичная функция
├── audiopreproc_test.go   # Тесты
├── ffmpeg.go              # runFFmpeg, CheckFFmpeg, split/concat, getAudioDuration
├── silence.go             # detect/remove silence для WAV-чанков
├── silence_test.go        # Тесты детекции и вырезания тишины
├── cmd/audiopreproc/      # CLI: запуск одной командой
│   └── main.go
├── scripts/
│   └── denoise.py         # Python-скрипт для вызова HF API
└── testdata/              # Тестовые аудио (в .gitignore)
```

### CLI запуск

```bash
go run ./pkg/audiopreproc/cmd/audiopreproc <input> <output>
```

### Зависимости пользователя

```bash
pip install gradio_client   # ~5MB, без PyTorch
```

+ ffmpeg в системе

---

## Task 1: Снести старое, создать скелет пакета

**Step 1:** Удалить `pkg/audiopreproc/` полностью

**Step 2:** Создать `pkg/audiopreproc/audiopreproc.go`:
```go
package audiopreproc

import "context"

// Process — шумоподавление аудиофайла через Resemble Enhance (HF Space API)
// Нарезает на чанки, отправляет на обработку, склеивает обратно.
// Входной формат — любой, который понимает ffmpeg. Выходной — по расширению.
func Process(ctx context.Context, input, output string) error {
    // ...
}
```

**Step 3:** Создать `pkg/audiopreproc/ffmpeg.go` — CheckFFmpeg, runFFmpeg (из старого кода)

**Step 4:** Тест что компилируется

---

## Task 2: Python-скрипт denoise.py

**Step 1:** Создать `pkg/audiopreproc/scripts/denoise.py`:

```python
#!/usr/bin/env python3
"""Шумоподавление через Resemble Enhance (HuggingFace Space API)."""

import sys
import os
import shutil
from gradio_client import Client, handle_file

def main():
    if len(sys.argv) != 3:
        print(f"Использование: {sys.argv[0]} <input_dir> <output_dir>", file=sys.stderr)
        sys.exit(1)

    in_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)

    client = Client("ResembleAI/resemble-enhance")
    chunks = sorted(f for f in os.listdir(in_dir) if f.endswith(".wav"))

    for i, chunk in enumerate(chunks):
        print(f"[{i+1}/{len(chunks)}] {chunk}", file=sys.stderr, flush=True)
        result = client.predict(
            handle_file(os.path.join(in_dir, chunk)),
            "Midpoint", 64, 0.5, True,
            api_name="/predict"
        )
        if result[0]:
            shutil.copy2(result[0], os.path.join(out_dir, chunk))
        else:
            shutil.copy2(os.path.join(in_dir, chunk), os.path.join(out_dir, chunk))
            print(f"  WARN: API вернул None, копируем оригинал", file=sys.stderr)

    print("OK", flush=True)

if __name__ == "__main__":
    main()
```

**Step 2:** Проверить вручную на 30-секундном чанке

---

## Task 3: Реализовать Process()

**Step 1:** Написать тест `TestProcess`

**Step 2:** Реализовать `Process(ctx, input, output)`:
1. `CheckFFmpeg(ctx)` + проверить что `python3` и `gradio_client` доступны
2. Создать tmpDir
3. ffmpeg: нарезать input на чанки по 60 сек → `tmpDir/in/*.wav` (44.1kHz mono)
4. `exec.Command("python3", scriptPath, tmpDir/in, tmpDir/out)`
5. ffmpeg: склеить `tmpDir/out/*.wav` → output
6. Очистить tmpDir

**Step 3:** Запустить тесты

---

## Task 4: Тесты и финализация

**Step 1:** Прогнать тесты на реальном аудио

**Step 2:** Обновить `.gitignore`

**Step 3:** Обновить `docs/plans/2026-02-11-LectureLog-design.md`

---

## Итого: 4 задачи

| # | Что | Файлы |
|---|-----|-------|
| 1 | Снести старое, скелет | `audiopreproc.go`, `ffmpeg.go` |
| 2 | Python-скрипт | `scripts/denoise.py` |
| 3 | Process() — нарезка + Python + склейка | `audiopreproc.go`, `audiopreproc_test.go` |
| 4 | Тесты + финализация | тесты, `.gitignore`, документация |

## Открытые вопросы

- **Точный лимит чанков:** 30 сек работает, 106 сек — нет. Нужно протестировать 60 сек.
- **Fallback при недоступности API:** копировать оригинал? Ошибка? Предупреждение?
- **Параллельная отправка чанков:** можно ускорить 2-3 чанка одновременно, но HF Space может не потянуть.
