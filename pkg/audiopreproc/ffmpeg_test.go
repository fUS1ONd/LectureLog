package audiopreproc

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCheckFFmpeg(t *testing.T) {
	err := CheckFFmpeg(context.Background())
	require.NoError(t, err)
}

func TestSplitChunks(t *testing.T) {
	ctx := context.Background()
	tmpDir := t.TempDir()
	input := filepath.Join(tmpDir, "input.wav")
	outDir := filepath.Join(tmpDir, "chunks")

	_, err := runFFmpeg(ctx,
		"-f", "lavfi",
		"-i", "sine=frequency=1000:duration=5",
		"-ar", "44100",
		"-ac", "1",
		"-c:a", "pcm_s16le",
		"-y", input,
	)
	require.NoError(t, err)

	err = splitChunks(ctx, input, outDir, 2)
	require.NoError(t, err)

	chunks, err := filepath.Glob(filepath.Join(outDir, "chunk_*.wav"))
	require.NoError(t, err)
	assert.Len(t, chunks, 3)
}

func TestConcatChunks(t *testing.T) {
	ctx := context.Background()
	tmpDir := t.TempDir()
	inDir := filepath.Join(tmpDir, "in")
	require.NoError(t, os.MkdirAll(inDir, 0o755))

	for i := 0; i < 2; i++ {
		chunkPath := filepath.Join(inDir, fmt.Sprintf("chunk_%03d.wav", i))
		_, err := runFFmpeg(ctx,
			"-f", "lavfi",
			"-i", "sine=frequency=1000:duration=1",
			"-ar", "44100",
			"-ac", "1",
			"-c:a", "pcm_s16le",
			"-y", chunkPath,
		)
		require.NoError(t, err)
	}

	output := filepath.Join(tmpDir, "out.mp3")
	err := concatChunks(ctx, inDir, output)
	require.NoError(t, err)

	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))
}
