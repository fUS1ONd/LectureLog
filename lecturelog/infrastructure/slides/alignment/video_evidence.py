from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class VisualMatch:
    slide_num: int
    timestamp_s: float
    score: float
    inliers: int


def match_slide_to_frame(
    slide_num: int,
    slide_path: Path,
    frame: np.ndarray,
    timestamp_s: float,
    *,
    min_inliers: int = 12,
) -> VisualMatch | None:
    slide = cv2.imread(str(slide_path), cv2.IMREAD_GRAYSCALE)
    if slide is None or frame is None:
        return None
    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    orb = cv2.ORB_create(nfeatures=1800)
    key_slide, desc_slide = orb.detectAndCompute(slide, None)
    key_frame, desc_frame = orb.detectAndCompute(frame_gray, None)
    if desc_slide is None or desc_frame is None or len(key_slide) < 8 or len(key_frame) < 8:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_slide, desc_frame, k=2)
    good = [first for first, second in pairs if first.distance < 0.72 * second.distance]
    if len(good) < min_inliers:
        return None
    src = np.float32([key_slide[item.queryIdx].pt for item in good]).reshape(-1, 1, 2)
    dst = np.float32([key_frame[item.trainIdx].pt for item in good]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if mask is None:
        return None
    inliers = int(mask.sum())
    if inliers < min_inliers:
        return None
    inlier_ratio = inliers / max(len(good), 1)
    if inlier_ratio < 0.45:
        return None
    return VisualMatch(slide_num, timestamp_s, min(1.0, inlier_ratio), inliers)


def aggregate_temporal_runs(
    matches: list[VisualMatch],
    *,
    max_gap_s: float = 8.0,
    min_run: int = 2,
) -> tuple[VisualMatch, ...]:
    result: list[VisualMatch] = []
    for slide_num in sorted({match.slide_num for match in matches}):
        ordered = sorted(
            (match for match in matches if match.slide_num == slide_num),
            key=lambda match: match.timestamp_s,
        )
        runs: list[list[VisualMatch]] = []
        for match in ordered:
            if not runs or match.timestamp_s - runs[-1][-1].timestamp_s > max_gap_s:
                runs.append([match])
            else:
                runs[-1].append(match)
        for run in runs:
            if len(run) >= min_run:
                best = max(run, key=lambda match: (match.score, match.inliers))
                result.append(
                    VisualMatch(
                        slide_num,
                        sum(item.timestamp_s for item in run) / len(run),
                        sum(item.score for item in run) / len(run),
                        best.inliers,
                    )
                )
    return tuple(sorted(result, key=lambda match: (match.timestamp_s, match.slide_num)))

