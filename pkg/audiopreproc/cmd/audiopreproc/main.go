package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/LectureLog/LectureLog/pkg/audiopreproc"
)

func main() {
	flag.Usage = func() {
		_, _ = fmt.Fprintf(flag.CommandLine.Output(), "Использование: %s <input> <output>\n\n", os.Args[0])
		_, _ = fmt.Fprintln(flag.CommandLine.Output(), "Шумоподавление аудио через Resemble Enhance HF API.")
		_, _ = fmt.Fprintln(flag.CommandLine.Output(), "Требования: ffmpeg, python3, gradio_client.")
	}

	flag.Parse()
	if len(flag.Args()) != 2 {
		flag.Usage()
		os.Exit(2)
	}

	input := flag.Arg(0)
	output := flag.Arg(1)
	lastProgressLine := false

	onProgress := func(p audiopreproc.Progress) {
		switch p.Stage {
		case "check":
			if lastProgressLine {
				_, _ = fmt.Fprintln(os.Stderr)
				lastProgressLine = false
			}
			_, _ = fmt.Fprintln(os.Stderr, "[1/6] Проверка зависимостей")
		case "prepare":
			if lastProgressLine {
				_, _ = fmt.Fprintln(os.Stderr)
				lastProgressLine = false
			}
			_, _ = fmt.Fprintln(os.Stderr, "[2/6] Подготовка временных директорий")
		case "split":
			if lastProgressLine {
				_, _ = fmt.Fprintln(os.Stderr)
				lastProgressLine = false
			}
			_, _ = fmt.Fprintln(os.Stderr, "[3/6] Нарезка на чанки")
		case "trim":
			if lastProgressLine {
				_, _ = fmt.Fprintln(os.Stderr)
				lastProgressLine = false
			}
			_, _ = fmt.Fprintln(os.Stderr, "[4/6] Вырезание тишины")
		case "denoise":
			if p.Total > 0 && p.Current > 0 {
				bar := renderBar(p.Current, p.Total, 20)
				percent := p.Current * 100 / p.Total
				_, _ = fmt.Fprintf(
					os.Stderr,
					"\r[5/6] Шумоподавление %s %3d%% (%d/%d) %-24s\033[K",
					bar,
					percent,
					p.Current,
					p.Total,
					shortName(p.Message, 24),
				)
				lastProgressLine = true
				if p.Current >= p.Total {
					_, _ = fmt.Fprintln(os.Stderr)
					lastProgressLine = false
				}
			}
		case "concat":
			if lastProgressLine {
				_, _ = fmt.Fprintln(os.Stderr)
				lastProgressLine = false
			}
			_, _ = fmt.Fprintln(os.Stderr, "[6/6] Склейка чанков")
		case "done":
			if lastProgressLine {
				_, _ = fmt.Fprintln(os.Stderr)
				lastProgressLine = false
			}
			_, _ = fmt.Fprintln(os.Stderr, "[done] Обработка завершена")
		}
	}

	if err := audiopreproc.ProcessWithProgress(context.Background(), input, output, onProgress); err != nil {
		if lastProgressLine {
			_, _ = fmt.Fprintln(os.Stderr)
		}
		_, _ = fmt.Fprintf(os.Stderr, "Ошибка: %v\n", err)
		os.Exit(1)
	}

	_, _ = fmt.Fprintf(os.Stdout, "Готово: %s\n", output)
}

func renderBar(current, total, width int) string {
	if total <= 0 {
		return "[--------------------]"
	}
	if current < 0 {
		current = 0
	}
	if current > total {
		current = total
	}
	filled := current * width / total
	return "[" + strings.Repeat("=", filled) + strings.Repeat("-", width-filled) + "]"
}

func shortName(s string, maxLen int) string {
	if maxLen <= 0 {
		return ""
	}
	runes := []rune(s)
	if len(runes) <= maxLen {
		return s
	}
	if maxLen <= 1 {
		return "…"
	}
	return string(runes[:maxLen-1]) + "…"
}
