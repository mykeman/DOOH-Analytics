"""Age and gender estimation from face crops (ONNX classifiers).

Estimates are probabilistic and inherently imprecise; they are aggregated over
several frames per person and reported only as coarse buckets.
"""

from __future__ import annotations

import cv2
import numpy as np

from ..config import ModelsConfig
from ..types import Box
from .onnx_util import as_probabilities, load_session

AGE_BUCKETS = [(0, 12, "0-12"), (13, 17, "13-17"), (18, 24, "18-24"), (25, 34, "25-34"),
               (35, 44, "35-44"), (45, 54, "45-54"), (55, 64, "55-64"), (65, 200, "65+")]


def age_bucket(age: float) -> str:
    for lo, hi, label in AGE_BUCKETS:
        if age < hi + 1:
            return label
    return AGE_BUCKETS[-1][2]


class DemographicsEstimator:
    def __init__(self, cfg: ModelsConfig):
        self.cfg = cfg
        age_path, gender_path = cfg.path(cfg.age), cfg.path(cfg.gender)
        self._age = load_session(age_path, cfg.threads) if age_path else None
        self._gender = load_session(gender_path, cfg.threads) if gender_path else None
        self._midpoints = np.asarray(cfg.age_class_midpoints, dtype=np.float64)

    def _blob(self, image: np.ndarray, box: Box) -> np.ndarray | None:
        # Faces are cropped with a margin; these models were trained on loose crops.
        crop = box.expand(0.2, 0.2).crop(image)
        if crop.size == 0:
            return None
        s = self.cfg.demographics_input_size
        return cv2.dnn.blobFromImage(
            crop,
            scalefactor=self.cfg.demographics_scale,
            size=(s, s),
            mean=tuple(self.cfg.demographics_mean),
            swapRB=self.cfg.demographics_swap_rb,
        ).astype(np.float32)

    def estimate(self, image: np.ndarray, face_box: Box) -> tuple[float | None, str | None, float]:
        """Return ``(age_years, gender, gender_confidence)``."""
        blob = self._blob(image, face_box)
        if blob is None:
            return None, None, 0.0
        age = gender = None
        conf = 0.0
        if self._age is not None:
            out = self._age.run(None, {self._age.get_inputs()[0].name: blob})[0]
            age = self.decode_age(out)
        if self._gender is not None:
            out = self._gender.run(None, {self._gender.get_inputs()[0].name: blob})[0]
            gender, conf = self.decode_gender(out)
        return age, gender, conf

    def decode_age(self, output: np.ndarray) -> float:
        flat = np.asarray(output).ravel()
        if flat.size == 1:  # regression model: predicts years directly
            return float(flat[0])
        probs = as_probabilities(flat)
        return float(np.dot(probs, self._midpoints[: probs.size]))

    def decode_gender(self, output: np.ndarray) -> tuple[str, float]:
        probs = as_probabilities(output)
        i = int(np.argmax(probs))
        return self.cfg.gender_labels[i], float(probs[i])
