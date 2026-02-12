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

	// 1 секунда в обработанном -> 1 секунда в оригинале
	orig := tm.ToOriginal(1 * time.Second)
	assert.Equal(t, 1*time.Second, orig)

	// 4 секунды в обработанном -> 11 секунд в оригинале (внутри второго сегмента)
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

	// 1 секунда в оригинале -> 1 секунда в обработанном
	proc := tm.ToProcessed(1 * time.Second)
	assert.Equal(t, 1*time.Second, proc)

	// 11 секунд в оригинале -> 4 секунды в обработанном
	proc = tm.ToProcessed(11 * time.Second)
	assert.Equal(t, 4*time.Second, proc)

	// 5 секунд в оригинале (внутри тишины) -> конец первого сегмента (3 сек)
	proc = tm.ToProcessed(5 * time.Second)
	assert.Equal(t, 3*time.Second, proc)
}
