# audiopreproc — План реализации модуля препроцессинга аудио

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Создать самостоятельную Go-библиотеку и CLI для препроцессинга аудио (нормализация, шумоподавление, вырезание тишины) через ffmpeg.

**Architecture:** Go-модуль `pkg/audiopreproc` внутри монорепо LectureLog. Три этапа обработки объединены в pipeline. Каждый этап — отдельный файл, вызывает ffmpeg через `exec.Command`. Пресеты задают параметры фильтров. TimeMap отслеживает смещения таймкодов при вырезании тишины.

**Tech Stack:** Go 1.25, ffmpeg 6.x (`exec.Command`), `testify` для тестов

**Ссылки:**
- Дизайн-документ: `docs/plans/2026-02-11-LectureLog-design.md` (раздел "Препроцессинг аудио")

---

## Task 1: Инициализация Go-модуля и структуры проекта

**Files:**
- Create: `go.mod`
- Create: `pkg/audiopreproc/audiopreproc.go`
- Create: `pkg/audiopreproc/audiopreproc_test.go`

**Step 1: Инициализировать Go-модуль**

Run: `go mod init github.com/LectureLog/LectureLog`

**Step 2: Создать пакет audiopreproc с базовой структурой**

`pkg/audiopreproc/audiopreproc.go`:
```go
package audiopreproc

import "time"

// Segment — фрагмент аудио с таймкодами в оригинале и обработанном файле
type Segment struct {
	OriginalStart  time.Duration
	OriginalEnd    time.Duration
	ProcessedStart time.Duration
	ProcessedEnd   time.Duration
}

// TimeMap — маппинг таймкодов между оригинальным и обработанным аудио
type TimeMap struct {
	Segments []Segment
}

// Result — результат обработки аудио
type Result struct {
	OutputPath string
	TimeMap    *TimeMap // nil если вырезание тишины отключено
}
```

**Step 3: Создать тест-файл с проверкой что пакет импортируется**

`pkg/audiopreproc/audiopreproc_test.go`:
```go
package audiopreproc

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
)

func TestSegmentCreation(t *testing.T) {
	seg := Segment{
		OriginalStart:  0,
		OriginalEnd:    10 * time.Second,
		ProcessedStart: 0,
		ProcessedEnd:   10 * time.Second,
	}
	assert.Equal(t, 10*time.Second, seg.OriginalEnd)
}
```

**Step 4: Установить testify и запустить тест**

Run: `go get github.com/stretchr/testify && go test ./pkg/audiopreproc/ -v`
Expected: PASS

**Step 5: Коммит**

Название: `feat(audiopreproc): инициализация модуля, базовые типы`

---

## Task 2: Обёртка над ffmpeg

**Files:**
- Create: `pkg/audiopreproc/ffmpeg.go`
- Create: `pkg/audiopreproc/ffmpeg_test.go`

**Step 1: Написать failing test**

`pkg/audiopreproc/ffmpeg_test.go`:
```go
package audiopreproc

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCheckFFmpeg(t *testing.T) {
	err := CheckFFmpeg(context.Background())
	require.NoError(t, err)
}

func TestRunFFmpeg_InvalidArgs(t *testing.T) {
	_, err := runFFmpeg(context.Background(), []string{"-i", "nonexistent_file.mp3"})
	assert.Error(t, err)
}
```

**Step 2: Запустить тест, убедиться что не компилируется**

Run: `go test ./pkg/audiopreproc/ -run TestCheckFFmpeg -v`
Expected: FAIL — `CheckFFmpeg` not defined

**Step 3: Реализовать обёртку**

`pkg/audiopreproc/ffmpeg.go`:
```go
package audiopreproc

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
)

// CheckFFmpeg — проверяет что ffmpeg доступен в системе
func CheckFFmpeg(ctx context.Context) error {
	cmd := exec.CommandContext(ctx, "ffmpeg", "-version")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("ffmpeg недоступен: %w", err)
	}
	return nil
}

// runFFmpeg — запускает ffmpeg с аргументами, возвращает stderr (ffmpeg пишет вывод туда)
func runFFmpeg(ctx context.Context, args []string) (string, error) {
	cmd := exec.CommandContext(ctx, "ffmpeg", args...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return stderr.String(), fmt.Errorf("ffmpeg завершился с ошибкой: %w\nstderr: %s", err, stderr.String())
	}
	return stderr.String(), nil
}

// probeFormat — получает информацию об аудиофайле через ffprobe
func probeFormat(ctx context.Context, path string) (string, error) {
	cmd := exec.CommandContext(ctx, "ffprobe",
		"-v", "quiet",
		"-print_format", "json",
		"-show_format",
		"-show_streams",
		path,
	)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("ffprobe завершился с ошибкой: %w\nstderr: %s", err, stderr.String())
	}
	return stdout.String(), nil
}
```

**Step 4: Запустить тесты**

Run: `go test ./pkg/audiopreproc/ -run "TestCheckFFmpeg|TestRunFFmpeg" -v`
Expected: PASS

**Step 5: Коммит**

Название: `feat(audiopreproc): обёртка над ffmpeg exec.Command`

