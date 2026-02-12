# audiopreproc v2 — задача для Codex

## Что делаем

Снести `pkg/audiopreproc/` целиком и написать заново. Новый пакет делает одно: шумоподавление через Resemble Enhance HF Space API.

## Пайплайн

```
input → ffmpeg (нарезка на чанки ≤60с, WAV 44.1kHz mono) → python3 denoise.py (HF API) → ffmpeg (склейка) → output
```

## Структура нового пакета

```
pkg/audiopreproc/
├── audiopreproc.go        # Process(ctx, input, output string) error
├── audiopreproc_test.go
├── ffmpeg.go              # CheckFFmpeg, runFFmpeg, splitChunks, concatChunks
├── scripts/
│   └── denoise.py         # Python: gradio_client → HF Space API
└── testdata/              # в .gitignore
```

## API: единственная публичная функция

```go
// Process — шумоподавление аудиофайла.
// Входной формат — любой (ffmpeg). Выходной — по расширению output.
func Process(ctx context.Context, input, output string) error
```

Внутри:
1. `CheckFFmpeg(ctx)` + проверить `python3` + `gradio_client`
2. Создать tmpDir с подпапками `in/` и `out/`
3. ffmpeg: `splitChunks(ctx, input, tmpDir/in, 60)` → `chunk_000.wav`, `chunk_001.wav`, ...
   - Команда: `ffmpeg -i input -f segment -segment_time 60 -ar 44100 -ac 1 -c:a pcm_s16le tmpDir/in/chunk_%03d.wav`
4. `exec.Command("python3", scriptPath, tmpDir/in, tmpDir/out)` — ждём stdout `"OK\n"`
5. ffmpeg: `concatChunks(ctx, tmpDir/out, output)` — через concat demuxer
6. `os.RemoveAll(tmpDir)`

## Python-скрипт `scripts/denoise.py`

```python
#!/usr/bin/env python3
"""Шумоподавление через Resemble Enhance (HF Space API)."""
import sys, os, shutil
from gradio_client import Client, handle_file

def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_dir> <output_dir>", file=sys.stderr)
        sys.exit(1)

    in_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)

    client = Client("ResembleAI/resemble-enhance")
    chunks = sorted(f for f in os.listdir(in_dir) if f.endswith(".wav"))

    for i, chunk in enumerate(chunks):
        print(f"[{i+1}/{len(chunks)}] {chunk}", file=sys.stderr, flush=True)
        result = client.predict(
            handle_file(os.path.join(in_dir, chunk)),
            "Midpoint",  # solver
            64,           # nfe (1-128)
            0.5,          # tau (0-1)
            True,         # denoising
            api_name="/predict"
        )
        # result = (denoised_path, enhanced_path)
        if result[0]:
            shutil.copy2(result[0], os.path.join(out_dir, chunk))
        else:
            shutil.copy2(os.path.join(in_dir, chunk), os.path.join(out_dir, chunk))
            print(f"  WARN: API вернул None, копируем оригинал", file=sys.stderr)

    print("OK", flush=True)

if __name__ == "__main__":
    main()
```

## HF Space API справка

- **Space:** `ResembleAI/resemble-enhance` (Gradio 4.8.0)
- **Endpoint:** `/predict`
- **Лимит:** 60 сек на файл (хардкод в app.py)
- **Inputs:** `(audio, solver, nfe, tau, denoising)` — см. скрипт выше
- **Outputs:** `(denoised_audio_path, enhanced_audio_path)` — берём `result[0]`
- **Rate limit:** публичный space, при злоупотреблении HF может ограничить

## ffmpeg.go — что должно быть

```go
// CheckFFmpeg — проверяет что ffmpeg доступен
func CheckFFmpeg(ctx context.Context) error

// runFFmpeg — запускает ffmpeg с аргументами, возвращает stderr
func runFFmpeg(ctx context.Context, args ...string) (string, error)

// splitChunks — нарезает input на WAV-чанки по segmentSec секунд в outDir
func splitChunks(ctx context.Context, input, outDir string, segmentSec int) error

// concatChunks — склеивает WAV-чанки из inDir в output через concat demuxer
func concatChunks(ctx context.Context, inDir, output string) error
```

## Что удалить

Всё содержимое `pkg/audiopreproc/` перед созданием нового:
- `audiopreproc.go`, `denoise.go`, `normalize.go`, `silence.go`, `pipeline.go`, `preset.go`, `ffmpeg.go`
- Все `*_test.go`
- `models/`
- `cmd/audiopreproc/` (если есть)

Папку `testdata/` сохранить — там тестовые аудио.

## Тесты

- `TestCheckFFmpeg` — ffmpeg доступен
- `TestSplitChunks` — нарезка создаёт правильное количество чанков
- `TestConcatChunks` — склейка работает
- `TestProcess` — интеграционный (пометить `testing.Short()` skip, нужен реальный API)

## Зависимости

- Go 1.25, `testify`
- ffmpeg в системе
- Python 3.11+ с `gradio_client` (`pip install gradio_client`)

## Комментарии в коде — на русском
