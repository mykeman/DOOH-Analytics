"""Run the ONNX Runtime wrappers end-to-end against tiny generated models.

The real models are large downloads, so these stand-ins have the same input
and output signatures and return fixed predictions. This exercises session
loading, preprocessing shapes and output decoding.
"""

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
from onnx import TensorProto, helper, numpy_helper  # noqa: E402

from dooh_edge.config import ModelsConfig  # noqa: E402
from dooh_edge.detectors.demographics import DemographicsEstimator  # noqa: E402
from dooh_edge.detectors.objects import ObjectDetector  # noqa: E402
from dooh_edge.types import Box  # noqa: E402


def constant_model(path, input_shape, output: np.ndarray):
    """Model whose output is ``output`` (plus 0 x sum(input), so the input is used)."""
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, input_shape)
    y = helper.make_tensor_value_info("output", TensorProto.FLOAT, list(output.shape))
    zero = numpy_helper.from_array(np.zeros((), np.float32), "zero")
    const = numpy_helper.from_array(output.astype(np.float32), "const")
    nodes = [
        helper.make_node("Mul", ["input", "zero"], ["zeroed"]),
        helper.make_node("ReduceSum", ["zeroed"], ["s"], keepdims=0),
        helper.make_node("Add", ["const", "s"], ["output"]),
    ]
    graph = helper.make_graph(nodes, "g", [x], [y], initializer=[zero, const])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.save(model, str(path))


def test_demographics_estimator(tmp_path):
    age_probs = np.zeros((1, 8))
    age_probs[0, 5] = 1.0  # (38-43) -> midpoint 40.5
    constant_model(tmp_path / "age.onnx", [1, 3, 224, 224], age_probs)
    constant_model(tmp_path / "gender.onnx", [1, 3, 224, 224], np.array([[0.1, 0.9]]))
    est = DemographicsEstimator(ModelsConfig(dir=str(tmp_path), age="age.onnx", gender="gender.onnx"))
    image = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    age, gender, conf = est.estimate(image, Box(200, 100, 300, 230))
    assert age == pytest.approx(40.5)
    assert gender == "female" and conf == pytest.approx(0.9)


def test_object_detector_yolox(tmp_path):
    size = 320
    n = (size // 8) ** 2 + (size // 16) ** 2 + (size // 32) ** 2
    out = np.zeros((1, n, 85), np.float32)
    # Raw YOLOX head output: one person in the stride-8 grid cell at
    # (col 10, row 20) -> centre (80, 160), size 60x200 in model pixels.
    cell = (160 // 8) * (size // 8) + (80 // 8)
    out[0, cell, :4] = [0, 0, np.log(60 / 8), np.log(200 / 8)]
    out[0, cell, 4] = 0.95
    out[0, cell, 5] = 1.0
    constant_model(tmp_path / "yolox.onnx", [1, 3, size, size], out)
    det = ObjectDetector(tmp_path / "yolox.onnx", "yolox", size)
    image = np.zeros((480, 640, 3), np.uint8)  # letterbox ratio 0.5
    persons, bags = det.detect(image, person_score=0.5, bag_score=0.3)
    assert len(persons) == 1 and not bags
    box, score = persons[0]
    assert score == pytest.approx(0.95)
    # Centre (80, 160) in model space -> (160, 320) in the image.
    assert box.cx == pytest.approx(160) and box.cy == pytest.approx(320)
    assert box.w == pytest.approx(120) and box.h == pytest.approx(400)