---

## Task 3: Пресеты

**Files:**
- Create: `pkg/audiopreproc/preset.go`
- Create: `pkg/audiopreproc/preset_test.go`

**Step 1: Написать failing test**

`pkg/audiopreproc/preset_test.go`:
```go
package audiopreproc

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGetPreset_KnownPresets(t *testing.T) {
	presets := []string{"quiet_room", "lecture_hall", "noisy"}
	for _, name := range presets {
		p, err := GetPreset(name)
		require.NoError(t, err, "пресет %s должен существовать", name)
		assert.NotEmpty(t, p.Name)
		assert.Greater(t, p.Normalize.IntegratedLoudness, -30.0)
		assert.Greater(t, p.Denoise.NoiseReduction, 0.0)
		assert.Less(t, p.Silence.Threshold, 0.0)
		assert.Greater(t, p.Silence.MinDuration, 0.0)
	}
}

func TestGetPreset_Unknown(t *testing.T) {
	_, err := GetPreset("nonexistent")
	assert.Error(t, err)
}

func TestPresetCustom(t *testing.T) {
	p := CustomPreset(NormalizeParams{
		IntegratedLoudness: -20,
		LoudnessRange:     9,
		TruePeak:          -1,
	}, DenoiseParams{
		NoiseReduction: 20,
	}, SilenceParams{
		Threshold:   -35,
		MinDuration: 3,
	})
	assert.Equal(t, "custom", p.Name)
	assert.Equal(t, -20.0, p.Normalize.IntegratedLoudness)
}
```

**Step 2: Запустить тест, убедиться что не компилируется**

Run: `go test ./pkg/audiopreproc/ -run TestGetPreset -v`
Expected: FAIL

**Step 3: Реализовать пресеты**

`pkg/audiopreproc/preset.go`:
```go
package audiopreproc

import "fmt"

// NormalizeParams — параметры нормализации громкости (loudnorm)
type NormalizeParams struct {
	IntegratedLoudness float64 // I, целевая громкость в LUFS (обычно -24)
	LoudnessRange      float64 // LRA, допустимый разброс в LU
	TruePeak           float64 // TP, максимальный пик в dBTP
}

// DenoiseParams — параметры шумоподавления (afftdn)
type DenoiseParams struct {
	NoiseReduction float64 // nr, уровень подавления в dB
}

// SilenceParams — параметры детекции тишины
type SilenceParams struct {
	Threshold   float64 // порог в dB (например -35)
	MinDuration float64 // минимальная длительность тишины в секундах
}

// Preset — набор параметров для всех этапов обработки
type Preset struct {
	Name        string
	Description string
	Normalize   NormalizeParams
	Denoise     DenoiseParams
	Silence     SilenceParams
}

var presets = map[string]Preset{
	"quiet_room": {
		Name:        "quiet_room",
		Description: "Тихое помещение, диктофон рядом",
		Normalize:   NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 7, TruePeak: -2},
		Denoise:     DenoiseParams{NoiseReduction: 8},
		Silence:     SilenceParams{Threshold: -40, MinDuration: 5},
	},
	"lecture_hall": {
		Name:        "lecture_hall",
		Description: "Аудитория среднего размера",
		Normalize:   NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 11, TruePeak: -2},
		Denoise:     DenoiseParams{NoiseReduction: 15},
		Silence:     SilenceParams{Threshold: -35, MinDuration: 5},
	},
	"noisy": {
		Name:        "noisy",
		Description: "Шумное помещение, запись издалека",
		Normalize:   NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 14, TruePeak: -2},
		Denoise:     DenoiseParams{NoiseReduction: 25},
		Silence:     SilenceParams{Threshold: -30, MinDuration: 4},
	},
}

// GetPreset — возвращает пресет по имени
func GetPreset(name string) (Preset, error) {
	p, ok := presets[name]
	if !ok {
		return Preset{}, fmt.Errorf("неизвестный пресет: %q", name)
	}
	return p, nil
}

// CustomPreset — создаёт пресет с пользовательскими параметрами
func CustomPreset(norm NormalizeParams, denoise DenoiseParams, silence SilenceParams) Preset {
	return Preset{
		Name:        "custom",
		Description: "Ручная настройка",
		Normalize:   norm,
		Denoise:     denoise,
		Silence:     silence,
	}
}
```

**Step 4: Запустить тесты**

Run: `go test ./pkg/audiopreproc/ -run "TestGetPreset|TestPresetCustom" -v`
Expected: PASS

**Step 5: Коммит**

Название: `feat(audiopreproc): пресеты quiet_room, lecture_hall, noisy`

---

## Task 4: Нормализация громкости (loudnorm)

**Files:**
- Create: `pkg/audiopreproc/normalize.go`
- Create: `pkg/audiopreproc/normalize_test.go`

**Зависимость:** нужен тестовый аудиофайл `pkg/audiopreproc/testdata/sample.mp3` (короткий файл, 5-10 сек)

**Step 1: Создать тестовый аудиофайл через ffmpeg (синтетический)**

