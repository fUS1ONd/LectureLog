package audiopreproc

import "fmt"

// NormalizeParams — параметры нормализации громкости (loudnorm)
type NormalizeParams struct {
	IntegratedLoudness float64 // I, целевая громкость в LUFS (обычно -24)
	LoudnessRange      float64 // LRA, допустимый разброс в LU
	TruePeak           float64 // TP, максимальный пик в dBTP
}

// SilenceParams — параметры детекции тишины
type SilenceParams struct {
	Threshold   float64 // порог в dB (например -35)
	MinDuration float64 // минимальная длительность тишины в секундах
}

// Preset — набор параметров для всех этапов обработки
// Шумоподавление (RNNoise) не имеет настраиваемых параметров — нейросеть адаптируется сама
type Preset struct {
	Name        string
	Description string
	Normalize   NormalizeParams
	Silence     SilenceParams
}

// presets — встроенные пресеты для типичных условий записи
var presets = map[string]Preset{
	"quiet_room": {
		Name:        "quiet_room",
		Description: "Тихое помещение, диктофон рядом",
		Normalize:   NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 7, TruePeak: -2},
		Silence:     SilenceParams{Threshold: -40, MinDuration: 5},
	},
	"lecture_hall": {
		Name:        "lecture_hall",
		Description: "Аудитория среднего размера",
		Normalize:   NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 11, TruePeak: -2},
		Silence:     SilenceParams{Threshold: -35, MinDuration: 5},
	},
	"noisy": {
		Name:        "noisy",
		Description: "Шумное помещение, запись издалека",
		Normalize:   NormalizeParams{IntegratedLoudness: -24, LoudnessRange: 14, TruePeak: -2},
		Silence:     SilenceParams{Threshold: -30, MinDuration: 4},
	},
}

// GetPreset — возвращает пресет по имени
func GetPreset(name string) (Preset, error) {
	p, ok := presets[name]
	if !ok {
		return Preset{}, fmt.Errorf("неизвестный пресет: %q", name)
	}
	return p, nil
}

// CustomPreset — создаёт пресет с пользовательскими параметрами
func CustomPreset(norm NormalizeParams, silence SilenceParams) Preset {
	return Preset{
		Name:        "custom",
		Description: "Ручная настройка",
		Normalize:   norm,
		Silence:     silence,
	}
}
