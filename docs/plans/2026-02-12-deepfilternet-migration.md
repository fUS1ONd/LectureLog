# Миграция шумоподавления на DeepFilterNet

**Goal:** Заменить RNNoise (`arnndn`) на DeepFilterNet для значительного улучшения качества шумоподавления без искажений голоса.

**Architecture:** DeepFilterNet — отдельный бинарник (`deep-filter`), вызывается через `exec.Command` так же как ffmpeg. Принимает только WAV 48kHz, поэтому нужна конвертация до/после через ffmpeg. Модель встроена в бинарник.

**Tech Stack:** Go 1.25, DeepFilterNet v0.5.6 (Rust-бинарник), ffmpeg для конвертации

**Ссылки:**
- Репозиторий: https://github.com/Rikorose/DeepFilterNet
- Релизы: https://github.com/Rikorose/DeepFilterNet/releases
- Текущий denoise.go: `pkg/audiopreproc/denoise.go`

---

## Ограничения DeepFilterNet

- **Только WAV** — не принимает mp3, m4a, ogg и т.д.
- **Строго 48kHz** — другие sample rate не поддерживаются
- **Только файлы** — нет stdin/stdout, нет потоковой обработки
- **Выходной формат** — WAV, нужно конвертировать обратно

---

## Task 1: Добавить CheckDeepFilter

**Files:**
- Modify: `pkg/audiopreproc/denoise.go`
- Modify: `pkg/audiopreproc/denoise_test.go`

**Step 1: Написать failing test**

Добавить в `pkg/audiopreproc/denoise_test.go`:
```go
func TestCheckDeepFilter(t *testing.T) {
	err := CheckDeepFilter(context.Background())
	require.NoError(t, err)
}
```

**Step 2: Запустить тест, убедиться что не компилируется**

Run: `go test ./pkg/audiopreproc/ -run TestCheckDeepFilter -v`
Expected: FAIL

**Step 3: Реализовать проверку**

Добавить в `pkg/audiopreproc/denoise.go`:
```go
// CheckDeepFilter — проверяет что deep-filter доступен в системе
func CheckDeepFilter(ctx context.Context) error {
	cmd := exec.CommandContext(ctx, "deep-filter", "--version")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("deep-filter недоступен: %w", err)
	}
	return nil
}
```

**Step 4: Запустить тест**

Run: `go test ./pkg/audiopreproc/ -run TestCheckDeepFilter -v`
Expected: PASS (после установки deep-filter)

**Step 5: Коммит**

Название: `feat(audiopreproc): добавить проверку доступности deep-filter`

---

## Task 2: Реализовать шумоподавление через DeepFilterNet

**Files:**
- Modify: `pkg/audiopreproc/denoise.go` (полная перезапись)
- Modify: `pkg/audiopreproc/denoise_test.go`

**Step 1: Написать failing test**

Обновить `TestDenoise` в `pkg/audiopreproc/denoise_test.go`:
```go
func TestDenoise(t *testing.T) {
	ctx := context.Background()

	input := filepath.Join("testdata", "noisy_sample.mp3")
	output := filepath.Join(t.TempDir(), "denoised.mp3")

	err := denoise(ctx, input, output)
	require.NoError(t, err)

	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))
}
```

Тест не меняется — интерфейс `denoise(ctx, input, output)` остаётся прежним.

**Step 2: Реализовать новый denoise**

Перезаписать `pkg/audiopreproc/denoise.go`:
```go
package audiopreproc

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

// CheckDeepFilter — проверяет что deep-filter доступен в системе
func CheckDeepFilter(ctx context.Context) error {
	cmd := exec.CommandContext(ctx, "deep-filter", "--version")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("deep-filter недоступен: %w", err)
	}
	return nil
}

// denoise — шумоподавление через DeepFilterNet
// DeepFilterNet принимает только WAV 48kHz, поэтому:
// 1. Конвертируем входной файл в WAV 48kHz через ffmpeg
// 2. Запускаем deep-filter
// 3. Конвертируем результат в нужный выходной формат через ffmpeg
func denoise(ctx context.Context, input, output string) error {
	tmpDir, err := os.MkdirTemp("", "deepfilter-*")
	if err != nil {
		return fmt.Errorf("создание временной директории: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	// Шаг 1: конвертация в WAV 48kHz
	wavInput := filepath.Join(tmpDir, "input.wav")
	_, err = runFFmpeg(ctx, []string{
		"-i", input,
		"-ar", "48000",
		"-ac", "1",
		"-y", wavInput,
	})
	if err != nil {
		return fmt.Errorf("конвертация в WAV 48kHz: %w", err)
	}

	// Шаг 2: запуск deep-filter
	cmd := exec.CommandContext(ctx, "deep-filter",
		"--output-dir", tmpDir,
		wavInput,
	)
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("deep-filter: %w", err)
	}

	// deep-filter сохраняет результат в output-dir с тем же именем
	wavOutput := filepath.Join(tmpDir, "input_DeepFilterNet3.wav")

	// Шаг 3: конвертация из WAV в выходной формат
	_, err = runFFmpeg(ctx, []string{
		"-i", wavOutput,
		"-y", output,
	})
	if err != nil {
		return fmt.Errorf("конвертация результата: %w", err)
	}

	return nil
}
```

