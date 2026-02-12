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

func makeToneWithSilence(t *testing.T, path string) {
	t.Helper()
	ctx := context.Background()

	_, err := runFFmpeg(ctx,
		"-f", "lavfi",
		"-i", "sine=frequency=440:duration=3",
		"-f", "lavfi",
		"-i", "anullsrc=r=44100:cl=mono",
		"-f", "lavfi",
		"-i", "sine=frequency=440:duration=1",
		"-filter_complex", "[1:a]atrim=duration=6[silence];[0:a][silence][2:a]concat=n=3:v=0:a=1[out]",
		"-map", "[out]",
		"-ar", "44100",
		"-ac", "1",
		"-c:a", "pcm_s16le",
		"-y", path,
	)
	require.NoError(t, err)
}

func TestDetectSilence(t *testing.T) {
	ctx := context.Background()
	input := filepath.Join(t.TempDir(), "with_silence.wav")
	makeToneWithSilence(t, input)

	segments, err := detectSilence(ctx, input, DefaultSilenceParams)
	require.NoError(t, err)
	require.Len(t, segments, 1)

	assert.InDelta(t, 3.0, segments[0].Start, 0.25)
	assert.InDelta(t, 9.0, segments[0].End, 0.25)
}

func TestRemoveSilenceFromFile(t *testing.T) {
	ctx := context.Background()
	tmpDir := t.TempDir()
	input := filepath.Join(tmpDir, "with_silence.wav")
	output := filepath.Join(tmpDir, "trimmed.wav")
	makeToneWithSilence(t, input)

	err := removeSilenceFromFile(ctx, input, output, DefaultSilenceParams)
	require.NoError(t, err)

	dur, err := getAudioDuration(ctx, output)
	require.NoError(t, err)
	assert.InDelta(t, 4.0, dur, 0.35)
}

func TestRemoveSilenceFromFile_NoSilence(t *testing.T) {
	ctx := context.Background()
	tmpDir := t.TempDir()
	input := filepath.Join(tmpDir, "tone.wav")
	output := filepath.Join(tmpDir, "out.wav")

	_, err := runFFmpeg(ctx,
		"-f", "lavfi",
		"-i", "sine=frequency=700:duration=5",
		"-ar", "44100",
		"-ac", "1",
		"-c:a", "pcm_s16le",
		"-y", input,
	)
	require.NoError(t, err)

	err = removeSilenceFromFile(ctx, input, output, DefaultSilenceParams)
	require.NoError(t, err)

	dur, err := getAudioDuration(ctx, output)
	require.NoError(t, err)
	assert.InDelta(t, 5.0, dur, 0.25)
}

func TestRemoveSilenceFromChunks(t *testing.T) {
	ctx := context.Background()
	tmpDir := t.TempDir()
	inDir := filepath.Join(tmpDir, "in")
	outDir := filepath.Join(tmpDir, "out")
	require.NoError(t, os.MkdirAll(inDir, 0o755))

	for i := 0; i < 3; i++ {
		chunkPath := filepath.Join(inDir, fmt.Sprintf("chunk_%03d.wav", i))
		if i == 1 {
			makeToneWithSilence(t, chunkPath)
			continue
		}
		_, err := runFFmpeg(ctx,
			"-f", "lavfi",
			"-i", "sine=frequency=500:duration=2",
			"-ar", "44100",
			"-ac", "1",
			"-c:a", "pcm_s16le",
			"-y", chunkPath,
		)
		require.NoError(t, err)
	}

	err := removeSilenceFromChunks(ctx, inDir, outDir, DefaultSilenceParams)
	require.NoError(t, err)

	for i := 0; i < 3; i++ {
		path := filepath.Join(outDir, fmt.Sprintf("chunk_%03d.wav", i))
		_, err := os.Stat(path)
		require.NoError(t, err)
	}
}

func TestGetAudioDuration(t *testing.T) {
	ctx := context.Background()
	input := filepath.Join(t.TempDir(), "tone.wav")

	_, err := runFFmpeg(ctx,
		"-f", "lavfi",
		"-i", "sine=frequency=1000:duration=5",
		"-ar", "44100",
		"-ac", "1",
		"-c:a", "pcm_s16le",
		"-y", input,
	)
	require.NoError(t, err)

	dur, err := getAudioDuration(ctx, input)
	require.NoError(t, err)
	assert.InDelta(t, 5.0, dur, 0.2)
}
