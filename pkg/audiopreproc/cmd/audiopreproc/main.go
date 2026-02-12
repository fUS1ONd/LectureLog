package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/LectureLog/LectureLog/pkg/audiopreproc"
)

func main() {
	// Флаги пресета и отключения этапов
	preset := flag.String("preset", "lecture_hall", "пресет: quiet_room, lecture_hall, noisy, custom")
	noNormalize := flag.Bool("no-normalize", false, "отключить нормализацию громкости")
	noDenoise := flag.Bool("no-denoise", false, "отключить шумоподавление")
	noSilence := flag.Bool("no-silence", false, "отключить вырезание тишины")

	// Кастомные параметры для пресета custom
	silenceThresh := flag.Float64("silence-thresh", 0, "порог тишины в dB (для --preset custom)")
	silenceDur := flag.Float64("silence-dur", 0, "минимальная длительность тишины в секундах (для --preset custom)")

	// Вывод TimeMap в JSON-файл
	timeMapPath := flag.String("timemap", "", "путь для сохранения TimeMap в JSON")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: audiopreproc [опции] <input> <output>\n\n")
		fmt.Fprintf(os.Stderr, "Препроцессинг аудио: нормализация, шумоподавление, вырезание тишины.\n\n")
		fmt.Fprintf(os.Stderr, "Опции:\n")
		flag.PrintDefaults()
		fmt.Fprintf(os.Stderr, "\nПримеры:\n")
		fmt.Fprintf(os.Stderr, "  audiopreproc --preset lecture_hall input.mp3 output.mp3\n")
		fmt.Fprintf(os.Stderr, "  audiopreproc --preset noisy --no-silence input.mp3 output.mp3\n")
		fmt.Fprintf(os.Stderr, "  audiopreproc --preset custom --silence-thresh -35 --silence-dur 3 input.mp3 output.mp3\n")
	}

	flag.Parse()

	// Проверяем что переданы ровно два позиционных аргумента: input и output
	if flag.NArg() != 2 {
		flag.Usage()
		os.Exit(1)
	}

	input := flag.Arg(0)
	output := flag.Arg(1)

	// Получаем пресет: встроенный или кастомный
	var p audiopreproc.Preset
	var err error

	if *preset == "custom" {
		p = audiopreproc.CustomPreset(
			audiopreproc.NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 7, TruePeak: -2},
			audiopreproc.SilenceParams{Threshold: *silenceThresh, MinDuration: *silenceDur},
		)
	} else {
		p, err = audiopreproc.GetPreset(*preset)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Ошибка: %v\n", err)
			os.Exit(1)
		}
	}

	// Формируем настройки пайплайна
	opts := audiopreproc.PipelineOptions{
		Preset:    p,
		Normalize: !*noNormalize,
		Denoise:   !*noDenoise,
		Silence:   !*noSilence,
	}

	// Контекст с обработкой сигналов завершения (Ctrl+C, kill)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	fmt.Fprintf(os.Stderr, "Обработка: %s → %s (пресет: %s)\n", input, output, p.Name)

	// Запуск пайплайна обработки
	result, err := audiopreproc.Process(ctx, input, output, opts)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Ошибка: %v\n", err)
		os.Exit(1)
	}

	fmt.Fprintf(os.Stderr, "Готово: %s\n", result.OutputPath)

	// Сохранение TimeMap в JSON если запрошено
	if *timeMapPath != "" && result.TimeMap != nil {
		data, err := json.MarshalIndent(result.TimeMap, "", "  ")
		if err != nil {
			fmt.Fprintf(os.Stderr, "Ошибка сериализации TimeMap: %v\n", err)
			os.Exit(1)
		}
		if err := os.WriteFile(*timeMapPath, data, 0644); err != nil {
			fmt.Fprintf(os.Stderr, "Ошибка записи TimeMap: %v\n", err)
			os.Exit(1)
		}
		fmt.Fprintf(os.Stderr, "TimeMap сохранён: %s\n", *timeMapPath)
	}
}
