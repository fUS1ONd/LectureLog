package audiopreproc

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGetPreset_KnownPresets(t *testing.T) {
	presets := []string{"quiet_room", "lecture_hall", "noisy"}
	for _, name := range presets {
		p, err := GetPreset(name)
		require.NoError(t, err, "пресет %s должен существовать", name)
		assert.NotEmpty(t, p.Name)
		assert.Greater(t, p.Normalize.IntegratedLoudness, -30.0)
		assert.Less(t, p.Silence.Threshold, 0.0)
		assert.Greater(t, p.Silence.MinDuration, 0.0)
	}
}

func TestGetPreset_Unknown(t *testing.T) {
	_, err := GetPreset("nonexistent")
	assert.Error(t, err)
}

func TestPresetCustom(t *testing.T) {
	p := CustomPreset(NormalizeParams{
		IntegratedLoudness: -20,
		LoudnessRange:     9,
		TruePeak:          -1,
	}, SilenceParams{
		Threshold:   -35,
		MinDuration: 3,
	})
	assert.Equal(t, "custom", p.Name)
	assert.Equal(t, -20.0, p.Normalize.IntegratedLoudness)
}
