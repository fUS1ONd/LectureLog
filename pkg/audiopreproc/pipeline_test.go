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