Run: `mkdir -p /home/krivonosov/projects/LectureLog/pkg/audiopreproc/testdata && ffmpeg -y -f lavfi -i "sine=frequency=440:duration=5" -af "volume=-20dB" -ar 44100 /home/krivonosov/projects/LectureLog/pkg/audiopreproc/testdata/sample.mp3`

Это создаст тихий 5-секундный тон для тестирования нормализации. Реальные аудио будут использоваться позже.

**Step 2: Написать failing test**

`pkg/audiopreproc/normalize_test.go`:
```go
package audiopreproc

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNormalize(t *testing.T) {
	ctx := context.Background()

	input := filepath.Join("testdata", "sample.mp3")
	output := filepath.Join(t.TempDir(), "normalized.mp3")

	params := NormalizeParams{
		IntegratedLoudness: -24,
		LoudnessRange:      7,
		TruePeak:           -2,
	}

	err := normalize(ctx, input, output, params)
	require.NoError(t, err)

	// Проверяем что выходной файл создан и не пустой
	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))
}

func TestNormalize_InvalidInput(t *testing.T) {
	ctx := context.Background()
	output := filepath.Join(t.TempDir(), "out.mp3")
	params := NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 7, TruePeak: -2}

	err := normalize(ctx, "nonexistent.mp3", output, params)
	assert.Error(t, err)
}
```

**Step 3: Запустить тест, убедиться что не компилируется**

Run: `go test ./pkg/audiopreproc/ -run TestNormalize -v`
Expected: FAIL

**Step 4: Реализовать двухпроходную нормализацию**

`pkg/audiopreproc/normalize.go`:
```go
package audiopreproc

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// loudnormStats — статистика первого прохода loudnorm
type loudnormStats struct {
	InputI            string `json:"input_i"`
	InputTP           string `json:"input_tp"`
	InputLRA          string `json:"input_lra"`
	InputThresh       string `json:"input_thresh"`
	OutputI           string `json:"output_i"`
	OutputTP          string `json:"output_tp"`
	OutputLRA         string `json:"output_lra"`
	OutputThresh      string `json:"output_thresh"`
	NormalizationType string `json:"normalization_type"`
	TargetOffset      string `json:"target_offset"`
}

// normalize — двухпроходная нормализация громкости через loudnorm (EBU R128)
func normalize(ctx context.Context, input, output string, params NormalizeParams) error {
	// Первый проход: анализ
	firstPassFilter := fmt.Sprintf(
		"loudnorm=I=%.1f:LRA=%.1f:TP=%.1f:print_format=json",
		params.IntegratedLoudness, params.LoudnessRange, params.TruePeak,
	)
	stderr, err := runFFmpeg(ctx, []string{
		"-i", input,
		"-af", firstPassFilter,
		"-f", "null", "-",
	})
	if err != nil {
		return fmt.Errorf("нормализация (проход 1): %w", err)
	}

	stats, err := parseLoudnormStats(stderr)
	if err != nil {
		return fmt.Errorf("парсинг статистики loudnorm: %w", err)
	}

	// Второй проход: применение с точными параметрами
	secondPassFilter := fmt.Sprintf(
		"loudnorm=I=%.1f:LRA=%.1f:TP=%.1f:measured_I=%s:measured_LRA=%s:measured_TP=%s:measured_thresh=%s:offset=%s:linear=true:print_format=summary",
		params.IntegratedLoudness, params.LoudnessRange, params.TruePeak,
		stats.InputI, stats.InputLRA, stats.InputTP, stats.InputThresh, stats.TargetOffset,
	)
	_, err = runFFmpeg(ctx, []string{
		"-i", input,
		"-af", secondPassFilter,
		"-y", output,
	})
	if err != nil {
		return fmt.Errorf("нормализация (проход 2): %w", err)
	}
	return nil
}

// parseLoudnormStats — извлекает JSON-статистику из stderr ffmpeg
func parseLoudnormStats(stderr string) (*loudnormStats, error) {
	// ffmpeg выводит JSON-блок после строки [Parsed_loudnorm_...]
	re := regexp.MustCompile(`(?s)\{[^{}]*"input_i"[^{}]*\}`)
	match := re.FindString(stderr)
	if match == "" {
		return nil, fmt.Errorf("не найден JSON-вывод loudnorm в stderr")
	}

	// Убираем возможные переносы строк внутри JSON
	match = strings.ReplaceAll(match, "\n", "")
	match = strings.ReplaceAll(match, "\r", "")

	var stats loudnormStats
	if err := json.Unmarshal([]byte(match), &stats); err != nil {
		return nil, fmt.Errorf("ошибка парсинга JSON loudnorm: %w", err)
	}
	return &stats, nil
}
```

**Step 5: Запустить тесты**

Run: `go test ./pkg/audiopreproc/ -run TestNormalize -v`
Expected: PASS

**Step 6: Коммит**

Название: `feat(audiopreproc): двухпроходная нормализация громкости loudnorm`

---

## Task 5: Шумоподавление (afftdn)

**Files:**
- Create: `pkg/audiopreproc/denoise.go`
- Create: `pkg/audiopreproc/denoise_test.go`

**Step 1: Создать тестовый аудиофайл с шумом**

