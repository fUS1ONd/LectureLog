package audiopreproc

import (
	"context"
	"fmt"
	"path/filepath"
	"runtime"
)

// modelPath — возвращает путь к модели RNNoise относительно исходников пакета
func modelPath() string {
	_, file, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(file), "models", "rnnoise-std.rnnn")
}

// denoise — шумоподавление через нейросетевой фильтр arnndn (RNNoise)
func denoise(ctx context.Context, input, output string) error {
	model := modelPath()
	filter := fmt.Sprintf("arnndn=m=%s", model)

	_, err := runFFmpeg(ctx, []string{
		"-i", input,
		"-af", filter,
		"-y", output,
	})
	if err != nil {
		return fmt.Errorf("шумоподавление: %w", err)
	}
	return nil
}
