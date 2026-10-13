"""Plain data types shared by perception, tracking and session aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def w(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def h(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def area(self) -> float:
        return self.w * self.h

    def iou(self, other: Box) -> float:
        ix = max(0.0, min(self.x2, other.x2) - max(self.x1, other.x1))
        iy = max(0.0, min(self.y2, other.y2) - max(self.y1, other.y1))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def expand(self, fx: float, fy: float) -> Box:
        dx, dy = self.w * fx, self.h * fy
        return Box(self.x1 - dx, self.y1 - dy, self.x2 + dx, self.y2 + dy)

    def clip(self, width: int, height: int) -> Box:
        return Box(
            min(max(self.x1, 0), width), min(max(self.y1, 0), height),
            min(max(self.x2, 0), width), min(max(self.y2, 0), height),
        )

    def crop(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        b = self.clip(w, h)
        return image[int(b.y1) : int(b.y2), int(b.x1) : int(b.x2)]


@dataclass
class FaceObs:
    box: Box
    score: float
    # YuNet order: right eye, left eye, nose tip, right mouth corner, left mouth corner.
    landmarks: np.ndarray | None = None
    yaw: float | None = None
    pitch: float | None = None
    attentive: bool = False
    # Filled in by demographic estimation (or directly by the simulator).
    age: float | None = None
    gender: str | None = None
    gender_conf: float = 0.0


@dataclass
class BagObs:
    box: Box
    score: float
    label: str = "bag"
    brand: str | None = None


@dataclass
class PersonObs:
    """One person in one frame. ``box`` is the body box, or the face box when
    no person detector is configured."""

    box: Box
    score: float
    face: FaceObs | None = None
    bags: list[BagObs] = field(default_factory=list)


@dataclass
class FrameObs:
    timestamp: float
    width: int
    height: int
    people: list[PersonObs] = field(default_factory=list)
