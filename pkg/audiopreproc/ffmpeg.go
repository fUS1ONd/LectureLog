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