Run: `ffmpeg -y -f lavfi -i "sine=frequency=440:duration=5" -f lavfi -i "anoisesrc=d=5:c=pink:a=0.05" -filter_complex "[0][1]amix=inputs=2:duration=first" -ar 44100 /home/krivonosov/projects/LectureLog/pkg/audiopreproc/testdata/noisy_sample.mp3`

**Step 2: Написать failing test**

`pkg/audiopreproc/denoise_test.go`:
```go
package audiopreproc

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestDenoise(t *testing.T) {
	ctx := context.Background()

	input := filepath.Join("testdata", "noisy_sample.mp3")
	output := filepath.Join(t.TempDir(), "denoised.mp3")

	params := DenoiseParams{NoiseReduction: 15}

	err := denoise(ctx, input, output, params)
	require.NoError(t, err)

	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))
}

func TestDenoise_InvalidInput(t *testing.T) {
	ctx := context.Background()
	output := filepath.Join(t.TempDir(), "out.mp3")

	err := denoise(ctx, "nonexistent.mp3", output, DenoiseParams{NoiseReduction: 15})
	assert.Error(t, err)
}
```

**Step 3: Запустить тест, убедиться что не компилируется**

Run: `go test ./pkg/audiopreproc/ -run TestDenoise -v`
Expected: FAIL

**Step 4: Реализовать шумоподавление**

`pkg/audiopreproc/denoise.go`:
```go
package audiopreproc

import (
	"context"
	"fmt"
)

// denoise — шумоподавление через фильтр afftdn
func denoise(ctx context.Context, input, output string, params DenoiseParams) error {
	filter := fmt.Sprintf("afftdn=nr=%.0f", params.NoiseReduction)

	_, err := runFFmpeg(ctx, []string{
		"-i", input,
		"-af", filter,
		"-y", output,
	})
	if err != nil {
		return fmt.Errorf("шумоподавление: %w", err)
	}
	return nil
}
```

**Step 5: Запустить тесты**

Run: `go test ./pkg/audiopreproc/ -run TestDenoise -v`
Expected: PASS

**Step 6: Коммит**

Название: `feat(audiopreproc): шумоподавление afftdn`

---

## Task 6: Детекция и вырезание тишины + TimeMap

**Files:**
- Create: `pkg/audiopreproc/silence.go`
- Create: `pkg/audiopreproc/silence_test.go`
- Modify: `pkg/audiopreproc/audiopreproc.go` (добавить методы TimeMap)

**Step 1: Создать тестовый аудиофайл с паузами**

Run: `ffmpeg -y -f lavfi -i "sine=frequency=440:duration=3" -f lavfi -i "anullsrc=d=7" -f lavfi -i "sine=frequency=660:duration=3" -filter_complex "[0][1][2]concat=n=3:v=0:a=1" -ar 44100 /home/krivonosov/projects/LectureLog/pkg/audiopreproc/testdata/with_silence.mp3`

Это создаст файл: 3 сек тон → 7 сек тишина → 3 сек тон (всего 13 сек). Тишина 7 сек должна быть вырезана при пороге 5 сек.

**Step 2: Написать failing тесты**

`pkg/audiopreproc/silence_test.go`:
```go
package audiopreproc

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestDetectSilence(t *testing.T) {
	ctx := context.Background()
	input := filepath.Join("testdata", "with_silence.mp3")

	params := SilenceParams{Threshold: -30, MinDuration: 5}

	segments, err := detectSilence(ctx, input, params)
	require.NoError(t, err)

	// Должен быть найден хотя бы один сегмент тишины
	require.GreaterOrEqual(t, len(segments), 1)

	// Тишина начинается примерно на 3-й секунде
	assert.InDelta(t, 3.0, segments[0].Start.Seconds(), 0.5)
}

func TestRemoveSilence(t *testing.T) {
	ctx := context.Background()
	input := filepath.Join("testdata", "with_silence.mp3")
	output := filepath.Join(t.TempDir(), "trimmed.mp3")

	params := SilenceParams{Threshold: -30, MinDuration: 5}

	tm, err := removeSilence(ctx, input, output, params)
	require.NoError(t, err)

	// Выходной файл создан
	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))

	// TimeMap должен содержать сегменты
	require.NotNil(t, tm)
	require.Greater(t, len(tm.Segments), 0)
}

func TestTimeMap_ToOriginal(t *testing.T) {
	// Сценарий: оригинал 0-3 (звук), 3-10 (тишина вырезана), 10-13 (звук)
	// После обрезки: 0-3 (первый кусок), 3-6 (второй кусок)
	tm := &TimeMap{
		Segments: []Segment{
			{OriginalStart: 0, OriginalEnd: 3 * time.Second, ProcessedStart: 0, ProcessedEnd: 3 * time.Second},
			{OriginalStart: 10 * time.Second, OriginalEnd: 13 * time.Second, ProcessedStart: 3 * time.Second, ProcessedEnd: 6 * time.Second},
		},
	}

	// 1 секунда в обработанном → 1 секунда в оригинале
	orig := tm.ToOriginal(1 * time.Second)
	assert.Equal(t, 1*time.Second, orig)

	// 4 секунды в обработанном → 11 секунд в оригинале (внутри второго сегмента)
	orig = tm.ToOriginal(4 * time.Second)
	assert.Equal(t, 11*time.Second, orig)
}

func TestTimeMap_ToProcessed(t *testing.T) {
	tm := &TimeMap{
		Segments: []Segment{
			{OriginalStart: 0, OriginalEnd: 3 * time.Second, ProcessedStart: 0, ProcessedEnd: 3 * time.Second},
			{OriginalStart: 10 * time.Second, OriginalEnd: 13 * time.Second, ProcessedStart: 3 * time.Second, ProcessedEnd: 6 * time.Second},
		},
	}

	// 1 секунда в оригинале → 1 секунда в обработанном
	proc := tm.ToProcessed(1 * time.Second)
	assert.Equal(t, 1*time.Second, proc)

	// 11 секунд в оригинале → 4 секунды в обработанном
	proc = tm.ToProcessed(11 * time.Second)
	assert.Equal(t, 4*time.Second, proc)

	// 5 секунд в оригинале (внутри тишины) → конец первого сегмента (3 сек)
	proc = tm.ToProcessed(5 * time.Second)
	assert.Equal(t, 3*time.Second, proc)
}
```

