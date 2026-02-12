package audiopreproc

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// silenceSegment — обнаруженный сегмент тишины
type silenceSegment struct {
	Start    time.Duration
	End      time.Duration
	Duration time.Duration
}

// detectSilence — запускает silencedetect и парсит вывод
func detectSilence(ctx context.Context, input string, params SilenceParams) ([]silenceSegment, error) {
	filter := fmt.Sprintf("silencedetect=noise=%.0fdB:d=%.1f", params.Threshold, params.MinDuration)

	stderr, err := runFFmpeg(ctx, []string{
		"-i", input,
		"-af", filter,
		"-f", "null", "-",
	})
	if err != nil {
		return nil, fmt.Errorf("детекция тишины: %w", err)
	}

	return parseSilenceDetect(stderr)
}

// parseSilenceDetect — парсит вывод ffmpeg silencedetect
func parseSilenceDetect(stderr string) ([]silenceSegment, error) {
	reStart := regexp.MustCompile(`silence_start:\s*([\d.]+)`)
	reEnd := regexp.MustCompile(`silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)`)

	var segments []silenceSegment
	var currentStart float64
	hasStart := false

	for line := range strings.SplitSeq(stderr, "\n") {
		if m := reStart.FindStringSubmatch(line); m != nil {
			val, err := strconv.ParseFloat(m[1], 64)
			if err != nil {
				return nil, fmt.Errorf("ошибка парсинга silence_start: %w", err)
			}
			currentStart = val
			hasStart = true
		}
		if m := reEnd.FindStringSubmatch(line); m != nil && hasStart {
			end, err := strconv.ParseFloat(m[1], 64)
			if err != nil {
				return nil, fmt.Errorf("ошибка парсинга silence_end: %w", err)
			}
			dur, err := strconv.ParseFloat(m[2], 64)
			if err != nil {
				return nil, fmt.Errorf("ошибка парсинга silence_duration: %w", err)
			}
			segments = append(segments, silenceSegment{
				Start:    time.Duration(currentStart * float64(time.Second)),
				End:      time.Duration(end * float64(time.Second)),
				Duration: time.Duration(dur * float64(time.Second)),
			})
			hasStart = false
		}
	}

	return segments, nil
}

// removeSilence — вырезает тишину и строит TimeMap
func removeSilence(ctx context.Context, input, output string, params SilenceParams) (*TimeMap, error) {
	// Получаем длительность файла
	duration, err := getAudioDuration(ctx, input)
	if err != nil {
		return nil, err
	}

	// Детектируем сегменты тишины
	silences, err := detectSilence(ctx, input, params)
	if err != nil {
		return nil, err
	}

	// Если тишины нет — копируем файл, TimeMap 1:1
	if len(silences) == 0 {
		_, err := runFFmpeg(ctx, []string{"-i", input, "-c", "copy", "-y", output})
		if err != nil {
			return nil, fmt.Errorf("копирование файла: %w", err)
		}
		tm := &TimeMap{
			Segments: []Segment{{
				OriginalStart: 0, OriginalEnd: duration,
				ProcessedStart: 0, ProcessedEnd: duration,
			}},
		}
		return tm, nil
	}

	// Строим список звуковых сегментов (промежутки между тишиной)
	var soundSegments []Segment
	var cursor time.Duration
	var processedCursor time.Duration

	for _, s := range silences {
		if s.Start > cursor {
			segDur := s.Start - cursor
			soundSegments = append(soundSegments, Segment{
				OriginalStart:  cursor,
				OriginalEnd:    s.Start,
				ProcessedStart: processedCursor,
				ProcessedEnd:   processedCursor + segDur,
			})
			processedCursor += segDur
		}
		cursor = s.End
	}

	// Последний сегмент после последней тишины
	if cursor < duration {
		segDur := duration - cursor
		soundSegments = append(soundSegments, Segment{
			OriginalStart:  cursor,
			OriginalEnd:    duration,
			ProcessedStart: processedCursor,
			ProcessedEnd:   processedCursor + segDur,
		})
	}

	// Строим ffmpeg filter_complex для конкатенации сегментов
	var filterParts []string
	for i, seg := range soundSegments {
		startSec := seg.OriginalStart.Seconds()
		endSec := seg.OriginalEnd.Seconds()
		filterParts = append(filterParts,
			fmt.Sprintf("[0:a]atrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS[s%d]", startSec, endSec, i),
		)
	}

	// Конкатенация
	var concatBuilder strings.Builder
	for i := range soundSegments {
		fmt.Fprintf(&concatBuilder, "[s%d]", i)
	}
	concatInputs := concatBuilder.String()
	filterParts = append(filterParts,
		fmt.Sprintf("%sconcat=n=%d:v=0:a=1[out]", concatInputs, len(soundSegments)),
	)

	filterComplex := strings.Join(filterParts, ";")

	_, err = runFFmpeg(ctx, []string{
		"-i", input,
		"-filter_complex", filterComplex,
		"-map", "[out]",
		"-y", output,
	})
	if err != nil {
		return nil, fmt.Errorf("вырезание тишины: %w", err)
	}

	return &TimeMap{Segments: soundSegments}, nil
}

// getAudioDuration — получает длительность аудиофайла
func getAudioDuration(ctx context.Context, path string) (time.Duration, error) {
	jsonStr, err := probeFormat(ctx, path)
	if err != nil {
		return 0, err
	}

	// Парсим duration из JSON ffprobe
	re := regexp.MustCompile(`"duration"\s*:\s*"([\d.]+)"`)
	m := re.FindStringSubmatch(jsonStr)
	if m == nil {
		return 0, fmt.Errorf("не удалось извлечь duration из ffprobe")
	}

	sec, err := strconv.ParseFloat(m[1], 64)
	if err != nil {
		return 0, fmt.Errorf("ошибка парсинга duration: %w", err)
	}

	return time.Duration(sec * float64(time.Second)), nil
}
