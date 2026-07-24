from pathlib import Path

import cv2
import numpy as np

from lecturelog.infrastructure.slides.alignment.video_evidence import (
    VisualMatch,
    aggregate_temporal_runs,
    match_slide_to_frame,
)


def test_orb_homography_matches_synthetic_perspective_slide(tmp_path: Path) -> None:
    slide = np.full((480, 640), 255, dtype=np.uint8)
    for index in range(12):
        cv2.putText(
            slide,
            f"Graph {index} X{index * 17}",
            (30, 35 + index * 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            0,
            2,
        )
    slide_path = tmp_path / "slide.png"
    cv2.imwrite(str(slide_path), slide)
    source = np.float32([[0, 0], [639, 0], [639, 479], [0, 479]])
    target = np.float32([[80, 60], [700, 20], [740, 530], [30, 560]])
    transform = cv2.getPerspectiveTransform(source, target)
    frame = cv2.warpPerspective(slide, transform, (800, 600))
    match = match_slide_to_frame(1, slide_path, frame, 12.0, min_inliers=8)
    assert match is not None
    assert match.inliers >= 8


def test_temporal_runs_reject_single_false_positive() -> None:
    matches = [
        VisualMatch(1, 1, 0.9, 20),
        VisualMatch(1, 4, 0.8, 18),
        VisualMatch(2, 20, 0.95, 30),
    ]
    runs = aggregate_temporal_runs(matches)
    assert [match.slide_num for match in runs] == [1]