**Step 3: Запустить тесты, убедиться что не компилируется**

Run: `go test ./pkg/audiopreproc/ -run "TestDetectSilence|TestRemoveSilence|TestTimeMap" -v`
Expected: FAIL

**Step 4: Добавить методы TimeMap в audiopreproc.go**

Добавить в `pkg/audiopreproc/audiopreproc.go`:
```go
// ToOriginal — конвертирует таймкод из обработанного аудио в оригинальное
func (tm *TimeMap) ToOriginal(processed time.Duration) time.Duration {
	for _, seg := range tm.Segments {
		if processed >= seg.ProcessedStart && processed <= seg.ProcessedEnd {
			offset := processed - seg.ProcessedStart
			return seg.OriginalStart + offset
		}
	}
	// За пределами всех сегментов — возвращаем конец последнего
	if len(tm.Segments) > 0 {
		last := tm.Segments[len(tm.Segments)-1]
		return last.OriginalEnd
	}
	return 0
}

// ToProcessed — конвертирует таймкод из оригинального аудио в обработанное
func (tm *TimeMap) ToProcessed(original time.Duration) time.Duration {
	for _, seg := range tm.Segments {
		if original >= seg.OriginalStart && original <= seg.OriginalEnd {
			offset := original - seg.OriginalStart
			return seg.ProcessedStart + offset
		}
	}
	// Попали в вырезанный участок — возвращаем конец предыдущего сегмента
	for i := len(tm.Segments) - 1; i >= 0; i-- {
		if original > tm.Segments[i].OriginalEnd {
			return tm.Segments[i].ProcessedEnd
		}
	}
	return 0
}
```

**Step 5: Реализовать детекцию и вырезание тишины**

