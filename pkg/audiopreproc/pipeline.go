package audiopreproc

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
)

// PipelineOptions — настройки пайплайна обработки
type PipelineOptions struct {
	Preset    Preset
	Normalize bool // включить нормализацию
	Denoise   bool // включить шумоподавление
	Silence   bool // включить вырезание тишины
}

// Process — запускает пайплайн обработки аудио
// Этапы выполняются последовательно: нормализация → шумоподавление → вырезание тишины
// Каждый этап можно отключить через PipelineOptions
func Process(ctx context.Context, input, output string, opts PipelineOptions) (*Result, error) {
	// Проверяем что ffmpeg доступен
	if err := CheckFFmpeg(ctx); err != nil {
		return nil, err
	}

	// Проверяем входной файл
	if _, err := os.Stat(input); err != nil {
		return nil, fmt.Errorf("входной файл: %w", err)
	}

	// Создаём временную директорию для промежуточных файлов
	tmpDir, err := os.MkdirTemp("", "audiopreproc-*")
	if err != nil {
		return nil, fmt.Errorf("создание временной директории: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	current := input
	step := 0

	// Вспомогательная функция для генерации пути промежуточного файла
	nextTmp := func() string {
		step++
		return filepath.Join(tmpDir, fmt.Sprintf("step%d.mp3", step))
	}

	// Этап 1: Нормализация
	if opts.Normalize {
		out := nextTmp()
		if err := normalize(ctx, current, out, opts.Preset.Normalize); err != nil {
			return nil, fmt.Errorf("этап нормализации: %w", err)
		}
		current = out
	}

	// Этап 2: Шумоподавление
	if opts.Denoise {
		out := nextTmp()
		if err := denoise(ctx, current, out); err != nil {
			return nil, fmt.Errorf("этап шумоподавления: %w", err)
		}
		current = out
	}

	// Этап 3: Вырезание тишины
	var timeMap *TimeMap
	if opts.Silence {
		tm, err := removeSilence(ctx, current, output, opts.Preset.Silence)
		if err != nil {
			return nil, fmt.Errorf("этап вырезания тишины: %w", err)
		}
		timeMap = tm
	} else {
		// Копируем результат в выходной файл
		_, err := runFFmpeg(ctx, []string{"-i", current, "-c", "copy", "-y", output})
		if err != nil {
			return nil, fmt.Errorf("копирование результата: %w", err)
		}
	}

	return &Result{
		OutputPath: output,
		TimeMap:    timeMap,
	}, nil
}
