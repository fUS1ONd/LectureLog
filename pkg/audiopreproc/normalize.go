package audiopreproc

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// loudnormStats — статистика первого прохода loudnorm
type loudnormStats struct {
	InputI            string `json:"input_i"`
	InputTP           string `json:"input_tp"`
	InputLRA          string `json:"input_lra"`
	InputThresh       string `json:"input_thresh"`
	OutputI           string `json:"output_i"`
	OutputTP          string `json:"output_tp"`
	OutputLRA         string `json:"output_lra"`
	OutputThresh      string `json:"output_thresh"`
	NormalizationType string `json:"normalization_type"`
	TargetOffset      string `json:"target_offset"`
}

// normalize — двухпроходная нормализация громкости через loudnorm (EBU R128)
func normalize(ctx context.Context, input, output string, params NormalizeParams) error {
	// Первый проход: анализ
	firstPassFilter := fmt.Sprintf(
		"loudnorm=I=%.1f:LRA=%.1f:TP=%.1f:print_format=json",
		params.IntegratedLoudness, params.LoudnessRange, params.TruePeak,
	)
	stderr, err := runFFmpeg(ctx, []string{
		"-i", input,
		"-af", firstPassFilter,
		"-f", "null", "-",
	})
	if err != nil {
		return fmt.Errorf("нормализация (проход 1): %w", err)
	}

	stats, err := parseLoudnormStats(stderr)
	if err != nil {
		return fmt.Errorf("парсинг статистики loudnorm: %w", err)
	}

	// Второй проход: применение с точными параметрами
	secondPassFilter := fmt.Sprintf(
		"loudnorm=I=%.1f:LRA=%.1f:TP=%.1f:measured_I=%s:measured_LRA=%s:measured_TP=%s:measured_thresh=%s:offset=%s:linear=true:print_format=summary",
		params.IntegratedLoudness, params.LoudnessRange, params.TruePeak,
		stats.InputI, stats.InputLRA, stats.InputTP, stats.InputThresh, stats.TargetOffset,
	)
	_, err = runFFmpeg(ctx, []string{
		"-i", input,
		"-af", secondPassFilter,
		"-y", output,
	})
	if err != nil {
		return fmt.Errorf("нормализация (проход 2): %w", err)
	}
	return nil
}

// parseLoudnormStats — извлекает JSON-статистику из stderr ffmpeg
func parseLoudnormStats(stderr string) (*loudnormStats, error) {
	// ffmpeg выводит JSON-блок после строки [Parsed_loudnorm_...]
	re := regexp.MustCompile(`(?s)\{[^{}]*"input_i"[^{}]*\}`)
	match := re.FindString(stderr)
	if match == "" {
		return nil, fmt.Errorf("не найден JSON-вывод loudnorm в stderr")
	}

	// Убираем возможные переносы строк внутри JSON
	match = strings.ReplaceAll(match, "\n", "")
	match = strings.ReplaceAll(match, "\r", "")

	var stats loudnormStats
	if err := json.Unmarshal([]byte(match), &stats); err != nil {
		return nil, fmt.Errorf("ошибка парсинга JSON loudnorm: %w", err)
	}
	return &stats, nil
}