`pkg/audiopreproc/silence.go`:
```go
package audiopreproc

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// silenceSegment — обнаруженный сегмент тишины
type silenceSegment struct {
	Start    time.Duration
	End      time.Duration
	Duration time.Duration
}

// detectSilence — запускает silencedetect и парсит вывод
func detectSilence(ctx context.Context, input string, params SilenceParams) ([]silenceSegment, error) {
	filter := fmt.Sprintf("silencedetect=noise=%.0fdB:d=%.1f", params.Threshold, params.MinDuration)

	stderr, err := runFFmpeg(ctx, []string{
		"-i", input,
		"-af", filter,
		"-f", "null", "-",
	})
	if err != nil {
		return nil, fmt.Errorf("детекция тишины: %w", err)
	}

	return parseSilenceDetect(stderr)
}

// parseSilenceDetect — парсит вывод ffmpeg silencedetect
func parseSilenceDetect(stderr string) ([]silenceSegment, error) {
	// silence_start: 3.00
	// silence_end: 10.00 | silence_duration: 7.00
	reStart := regexp.MustCompile(`silence_start:\s*([\d.]+)`)
	reEnd := regexp.MustCompile(`silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)`)

	var segments []silenceSegment
	var currentStart float64
	hasStart := false

	for _, line := range strings.Split(stderr, "\n") {
		if m := reStart.FindStringSubmatch(line); m != nil {
			val, err := strconv.ParseFloat(m[1], 64)
			if err != nil {
				return nil, fmt.Errorf("ошибка парсинга silence_start: %w", err)
			}
			currentStart = val
			hasStart = true
		}
		if m := reEnd.FindStringSubmatch(line); m != nil && hasStart {
			end, err := strconv.ParseFloat(m[1], 64)
			if err != nil {
				return nil, fmt.Errorf("ошибка парсинга silence_end: %w", err)
			}
			dur, err := strconv.ParseFloat(m[2], 64)
			if err != nil {
				return nil, fmt.Errorf("ошибка парсинга silence_duration: %w", err)
			}
			segments = append(segments, silenceSegment{
				Start:    time.Duration(currentStart * float64(time.Second)),
				End:      time.Duration(end * float64(time.Second)),
				Duration: time.Duration(dur * float64(time.Second)),
			})
			hasStart = false
		}
	}

	return segments, nil
}

// removeSilence — вырезает тишину и строит TimeMap
func removeSilence(ctx context.Context, input, output string, params SilenceParams) (*TimeMap, error) {
	// Получаем длительность файла
	duration, err := getAudioDuration(ctx, input)
	if err != nil {
		return nil, err
	}

	// Детектируем сегменты тишины
	silences, err := detectSilence(ctx, input, params)
	if err != nil {
		return nil, err
	}

	// Если тишины нет — копируем файл, TimeMap 1:1
	if len(silences) == 0 {
		_, err := runFFmpeg(ctx, []string{"-i", input, "-c", "copy", "-y", output})
		if err != nil {
			return nil, fmt.Errorf("копирование файла: %w", err)
		}
		tm := &TimeMap{
			Segments: []Segment{{
				OriginalStart: 0, OriginalEnd: duration,
				ProcessedStart: 0, ProcessedEnd: duration,
			}},
		}
		return tm, nil
	}

	// Строим список звуковых сегментов (промежутки между тишиной)
	var soundSegments []Segment
	var cursor time.Duration
	var processedCursor time.Duration

	for _, s := range silences {
		if s.Start > cursor {
			segDur := s.Start - cursor
			soundSegments = append(soundSegments, Segment{
				OriginalStart:  cursor,
				OriginalEnd:    s.Start,
				ProcessedStart: processedCursor,
				ProcessedEnd:   processedCursor + segDur,
			})
			processedCursor += segDur
		}
		cursor = s.End
	}

	// Последний сегмент после последней тишины
	if cursor < duration {
		segDur := duration - cursor
		soundSegments = append(soundSegments, Segment{
			OriginalStart:  cursor,
			OriginalEnd:    duration,
			ProcessedStart: processedCursor,
			ProcessedEnd:   processedCursor + segDur,
		})
	}

	// Строим ffmpeg filter_complex для конкатенации сегментов
	var filterParts []string
	for i, seg := range soundSegments {
		startSec := seg.OriginalStart.Seconds()
		endSec := seg.OriginalEnd.Seconds()
		filterParts = append(filterParts,
			fmt.Sprintf("[0:a]atrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS[s%d]", startSec, endSec, i),
		)
	}

	// Конкатенация
	var concatInputs string
	for i := range soundSegments {
		concatInputs += fmt.Sprintf("[s%d]", i)
	}
	filterParts = append(filterParts,
		fmt.Sprintf("%sconcat=n=%d:v=0:a=1[out]", concatInputs, len(soundSegments)),
	)

	filterComplex := strings.Join(filterParts, ";")

	_, err = runFFmpeg(ctx, []string{
		"-i", input,
		"-filter_complex", filterComplex,
		"-map", "[out]",
		"-y", output,
	})
	if err != nil {
		return nil, fmt.Errorf("вырезание тишины: %w", err)
	}

	return &TimeMap{Segments: soundSegments}, nil
}

// getAudioDuration — получает длительность аудиофайла
func getAudioDuration(ctx context.Context, path string) (time.Duration, error) {
	jsonStr, err := probeFormat(ctx, path)
	if err != nil {
		return 0, err
	}

	// Парсим duration из JSON ffprobe
	re := regexp.MustCompile(`"duration"\s*:\s*"([\d.]+)"`)
	m := re.FindStringSubmatch(jsonStr)
	if m == nil {
		return 0, fmt.Errorf("не удалось извлечь duration из ffprobe")
	}

	sec, err := strconv.ParseFloat(m[1], 64)
	if err != nil {
		return 0, fmt.Errorf("ошибка парсинга duration: %w", err)
	}

	return time.Duration(sec * float64(time.Second)), nil
}
```

**Step 6: Запустить тесты**

Run: `go test ./pkg/audiopreproc/ -run "TestDetectSilence|TestRemoveSilence|TestTimeMap" -v`
Expected: PASS

**Step 7: Коммит**

Название: `feat(audiopreproc): детекция/вырезание тишины, TimeMap`

---

## Task 7: Pipeline — оркестрация этапов

**Files:**
- Create: `pkg/audiopreproc/pipeline.go`
- Create: `pkg/audiopreproc/pipeline_test.go`

**Step 1: Написать failing test**

