package audiopreproc

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
)

// Progress — событие прогресса обработки.
type Progress struct {
	Stage   string
	Current int
	Total   int
	Message string
}

var chunkProgressRe = regexp.MustCompile(`^\[(\d+)/(\d+)\]\s+(.+)$`) //nolint:gochecknoglobals

// Process — шумоподавление аудиофайла.
// Входной формат — любой (ffmpeg). Выходной — по расширению output.
func Process(ctx context.Context, input, output string) error {
	return process(ctx, input, output, nil)
}

// ProcessWithProgress — шумоподавление с колбэком прогресса.
func ProcessWithProgress(ctx context.Context, input, output string, onProgress func(Progress)) error {
	return process(ctx, input, output, onProgress)
}

func process(ctx context.Context, input, output string, onProgress func(Progress)) error {
	emit := func(p Progress) {
		if onProgress != nil {
			onProgress(p)
		}
	}

	emit(Progress{Stage: "check", Message: "Проверка зависимостей"})
	if err := CheckFFmpeg(ctx); err != nil {
		return err
	}

	if _, err := execLookPath("python3"); err != nil {
		return fmt.Errorf("python3 недоступен: %w", err)
	}

	if err := checkGradioClient(ctx); err != nil {
		return err
	}

	emit(Progress{Stage: "prepare", Message: "Подготовка временных директорий"})
	tmpDir, err := os.MkdirTemp("", "audiopreproc-v2-*")
	if err != nil {
		return fmt.Errorf("создание временной директории: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	inDir := filepath.Join(tmpDir, "in")
	trimmedDir := filepath.Join(tmpDir, "trimmed")
	outDir := filepath.Join(tmpDir, "out")
	if err := os.MkdirAll(inDir, 0o755); err != nil {
		return fmt.Errorf("создание in-директории: %w", err)
	}
	if err := os.MkdirAll(trimmedDir, 0o755); err != nil {
		return fmt.Errorf("создание trimmed-директории: %w", err)
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return fmt.Errorf("создание out-директории: %w", err)
	}

	emit(Progress{Stage: "split", Message: "Нарезка на чанки"})
	if err := splitChunks(ctx, input, inDir, 60); err != nil {
		return err
	}
	emit(Progress{Stage: "trim", Message: "Вырезание тишины"})
	if err := removeSilenceFromChunks(ctx, inDir, trimmedDir, DefaultSilenceParams); err != nil {
		return fmt.Errorf("вырезание тишины из чанков: %w", err)
	}

	scriptPath, err := denoiseScriptPath()
	if err != nil {
		return err
	}

	cmd := exec.CommandContext(ctx, "python3", scriptPath, trimmedDir, outDir)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &stdout
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		return fmt.Errorf("получение stderr denoise.py: %w", err)
	}

	emit(Progress{Stage: "denoise", Message: "Шумоподавление чанков"})
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("запуск denoise.py: %w", err)
	}
	if scanErr := consumePythonStderr(stderrPipe, &stderr, emit); scanErr != nil {
		return fmt.Errorf("чтение stderr denoise.py: %w", scanErr)
	}

	if err := cmd.Wait(); err != nil {
		return fmt.Errorf("denoise.py: %w: %s", err, stderr.String())
	}

	if !hasSuccessMarker(stdout.String()) {
		return fmt.Errorf("denoise.py вернул неожиданный stdout: %q", stdout.String())
	}

	emit(Progress{Stage: "concat", Message: "Склейка чанков"})
	if err := concatChunks(ctx, outDir, output); err != nil {
		return err
	}

	emit(Progress{Stage: "done", Message: "Готово"})
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

func consumePythonStderr(stderrPipe io.ReadCloser, stderr *bytes.Buffer, emit func(Progress)) error {
	scanner := bufio.NewScanner(stderrPipe)
	for scanner.Scan() {
		line := scanner.Text()
		stderr.WriteString(line)
		stderr.WriteString("\n")

		cur, total, name, ok := parseChunkProgressLine(line)
		if ok {
			emit(Progress{
				Stage:   "denoise",
				Current: cur,
				Total:   total,
				Message: name,
			})
		}
	}
	return scanner.Err()
}

// parseChunkProgressLine — парсит строку прогресса вида "[1/7] chunk_000.wav".
func parseChunkProgressLine(line string) (int, int, string, bool) {
	m := chunkProgressRe.FindStringSubmatch(strings.TrimSpace(line))
	if len(m) != 4 {
		return 0, 0, "", false
	}

	current, err := strconv.Atoi(m[1])
	if err != nil {
		return 0, 0, "", false
	}
	total, err := strconv.Atoi(m[2])
	if err != nil || total <= 0 {
		return 0, 0, "", false
	}

	return current, total, m[3], true
}

// hasSuccessMarker — проверяет, что stdout скрипта содержит маркер успешного завершения.
func hasSuccessMarker(stdout string) bool {
	for _, line := range strings.Split(stdout, "\n") {
		if strings.TrimSpace(line) == "OK" {
			return true
		}
	}
	return false
}
