package audiopreproc

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestProcess(t *testing.T) {
	if testing.Short() {
		t.Skip("пропускаем интеграционный тест в short-режиме")
	}

	if _, err := execLookPath("python3"); err != nil {
		t.Skip("python3 недоступен")
	}

	if err := checkGradioClient(context.Background()); err != nil {
		t.Skip("gradio_client недоступен")
	}

	ctx := context.Background()
	input := filepath.Join("testdata", "sample.mp3")
	output := filepath.Join(t.TempDir(), "denoised.mp3")

	err := Process(ctx, input, output)
	require.NoError(t, err)

	info, err := os.Stat(output)
	require.NoError(t, err)
	assert.Greater(t, info.Size(), int64(0))
}
