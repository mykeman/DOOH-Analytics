from __future__ import annotations

from pathlib import Path

import numpy as np


def load_session(path: Path, threads: int = 2):
    import onnxruntime as ort

    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path} (run scripts/fetch_models.sh)")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def softmax(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64).ravel()
    e = np.exp(x - x.max())
    return e / e.sum()


def as_probabilities(x: np.ndarray) -> np.ndarray:
    """Models differ in whether they end with a softmax; normalise either way."""
    x = np.asarray(x, dtype=np.float64).ravel()
    if np.all(x >= 0) and abs(x.sum() - 1.0) < 1e-3:
        return x
    return softmax(x)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression. ``boxes`` is ``N x 4`` (x1, y1, x2, y2)."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_threshold]
    return keep
