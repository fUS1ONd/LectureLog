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
