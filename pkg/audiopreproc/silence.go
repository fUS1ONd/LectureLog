package audiopreproc

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// silenceSegment — обнаруженный сегмент тишины.
type silenceSegment struct {
	Start float64 // секунды
	End   float64 // секунды
}

// SilenceParams — параметры детекции тишины.
type SilenceParams struct {
	Threshold   float64 // порог в dB
	MinDuration float64 // минимальная длительность тишины в секундах
}

// DefaultSilenceParams — параметры тишины по умолчанию для пайплайна.
var DefaultSilenceParams = SilenceParams{
	Threshold:   -35,
	MinDuration: 5,
}

var (
	silenceStartRe = regexp.MustCompile(`silence_start:\s*([0-9]*\.?[0-9]+)`) //nolint:gochecknoglobals
	silenceEndRe   = regexp.MustCompile(`silence_end:\s*([0-9]*\.?[0-9]+)`)   //nolint:gochecknoglobals
)

// detectSilence — находит интервалы тишины в файле.
func detectSilence(ctx context.Context, input string, params SilenceParams) ([]silenceSegment, error) {
	filter := fmt.Sprintf("silencedetect=noise=%gdB:d=%g", params.Threshold, params.MinDuration)
	stderr, err := runFFmpeg(ctx,
		"-i", input,
		"-af", filter,
		"-f", "null",
		"-",
	)
	if err != nil {
		return nil, fmt.Errorf("детекция тишины: %w", err)
	}

	starts := silenceStartRe.FindAllStringSubmatch(stderr, -1)
	ends := silenceEndRe.FindAllStringSubmatch(stderr, -1)
	if len(starts) == 0 {
		return nil, nil
	}

	segments := make([]silenceSegment, 0, len(starts))
	for i := range starts {
		start, parseErr := strconv.ParseFloat(starts[i][1], 64)
		if parseErr != nil {
			return nil, fmt.Errorf("парсинг silence_start: %w", parseErr)
		}

		if i < len(ends) {
			end, endErr := strconv.ParseFloat(ends[i][1], 64)
			if endErr != nil {
				return nil, fmt.Errorf("парсинг silence_end: %w", endErr)
			}
			segments = append(segments, silenceSegment{Start: start, End: end})
			continue
		}

		dur, durErr := getAudioDuration(ctx, input)
		if durErr != nil {
			return nil, fmt.Errorf("длительность файла для незакрытой тишины: %w", durErr)
		}
		segments = append(segments, silenceSegment{Start: start, End: dur})
	}

	return segments, nil
}

// removeSilenceFromFile — удаляет тишину из одного файла.
func removeSilenceFromFile(ctx context.Context, input, output string, params SilenceParams) error {
	silences, err := detectSilence(ctx, input, params)
	if err != nil {
		return err
	}

	if len(silences) == 0 {
		_, copyErr := runFFmpeg(ctx, "-i", input, "-c", "copy", "-y", output)
		if copyErr != nil {
			return fmt.Errorf("копирование файла без тишины: %w", copyErr)
		}
		return nil
	}

	dur, err := getAudioDuration(ctx, input)
	if err != nil {
		return fmt.Errorf("длительность входного файла: %w", err)
	}

	if isAllSilence(silences, dur) {
		_, copyErr := runFFmpeg(ctx, "-i", input, "-c", "copy", "-y", output)
		if copyErr != nil {
			return fmt.Errorf("копирование полностью тихого файла: %w", copyErr)
		}
		return nil
	}

	type audioSegment struct {
		Start float64
		End   float64
	}

	audioSegments := make([]audioSegment, 0, len(silences)+1)
	current := 0.0
	for _, silence := range silences {
		if silence.Start > current {
			audioSegments = append(audioSegments, audioSegment{Start: current, End: minFloat(silence.Start, dur)})
		}
		if silence.End > current {
			current = silence.End
		}
	}
	if current < dur {
		audioSegments = append(audioSegments, audioSegment{Start: current, End: dur})
	}

	if len(audioSegments) == 0 {
		_, copyErr := runFFmpeg(ctx, "-i", input, "-c", "copy", "-y", output)
		if copyErr != nil {
			return fmt.Errorf("копирование файла без звуковых сегментов: %w", copyErr)
		}
		return nil
	}

	parts := make([]string, 0, len(audioSegments)+1)
	labels := make([]string, 0, len(audioSegments))
	for i, seg := range audioSegments {
		label := fmt.Sprintf("s%d", i)
		parts = append(parts, fmt.Sprintf("[0:a]atrim=start=%.6f:end=%.6f,asetpts=PTS-STARTPTS[%s]", seg.Start, seg.End, label))
		labels = append(labels, fmt.Sprintf("[%s]", label))
	}

	mapLabel := "[out]"
	if len(labels) == 1 {
		mapLabel = labels[0]
	} else {
		parts = append(parts, fmt.Sprintf("%sconcat=n=%d:v=0:a=1[out]", strings.Join(labels, ""), len(labels)))
	}

	_, err = runFFmpeg(ctx,
		"-i", input,
		"-filter_complex", strings.Join(parts, ";"),
		"-map", mapLabel,
		"-ar", "44100",
		"-ac", "1",
		"-c:a", "pcm_s16le",
		"-y", output,
	)
	if err != nil {
		return fmt.Errorf("вырезание тишины: %w", err)
	}

	return nil
}

// removeSilenceFromChunks — удаляет тишину из чанков, сохраняя имена файлов.
func removeSilenceFromChunks(ctx context.Context, inDir, outDir string, params SilenceParams) error {
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return fmt.Errorf("создание директории для обработанных чанков: %w", err)
	}

	chunks, err := filepath.Glob(filepath.Join(inDir, "chunk_*.wav"))
	if err != nil {
		return fmt.Errorf("поиск входных чанков: %w", err)
	}
	sort.Strings(chunks)

	for _, chunk := range chunks {
		outPath := filepath.Join(outDir, filepath.Base(chunk))
		if rmErr := removeSilenceFromFile(ctx, chunk, outPath, params); rmErr != nil {
			return fmt.Errorf("обработка %s: %w", filepath.Base(chunk), rmErr)
		}
	}

	return nil
}

func isAllSilence(silences []silenceSegment, duration float64) bool {
	if len(silences) == 0 {
		return false
	}
	first := silences[0]
	last := silences[len(silences)-1]
	return first.Start <= 0.05 && last.End >= duration-0.05
}

func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