**Step 3: Запустить тесты**

Run: `go test ./pkg/audiopreproc/ -run TestDenoise -v`
Expected: PASS

**Step 4: Проверить на реальном аудио**

Run:
```bash
go build -o ./bin/audiopreproc ./pkg/audiopreproc/cmd/audiopreproc/
./bin/audiopreproc --preset noisy pkg/audiopreproc/testdata/Noisy-audio.m4a /tmp/df-result.mp3
```
Expected: обработанный файл без искажений голоса

**Step 5: Коммит**

Название: `feat(audiopreproc): заменить RNNoise на DeepFilterNet`

---

## Task 3: Обновить проверки в pipeline

**Files:**
- Modify: `pkg/audiopreproc/pipeline.go`

**Step 1: Добавить проверку deep-filter в Process()**

В `pkg/audiopreproc/pipeline.go` добавить проверку после `CheckFFmpeg`:
```go
// Проверяем что deep-filter доступен (если шумоподавление включено)
if opts.Denoise {
	if err := CheckDeepFilter(ctx); err != nil {
		return nil, err
	}
}
```

**Step 2: Запустить все тесты**

Run: `go test ./pkg/audiopreproc/ -v -count=1`
Expected: ALL PASS

**Step 3: Коммит**

Название: `feat(audiopreproc): проверять доступность deep-filter в pipeline`

---

## Task 4: Удалить модель RNNoise и обновить документацию

**Files:**
- Delete: `pkg/audiopreproc/models/rnnoise-std.rnnn`
- Delete: `pkg/audiopreproc/models/` (директория)
- Modify: `docs/plans/2026-02-11-LectureLog-design.md`

**Step 1: Удалить модель RNNoise**

Run: `rm -rf pkg/audiopreproc/models/`

**Step 2: Обновить дизайн-документ**

В `docs/plans/2026-02-11-LectureLog-design.md`:
- Заменить "нейросетевой фильтр `arnndn` (RNNoise)" на "DeepFilterNet (`deep-filter`)"
- В таблице пресетов: колонку "arnndn (RNNoise)" заменить на "DeepFilterNet"
- В архитектуре: убрать `models/`, обновить описание `denoise.go`

**Step 3: Обновить .gitignore**

Убрать строку `*.rnnn` если есть, добавить `*.wav` в testdata.

**Step 4: Запустить все тесты**

Run: `go test ./pkg/audiopreproc/ -v -count=1`
Expected: ALL PASS

**Step 5: Коммит**

Название: `chore(audiopreproc): удалить RNNoise, обновить документацию под DeepFilterNet`

---

## Task 5: Финальная проверка на всех тестовых аудио

**Files:** нет изменений, только проверка

**Step 1: Собрать CLI**

Run: `go build -o ./bin/audiopreproc ./pkg/audiopreproc/cmd/audiopreproc/`

**Step 2: Прогнать на всех тестовых файлах**

```bash
./bin/audiopreproc --preset quiet_room pkg/audiopreproc/testdata/Clear-audio.m4a /tmp/df-clear.mp3
./bin/audiopreproc --preset lecture_hall pkg/audiopreproc/testdata/BigLectureHall-audio.m4a /tmp/df-hall.mp3
./bin/audiopreproc --preset noisy pkg/audiopreproc/testdata/Noisy-audio.m4a /tmp/df-noisy.mp3
```

**Step 3: Ручная проверка**

Послушать `/tmp/df-clear.mp3`, `/tmp/df-hall.mp3`, `/tmp/df-noisy.mp3` — голос не должен быть искажён, шум должен быть убран.

**Step 4: Прогнать все тесты**

Run: `go test ./pkg/audiopreproc/ -v -count=1`
Expected: ALL PASS

**Step 5: Коммит**

Название: `test(audiopreproc): финальная проверка DeepFilterNet на реальных аудио`

---

## Предварительные требования

Перед началом работы нужно установить DeepFilterNet:

```bash
pip install deepfilternet
```

Или скачать бинарник со страницы релизов:
https://github.com/Rikorose/DeepFilterNet/releases

Проверка:
```bash
deep-filter --version
```

---

## Итого: 5 задач

| # | Что | Файлы |
|---|-----|-------|
| 1 | Проверка доступности deep-filter | `denoise.go`, `denoise_test.go` |
| 2 | Новый denoise через DeepFilterNet | `denoise.go`, `denoise_test.go` |
| 3 | Проверка deep-filter в pipeline | `pipeline.go` |
| 4 | Удалить RNNoise, обновить документацию | `models/`, дизайн-документ, `.gitignore` |
| 5 | Финальная проверка на реальных аудио | — |

## Примечание по именованию выходного файла deep-filter

deep-filter сохраняет результат в `--output-dir` с суффиксом `_DeepFilterNet3` (например `input_DeepFilterNet3.wav`). Это захардкожено в бинарнике. В Task 2 это учтено в коде — путь к результату формируется с этим суффиксом.