`pkg/audiopreproc/pipeline_test.go`:
```go
package audiopreproc

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPipeline_AllSteps(t *testing.T) {
	ctx := context.Background()
	input := filepath.Join("testdata", "with_silence.mp3")
	output := filepath.Join(t.TempDir(), "result.mp3")

	preset, _ := GetPreset("lecture_hall")
	opts := PipelineOptions{
		Preset:    preset,
		Normalize: true,
		Denoise:   true,
		Silence:   true,
	}

	result, err := Process(ctx, input, output, opts)
	require.NoError(t, err)
	assert.Equal(t, output, result.OutputPath)
	assert.NotNil(t, result.TimeMap)

	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))
}

func TestPipeline_NormalizeOnly(t *testing.T) {
	ctx := context.Background()
	input := filepath.Join("testdata", "sample.mp3")
	output := filepath.Join(t.TempDir(), "result.mp3")

	preset, _ := GetPreset("quiet_room")
	opts := PipelineOptions{
		Preset:    preset,
		Normalize: true,
		Denoise:   false,
		Silence:   false,
	}

	result, err := Process(ctx, input, output, opts)
	require.NoError(t, err)
	assert.Nil(t, result.TimeMap)

	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))
}

func TestPipeline_InvalidInput(t *testing.T) {
	ctx := context.Background()

	preset, _ := GetPreset("lecture_hall")
	opts := PipelineOptions{
		Preset:    preset,
		Normalize: true,
		Denoise:   true,
		Silence:   true,
	}

	_, err := Process(ctx, "nonexistent.mp3", "/tmp/out.mp3", opts)
	assert.Error(t, err)
}
```

**Step 2: Запустить тест, убедиться что не компилируется**

Run: `go test ./pkg/audiopreproc/ -run TestPipeline -v`
Expected: FAIL

**Step 3: Реализовать pipeline**

`pkg/audiopreproc/pipeline.go`:
```go
package audiopreproc

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
)

// PipelineOptions — настройки пайплайна обработки
type PipelineOptions struct {
	Preset    Preset
	Normalize bool // включить нормализацию
	Denoise   bool // включить шумоподавление
	Silence   bool // включить вырезание тишины
}

// Process — запускает пайплайн обработки аудио
// Этапы выполняются последовательно: нормализация → шумоподавление → вырезание тишины
// Каждый этап можно отключить через PipelineOptions
func Process(ctx context.Context, input, output string, opts PipelineOptions) (*Result, error) {
	// Проверяем что ffmpeg доступен
	if err := CheckFFmpeg(ctx); err != nil {
		return nil, err
	}

	// Проверяем входной файл
	if _, err := os.Stat(input); err != nil {
		return nil, fmt.Errorf("входной файл: %w", err)
	}

	// Создаём временную директорию для промежуточных файлов
	tmpDir, err := os.MkdirTemp("", "audiopreproc-*")
	if err != nil {
		return nil, fmt.Errorf("создание временной директории: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	current := input
	step := 0

	// Вспомогательная функция для генерации пути промежуточного файла
	nextTmp := func() string {
		step++
		return filepath.Join(tmpDir, fmt.Sprintf("step%d.mp3", step))
	}

	// Этап 1: Нормализация
	if opts.Normalize {
		out := nextTmp()
		if err := normalize(ctx, current, out, opts.Preset.Normalize); err != nil {
			return nil, fmt.Errorf("этап нормализации: %w", err)
		}
		current = out
	}

	// Этап 2: Шумоподавление
	if opts.Denoise {
		out := nextTmp()
		if err := denoise(ctx, current, out, opts.Preset.Denoise); err != nil {
			return nil, fmt.Errorf("этап шумоподавления: %w", err)
		}
		current = out
	}

	// Этап 3: Вырезание тишины
	var timeMap *TimeMap
	if opts.Silence {
		tm, err := removeSilence(ctx, current, output, opts.Preset.Silence)
		if err != nil {
			return nil, fmt.Errorf("этап вырезания тишины: %w", err)
		}
		timeMap = tm
	} else {
		// Копируем результат в выходной файл
		_, err := runFFmpeg(ctx, []string{"-i", current, "-c", "copy", "-y", output})
		if err != nil {
			return nil, fmt.Errorf("копирование результата: %w", err)
		}
	}

	return &Result{
		OutputPath: output,
		TimeMap:    timeMap,
	}, nil
}
```

**Step 4: Запустить тесты**

Run: `go test ./pkg/audiopreproc/ -run TestPipeline -v`
Expected: PASS

**Step 5: Коммит**

Название: `feat(audiopreproc): pipeline — оркестрация этапов обработки`

---

## Task 8: CLI-обёртка

**Files:**
- Create: `pkg/audiopreproc/cmd/audiopreproc/main.go`

**Step 1: Реализовать CLI**

