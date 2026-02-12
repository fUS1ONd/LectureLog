# audiopreproc: вырезание тишины перед отправкой в API

## Контекст

Пайплайн `pkg/audiopreproc` нарезает аудио на 60-сек чанки и отправляет в Resemble Enhance HF API для шумоподавления. Длинные паузы (5+ сек) тратят время API впустую. Нужно вырезать тишину из чанков перед отправкой. Тишина вырезается навсегда, TimeMap не нужен.

## Новый пайплайн

```
input → splitChunks(60с) → removeSilenceFromChunks → denoise.py (HF API) → concatChunks → output
```

## Файлы

| Файл | Действие |
|------|----------|
| `pkg/audiopreproc/silence.go` | CREATE |
| `pkg/audiopreproc/silence_test.go` | CREATE |
| `pkg/audiopreproc/audiopreproc.go` | MODIFY |
| `pkg/audiopreproc/ffmpeg.go` | MODIFY |

## 1. Создать `pkg/audiopreproc/silence.go`

```go
// silenceSegment — обнаруженный сегмент тишины
type silenceSegment struct {
    Start float64 // секунды
    End   float64 // секунды
}

type SilenceParams struct {
    Threshold   float64 // порог в dB
    MinDuration float64 // минимальная длительность тишины в секундах
}

var DefaultSilenceParams = SilenceParams{
    Threshold:   -35,
    MinDuration: 5,
}
```

### detectSilence

```go
func detectSilence(ctx context.Context, input string, params SilenceParams) ([]silenceSegment, error)
```

- Команда: `ffmpeg -i input -af "silencedetect=noise=-35dB:d=5" -f null -`
- Парсить stderr regex: `silence_start: (\d+\.?\d*)` и `silence_end: (\d+\.?\d*)`
- Если `silence_start` без пары `silence_end` — тишина до конца файла, получить длительность через `getAudioDuration`

### removeSilenceFromFile

```go
func removeSilenceFromFile(ctx context.Context, input, output string, params SilenceParams) error
```

- Нет тишины → `runFFmpeg(ctx, "-i", input, "-c", "copy", "-y", output)`
- Весь файл тишина → скопировать как есть
- Иначе: построить звуковые сегменты (промежутки между тишиной), вырезать через filter_complex:

```bash
ffmpeg -i input.wav \
  -filter_complex "[0:a]atrim=start=0:end=3.5,asetpts=PTS-STARTPTS[s0];[0:a]atrim=start=8.5:end=15.0,asetpts=PTS-STARTPTS[s1];[s0][s1]concat=n=2:v=0:a=1[out]" \
  -map "[out]" -ar 44100 -ac 1 -c:a pcm_s16le -y output.wav
```

### removeSilenceFromChunks

```go
func removeSilenceFromChunks(ctx context.Context, inDir, outDir string, params SilenceParams) error
```

- Glob `inDir/chunk_*.wav`, sort
- Для каждого: `removeSilenceFromFile(ctx, chunk, outDir/chunk_NNN.wav, params)`
- Сохранять те же имена файлов

## 2. Добавить `getAudioDuration` в `pkg/audiopreproc/ffmpeg.go`

```go
func getAudioDuration(ctx context.Context, path string) (float64, error)
```

Команда: `ffprobe -v error -show_entries format=duration -of csv=p=0 path`

## 3. Изменить `Process()` в `pkg/audiopreproc/audiopreproc.go`

Добавить `trimmedDir` между `inDir` и `outDir`:

```go
inDir := filepath.Join(tmpDir, "in")
trimmedDir := filepath.Join(tmpDir, "trimmed")  // НОВОЕ
outDir := filepath.Join(tmpDir, "out")
// MkdirAll для всех трёх

splitChunks(ctx, input, inDir, 60)
removeSilenceFromChunks(ctx, inDir, trimmedDir, DefaultSilenceParams)  // НОВОЕ
exec.Command("python3", scriptPath, trimmedDir, outDir)  // trimmedDir вместо inDir
concatChunks(ctx, outDir, output)
```

## 4. Создать `pkg/audiopreproc/silence_test.go`

Паттерн из `ffmpeg_test.go`: `t.TempDir()`, lavfi sine, testify.

1. **TestDetectSilence** — 3с sine + 6с тишина + 1с sine → найден 1 сегмент ~3-9с
2. **TestRemoveSilenceFromFile** — то же аудио → выход ~4с
3. **TestRemoveSilenceFromFile_NoSilence** — чистый sine 5с → выход ~5с
4. **TestRemoveSilenceFromChunks** — 3 чанка, один с тишиной → все 3 выходных файла существуют
5. **TestGetAudioDuration** — sine 5с → длительность ≈ 5.0

Генерация тестового аудио с тишиной:
```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=3" -f lavfi -i "anullsrc=r=44100:cl=mono" -f lavfi -i "sine=frequency=440:duration=1" \
  -filter_complex "[1:a]atrim=duration=6[silence];[0:a][silence][2:a]concat=n=3:v=0:a=1[out]" \
  -map "[out]" -ar 44100 -ac 1 -c:a pcm_s16le -y test.wav
```

## Комментарии в коде — на русском

## Верификация

```bash
go build ./pkg/audiopreproc/...
go vet ./pkg/audiopreproc/...
go test -v -short ./pkg/audiopreproc/ -run 'TestDetectSilence|TestRemoveSilence|TestGetAudioDuration'
```
