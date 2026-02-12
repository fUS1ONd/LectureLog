package audiopreproc

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// Process — шумоподавление аудиофайла.
// Входной формат — любой (ffmpeg). Выходной — по расширению output.
func Process(ctx context.Context, input, output string) error {
	if err := CheckFFmpeg(ctx); err != nil {
		return err
	}

	if _, err := execLookPath("python3"); err != nil {
		return fmt.Errorf("python3 недоступен: %w", err)
	}

	if err := checkGradioClient(ctx); err != nil {
		return err
	}

	tmpDir, err := os.MkdirTemp("", "audiopreproc-v2-*")
	if err != nil {
		return fmt.Errorf("создание временной директории: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	inDir := filepath.Join(tmpDir, "in")
	outDir := filepath.Join(tmpDir, "out")
	if err := os.MkdirAll(inDir, 0o755); err != nil {
		return fmt.Errorf("создание in-директории: %w", err)
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return fmt.Errorf("создание out-директории: %w", err)
	}

	if err := splitChunks(ctx, input, inDir, 60); err != nil {
		return err
	}

	scriptPath, err := denoiseScriptPath()
	if err != nil {
		return err
	}

	cmd := exec.CommandContext(ctx, "python3", scriptPath, inDir, outDir)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("denoise.py: %w: %s", err, stderr.String())
	}

	if strings.TrimSpace(stdout.String()) != "OK" {
		return fmt.Errorf("denoise.py вернул неожиданный stdout: %q", stdout.String())
	}

	if err := concatChunks(ctx, outDir, output); err != nil {
		return err
	}

	return nil
}

func denoiseScriptPath() (string, error) {
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		return "", fmt.Errorf("не удалось определить путь к audiopreproc.go")
	}

	path := filepath.Join(filepath.Dir(thisFile), "scripts", "denoise.py")
	if _, err := os.Stat(path); err != nil {
		return "", fmt.Errorf("скрипт denoise.py не найден: %w", err)
	}

	return path, nil
}

func execLookPath(file string) (string, error) {
	return exec.LookPath(file)
}

func checkGradioClient(ctx context.Context) error {
	cmd := exec.CommandContext(ctx, "python3", "-c", "import gradio_client")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("python3 пакет gradio_client недоступен: %w", err)
	}
	return nil
}
