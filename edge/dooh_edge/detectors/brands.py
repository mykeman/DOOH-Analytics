"""Recognise the brand printed on a shopping bag.

Three complementary signals, all optional:

1. **Logo matching** - ORB features of each uploaded logo template are matched
   against the bag crop; a RANSAC homography with enough inliers is a strong vote.
2. **Colour signature** - many retailers use a distinctive bag colour. If enough
   of the crop falls inside a brand's HSV ranges, that is a weak vote.
3. **Custom classifier** - an ONNX image classifier trained on your own bag
   photos (most accurate when available).

Votes are accumulated per tracked person by the session aggregator, so a single
mismatched frame does not label a bag.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .onnx_util import as_probabilities, load_session

log = logging.getLogger(__name__)

LOGO_WEIGHT = 2
COLOR_WEIGHT = 1
CLASSIFIER_WEIGHT = 2


@dataclass
class _Template:
    keypoints: list
    descriptors: np.ndarray
    shape: tuple[int, int]


@dataclass
class BrandSpec:
    name: str
    colors: list[dict] = field(default_factory=list)  # [{"lo": [h,s,v], "hi": [h,s,v]}]
    template_paths: list[Path] = field(default_factory=list)


class BrandRecognizer:
    def __init__(
        self,
        brands: list[BrandSpec],
        min_inliers: int = 12,
        color_min_fraction: float = 0.35,
        classifier_path: Path | None = None,
        classifier_labels: list[str] | None = None,
        classifier_input: int = 224,
        classifier_min_conf: float = 0.6,
    ):
        self.min_inliers = min_inliers
        self.color_min_fraction = color_min_fraction
        self._orb = cv2.ORB_create(nfeatures=1000)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self._templates: dict[str, list[_Template]] = {}
        self._colors: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
        for brand in brands:
            for path in brand.template_paths:
                t = self._load_template(path)
                if t is not None:
                    self._templates.setdefault(brand.name, []).append(t)
            ranges = [
                (np.array(c["lo"], dtype=np.uint8), np.array(c["hi"], dtype=np.uint8))
                for c in brand.colors
            ]
            if ranges:
                self._colors[brand.name] = ranges
        self._clf = None
        if classifier_path:
            self._clf = load_session(classifier_path)
            self._clf_labels = classifier_labels or []
            self._clf_input = classifier_input
            self._clf_min_conf = classifier_min_conf

    @property
    def enabled(self) -> bool:
        return bool(self._templates or self._colors or self._clf)

    def _load_template(self, path: Path) -> _Template | None:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            log.warning("Cannot read brand template %s", path)
            return None
        # Normalise template scale so feature counts are comparable.
        scale = 300.0 / max(img.shape)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        kp, des = self._orb.detectAndCompute(img, None)
        if des is None or len(kp) < 10:
            log.warning("Brand template %s has too little texture for matching", path)
            return None
        return _Template(keypoints=kp, descriptors=des, shape=img.shape[:2])

    def _logo_votes(self, gray: np.ndarray) -> dict[str, int]:
        if not self._templates:
            return {}
        kp, des = self._orb.detectAndCompute(gray, None)
        if des is None or len(kp) < self.min_inliers:
            return {}
        best: dict[str, int] = {}
        for name, templates in self._templates.items():
            for t in templates:
                pairs = self._matcher.knnMatch(t.descriptors, des, k=2)
                good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
                if len(good) < self.min_inliers:
                    continue
                src = np.float32([t.keypoints[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
                inliers = int(mask.sum()) if mask is not None else 0
                if inliers >= self.min_inliers:
                    best[name] = max(best.get(name, 0), inliers)
        return best

    def _color_votes(self, crop: np.ndarray) -> dict[str, float]:
        if not self._colors:
            return {}
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        total = float(hsv.shape[0] * hsv.shape[1])
        out = {}
        for name, ranges in self._colors.items():
            mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
            for lo, hi in ranges:
                mask |= cv2.inRange(hsv, lo, hi)
            frac = float(np.count_nonzero(mask)) / total
            if frac >= self.color_min_fraction:
                out[name] = frac
        return out

    def _classify(self, crop: np.ndarray) -> str | None:
        if self._clf is None:
            return None
        s = self._clf_input
        blob = cv2.dnn.blobFromImage(crop, 1 / 255.0, (s, s), swapRB=True).astype(np.float32)
        probs = as_probabilities(self._clf.run(None, {self._clf.get_inputs()[0].name: blob})[0])
        i = int(np.argmax(probs))
        if probs[i] < self._clf_min_conf or i >= len(self._clf_labels):
            return None
        label = self._clf_labels[i]
        return None if label.lower() in ("none", "unbranded", "background") else label

    def recognize(self, crop: np.ndarray) -> dict[str, int]:
        """Return weighted votes per brand for one bag crop (empty = no brand seen)."""
        if crop.size == 0 or min(crop.shape[:2]) < 24:
            return {}
        votes: dict[str, int] = {}
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        # Upscale small crops so ORB finds enough features on the logo.
        if max(gray.shape) < 300:
            f = 300.0 / max(gray.shape)
            gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        logo = self._logo_votes(gray)
        if logo:
            name = max(logo, key=logo.get)
            votes[name] = votes.get(name, 0) + LOGO_WEIGHT
        color = self._color_votes(crop)
        if color:
            name = max(color, key=color.get)
            votes[name] = votes.get(name, 0) + COLOR_WEIGHT
        label = self._classify(crop)
        if label:
            votes[label] = votes.get(label, 0) + CLASSIFIER_WEIGHT
        return votes
