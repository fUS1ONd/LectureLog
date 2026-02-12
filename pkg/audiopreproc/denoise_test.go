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

	err := denoise(ctx, input, output)
	require.NoError(t, err)

	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))
}

func TestDenoise_InvalidInput(t *testing.T) {
	ctx := context.Background()
	output := filepath.Join(t.TempDir(), "out.mp3")

	err := denoise(ctx, "nonexistent.mp3", output)
	assert.Error(t, err)
}
