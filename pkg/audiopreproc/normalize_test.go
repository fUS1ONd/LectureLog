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
