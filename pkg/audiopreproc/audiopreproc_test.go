package audiopreproc

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
)

func TestSegmentCreation(t *testing.T) {
	seg := Segment{
		OriginalStart:  0,
		OriginalEnd:    10 * time.Second,
		ProcessedStart: 0,
		ProcessedEnd:   10 * time.Second,
	}
	assert.Equal(t, 10*time.Second, seg.OriginalEnd)
}