`pkg/audiopreproc/cmd/audiopreproc/main.go`:
```go
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/LectureLog/LectureLog/pkg/audiopreproc"
)

func main() {
	// Флаги
	preset := flag.String("preset", "lecture_hall", "пресет: quiet_room, lecture_hall, noisy, custom")
	noNormalize := flag.Bool("no-normalize", false, "отключить нормализацию громкости")
	noDenoise := flag.Bool("no-denoise", false, "отключить шумоподавление")
	noSilence := flag.Bool("no-silence", false, "отключить вырезание тишины")

	// Кастомные параметры
	nr := flag.Float64("nr", 0, "уровень шумоподавления в dB (для --preset custom)")
	silenceThresh := flag.Float64("silence-thresh", 0, "порог тишины в dB (для --preset custom)")
	silenceDur := flag.Float64("silence-dur", 0, "минимальная длительность тишины в секундах (для --preset custom)")

	// Вывод TimeMap
	timeMapPath := flag.String("timemap", "", "путь для сохранения TimeMap в JSON")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: audiopreproc [опции] <input> <output>\n\n")
		fmt.Fprintf(os.Stderr, "Препроцессинг аудио: нормализация, шумоподавление, вырезание тишины.\n\n")
		fmt.Fprintf(os.Stderr, "Опции:\n")
		flag.PrintDefaults()
		fmt.Fprintf(os.Stderr, "\nПримеры:\n")
		fmt.Fprintf(os.Stderr, "  audiopreproc --preset lecture_hall input.mp3 output.mp3\n")
		fmt.Fprintf(os.Stderr, "  audiopreproc --preset noisy --no-silence input.mp3 output.mp3\n")
		fmt.Fprintf(os.Stderr, "  audiopreproc --preset custom --nr 20 --silence-thresh -35 input.mp3 output.mp3\n")
	}

	flag.Parse()

	if flag.NArg() != 2 {
		flag.Usage()
		os.Exit(1)
	}

	input := flag.Arg(0)
	output := flag.Arg(1)

	// Получаем пресет
	var p audiopreproc.Preset
	var err error

	if *preset == "custom" {
		p = audiopreproc.CustomPreset(
			audiopreproc.NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 7, TruePeak: -2},
			audiopreproc.DenoiseParams{NoiseReduction: *nr},
			audiopreproc.SilenceParams{Threshold: *silenceThresh, MinDuration: *silenceDur},
		)
	} else {
		p, err = audiopreproc.GetPreset(*preset)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Ошибка: %v\n", err)
			os.Exit(1)
		}
	}

	opts := audiopreproc.PipelineOptions{
		Preset:    p,
		Normalize: !*noNormalize,
		Denoise:   !*noDenoise,
		Silence:   !*noSilence,
	}

	// Контекст с обработкой сигналов
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	fmt.Fprintf(os.Stderr, "Обработка: %s → %s (пресет: %s)\n", input, output, p.Name)

	result, err := audiopreproc.Process(ctx, input, output, opts)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Ошибка: %v\n", err)
		os.Exit(1)
	}

	fmt.Fprintf(os.Stderr, "Готово: %s\n", result.OutputPath)

	// Сохранение TimeMap если запрошено
	if *timeMapPath != "" && result.TimeMap != nil {
		data, err := json.MarshalIndent(result.TimeMap, "", "  ")
		if err != nil {
			fmt.Fprintf(os.Stderr, "Ошибка сериализации TimeMap: %v\n", err)
			os.Exit(1)
		}
		if err := os.WriteFile(*timeMapPath, data, 0644); err != nil {
			fmt.Fprintf(os.Stderr, "Ошибка записи TimeMap: %v\n", err)
			os.Exit(1)
		}
		fmt.Fprintf(os.Stderr, "TimeMap сохранён: %s\n", *timeMapPath)
	}
}
```

**Step 2: Собрать CLI**

Run: `go build -o /tmp/audiopreproc ./pkg/audiopreproc/cmd/audiopreproc/`
Expected: успешная сборка

**Step 3: Проверить help**

Run: `/tmp/audiopreproc --help`
Expected: вывод справки с описанием опций

**Step 4: Проверить на тестовом файле**

Run: `/tmp/audiopreproc --preset lecture_hall --timemap /tmp/timemap.json pkg/audiopreproc/testdata/with_silence.mp3 /tmp/test_output.mp3`
Expected: "Готово: /tmp/test_output.mp3" + файл TimeMap

**Step 5: Коммит**

Название: `feat(audiopreproc): CLI-обёртка`

---

## Task 9: Запуск всех тестов + .gitignore

**Files:**
- Create: `.gitignore`
- Create: `pkg/audiopreproc/testdata/.gitkeep`

**Step 1: Создать .gitignore**

`.gitignore`:
```
# Бинарники
/bin/
*.exe

# Временные файлы
*.tmp
*.swp

# IDE
.idea/
.vscode/
*.code-workspace

# Go
/vendor/

# Тестовые аудиофайлы (синтетические генерируются ffmpeg в тестах)
pkg/audiopreproc/testdata/*.mp3
!pkg/audiopreproc/testdata/.gitkeep
```

**Step 2: Запустить все тесты**

Run: `go test ./pkg/audiopreproc/ -v -count=1`
Expected: все тесты PASS

**Step 3: Коммит**

Название: `chore: .gitignore, финализация тестов audiopreproc`

---

## Итого: 9 задач

| # | Что | Файлы |
|---|-----|-------|
| 1 | Инициализация Go-модуля, базовые типы | `go.mod`, `audiopreproc.go` |
| 2 | Обёртка над ffmpeg | `ffmpeg.go` |
| 3 | Пресеты | `preset.go` |
| 4 | Нормализация громкости | `normalize.go` |
| 5 | Шумоподавление | `denoise.go` |
| 6 | Детекция/вырезание тишины + TimeMap | `silence.go`, `audiopreproc.go` |
| 7 | Pipeline | `pipeline.go` |
| 8 | CLI-обёртка | `cmd/audiopreproc/main.go` |
| 9 | Тесты + .gitignore | `.gitignore` |
