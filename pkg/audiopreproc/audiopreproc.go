package audiopreproc

import "time"

// Segment — фрагмент аудио с таймкодами в оригинале и обработанном файле
type Segment struct {
	OriginalStart  time.Duration
	OriginalEnd    time.Duration
	ProcessedStart time.Duration
	ProcessedEnd   time.Duration
}

// TimeMap — маппинг таймкодов между оригинальным и обработанным аудио
type TimeMap struct {
	Segments []Segment
}

// Result — результат обработки аудио
type Result struct {
	OutputPath string
	TimeMap    *TimeMap // nil если вырезание тишины отключено
}

// ToOriginal — конвертирует таймкод из обработанного аудио в оригинальное
func (tm *TimeMap) ToOriginal(processed time.Duration) time.Duration {
	for _, seg := range tm.Segments {
		if processed >= seg.ProcessedStart && processed <= seg.ProcessedEnd {
			offset := processed - seg.ProcessedStart
			return seg.OriginalStart + offset
		}
	}
	// За пределами всех сегментов — возвращаем конец последнего
	if len(tm.Segments) > 0 {
		last := tm.Segments[len(tm.Segments)-1]
		return last.OriginalEnd
	}
	return 0
}

// ToProcessed — конвертирует таймкод из оригинального аудио в обработанное
func (tm *TimeMap) ToProcessed(original time.Duration) time.Duration {
	for _, seg := range tm.Segments {
		if original >= seg.OriginalStart && original <= seg.OriginalEnd {
			offset := original - seg.OriginalStart
			return seg.ProcessedStart + offset
		}
	}
	// Попали в вырезанный участок — возвращаем конец предыдущего сегмента
	for i := len(tm.Segments) - 1; i >= 0; i-- {
		if original > tm.Segments[i].OriginalEnd {
			return tm.Segments[i].ProcessedEnd
		}
	}
	return 0
}
