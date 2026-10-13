"""Face detection with OpenCV's YuNet (``cv2.FaceDetectorYN``)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..types import Box, FaceObs


class FaceDetector:
    def __init__(self, model_path: Path, score_threshold: float = 0.7, nms_threshold: float = 0.3):
        if not model_path.exists():
            raise FileNotFoundError(f"Face model not found: {model_path} (run scripts/fetch_models.sh)")
        self._det = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), score_threshold, nms_threshold, 5000
        )
        self._size: tuple[int, int] | None = None

    def set_threshold(self, score: float) -> None:
        self._det.setScoreThreshold(score)

    def detect(self, image: np.ndarray, min_size: int = 0) -> list[FaceObs]:
        h, w = image.shape[:2]
        if self._size != (w, h):
            self._det.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self._det.detect(image)
        return parse_yunet(faces, min_size)


def parse_yunet(faces: np.ndarray | None, min_size: int = 0) -> list[FaceObs]:
    """Convert YuNet's ``N x 15`` output (x, y, w, h, 5 landmarks, score)."""
    out: list[FaceObs] = []
    if faces is None:
        return out
    for row in np.asarray(faces).reshape(-1, 15):
        x, y, fw, fh = (float(v) for v in row[:4])
        if min(fw, fh) < min_size:
            continue
        out.append(
            FaceObs(
                box=Box(x, y, x + fw, y + fh),
                score=float(row[14]),
                landmarks=row[4:14].reshape(5, 2).astype(np.float64),
            )
        )
    return out
