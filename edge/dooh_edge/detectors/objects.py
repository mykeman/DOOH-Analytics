"""Person and bag detection with a COCO-trained YOLO-family ONNX model.

Two output layouts are supported:

* ``yolox``  - OpenCV-zoo YOLOX (``1 x N x 85``, raw grid offsets, objectness)
* ``yolov8`` - Ultralytics YOLOv8/v11 export (``1 x 84 x N``, decoded boxes, no objectness)
"""

from __future__ import annotations

import cv2
import numpy as np

from ..types import BagObs, Box
from .onnx_util import load_session, nms

# COCO class ids of interest.
PERSON = 0
BAG_CLASSES = {24: "backpack", 26: "handbag", 28: "suitcase"}


def letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float]:
    """Resize keeping aspect ratio into the top-left of a ``size`` square."""
    h, w = image.shape[:2]
    ratio = min(size / h, size / w)
    resized = cv2.resize(image, (int(round(w * ratio)), int(round(h * ratio))), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[: resized.shape[0], : resized.shape[1]] = resized
    return canvas, ratio


def _yolox_grids(size: int, strides=(8, 16, 32)) -> tuple[np.ndarray, np.ndarray]:
    grids, expanded = [], []
    for s in strides:
        n = size // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        expanded.append(np.full((n * n, 1), s))
    return np.concatenate(grids).astype(np.float32), np.concatenate(expanded).astype(np.float32)


def decode_yolox(raw: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(boxes_xyxy, class_scores)`` in letterboxed input pixels."""
    pred = np.asarray(raw, dtype=np.float32).reshape(-1, raw.shape[-1]).copy()
    grids, strides = _yolox_grids(size)
    if pred.shape[0] == grids.shape[0]:  # raw head output: decode grid offsets
        pred[:, :2] = (pred[:, :2] + grids) * strides
        pred[:, 2:4] = np.exp(pred[:, 2:4]) * strides
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    scores = pred[:, 4:5] * pred[:, 5:]
    return boxes, scores


def decode_yolov8(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pred = np.asarray(raw, dtype=np.float32)
    pred = pred.reshape(pred.shape[-2], pred.shape[-1])
    if pred.shape[0] < pred.shape[1]:  # 84 x N -> N x 84
        pred = pred.T
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    return boxes, pred[:, 4:]


def select(
    boxes: np.ndarray,
    scores: np.ndarray,
    ratio: float,
    person_score: float,
    bag_score: float,
    iou: float = 0.45,
) -> tuple[list[tuple[Box, float]], list[BagObs]]:
    persons: list[tuple[Box, float]] = []
    bags: list[BagObs] = []
    wanted = {PERSON: person_score, **{c: bag_score for c in BAG_CLASSES}}
    for cls, threshold in wanted.items():
        if cls >= scores.shape[1]:
            continue
        s = scores[:, cls]
        idx = np.nonzero(s >= threshold)[0]
        if idx.size == 0:
            continue
        b = boxes[idx] / ratio
        for k in nms(b, s[idx], iou):
            box = Box(*map(float, b[k]))
            if cls == PERSON:
                persons.append((box, float(s[idx][k])))
            else:
                bags.append(BagObs(box=box, score=float(s[idx][k]), label=BAG_CLASSES[cls]))
    # One physical bag is often detected as both "handbag" and "backpack".
    bags.sort(key=lambda b: b.score, reverse=True)
    unique: list[BagObs] = []
    for bag in bags:
        if all(bag.box.iou(u.box) < 0.5 for u in unique):
            unique.append(bag)
    return persons, unique


class ObjectDetector:
    def __init__(self, model_path, fmt: str = "yolox", input_size: int = 640, threads: int = 2):
        if fmt not in ("yolox", "yolov8"):
            raise ValueError(f"Unsupported objects_format {fmt!r}")
        self.fmt = fmt
        self.size = input_size
        self._sess = load_session(model_path, threads)
        self._input = self._sess.get_inputs()[0].name

    def detect(self, image: np.ndarray, person_score: float, bag_score: float):
        canvas, ratio = letterbox(image, self.size)
        if self.fmt == "yolox":
            # YOLOX expects BGR 0-255 without normalisation.
            blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)
            raw = self._sess.run(None, {self._input: blob})[0]
            boxes, scores = decode_yolox(raw, self.size)
        else:
            blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
            raw = self._sess.run(None, {self._input: blob})[0]
            boxes, scores = decode_yolov8(raw)
        return select(boxes, scores, ratio, person_score, bag_score)
