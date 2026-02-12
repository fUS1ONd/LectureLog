package audiopreproc

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

// CheckFFmpeg — проверяет что ffmpeg доступен.
func CheckFFmpeg(ctx context.Context) error {
	cmd := exec.CommandContext(ctx, "ffmpeg", "-version")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("ffmpeg недоступен: %w", err)
	}
	return nil
}

// runFFmpeg — запускает ffmpeg с аргументами и возвращает stderr.
func runFFmpeg(ctx context.Context, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, "ffmpeg", args...)

	var stderr bytes.Buffer
	cmd.Stderr = &stderr

	err := cmd.Run()
	if err != nil {
		return stderr.String(), fmt.Errorf("ffmpeg %v: %w", args, err)
	}

	return stderr.String(), nil
}

// splitChunks — нарезает input на WAV-чанки по segmentSec секунд в outDir.
func splitChunks(ctx context.Context, input, outDir string, segmentSec int) error {
	if segmentSec <= 0 {
		return fmt.Errorf("segmentSec должен быть > 0")
	}

	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return fmt.Errorf("создание директории чанков: %w", err)
	}

	pattern := filepath.Join(outDir, "chunk_%03d.wav")
	_, err := runFFmpeg(ctx,
		"-i", input,
		"-f", "segment",
		"-segment_time", fmt.Sprintf("%d", segmentSec),
		"-ar", "44100",
		"-ac", "1",
		"-c:a", "pcm_s16le",
		"-y", pattern,
	)
	if err != nil {
		return fmt.Errorf("нарезка чанков: %w", err)
	}

	return nil
}

// concatChunks — склеивает WAV-чанки из inDir в output через concat demuxer.
func concatChunks(ctx context.Context, inDir, output string) error {
	chunks, err := filepath.Glob(filepath.Join(inDir, "chunk_*.wav"))
	if err != nil {
		return fmt.Errorf("поиск чанков: %w", err)
	}
	if len(chunks) == 0 {
		return fmt.Errorf("в директории %s нет чанков", inDir)
	}
	sort.Strings(chunks)

	listPath := filepath.Join(inDir, "concat.txt")
	var b strings.Builder
	for _, chunk := range chunks {
		b.WriteString("file '")
		b.WriteString(strings.ReplaceAll(chunk, "'", "'\\''"))
		b.WriteString("'\n")
	}

	if err := os.WriteFile(listPath, []byte(b.String()), 0o644); err != nil {
		return fmt.Errorf("запись concat-файла: %w", err)
	}

	_, err = runFFmpeg(ctx,
		"-f", "concat",
		"-safe", "0",
		"-i", listPath,
		"-c:a", "libmp3lame",
		"-q:a", "2",
		"-y", output,
	)
	if err != nil {
		return fmt.Errorf("склейка чанков: %w", err)
	}

	return nil
}
