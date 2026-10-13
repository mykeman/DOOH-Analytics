import cv2
import numpy as np

from dooh_edge.config import Config, ModelsConfig
from dooh_edge.detectors.brands import BrandRecognizer, BrandSpec
from dooh_edge.detectors.demographics import DemographicsEstimator
from dooh_edge.detectors.objects import decode_yolov8, decode_yolox, letterbox, select
from dooh_edge.detectors.onnx_util import nms


def test_letterbox_keeps_aspect():
    img = np.zeros((360, 640, 3), np.uint8)
    canvas, ratio = letterbox(img, 640)
    assert canvas.shape == (640, 640, 3)
    assert ratio == 1.0
    assert canvas[400, 10, 0] == 114  # padding below the image


def test_nms_suppresses_overlaps():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7])
    assert nms(boxes, scores, 0.5) == [0, 2]


def test_decode_yolov8_and_select():
    n = 8400
    raw = np.zeros((1, 84, n), np.float32)
    # A person at centre (320, 320) size 100x300, and a handbag next to them.
    raw[0, :4, 0] = [320, 320, 100, 300]
    raw[0, 4 + 0, 0] = 0.9
    raw[0, :4, 1] = [322, 318, 98, 302]  # duplicate person -> suppressed
    raw[0, 4 + 0, 1] = 0.8
    raw[0, :4, 2] = [380, 400, 40, 50]
    raw[0, 4 + 26, 2] = 0.6
    boxes, scores = decode_yolov8(raw)
    persons, bags = select(boxes, scores, ratio=0.5, person_score=0.5, bag_score=0.3)
    assert len(persons) == 1
    box, score = persons[0]
    # Coordinates are mapped back to the original image (ratio 0.5 -> x2).
    assert (box.x1, box.y1, box.x2, box.y2) == (540, 340, 740, 940)
    assert len(bags) == 1 and bags[0].label == "handbag"


def test_decode_yolox_grid():
    size = 640
    n = (size // 8) ** 2 + (size // 16) ** 2 + (size // 32) ** 2
    raw = np.zeros((1, n, 85), np.float32)
    # Grid cell (10, 5) at stride 8 -> centre (80, 40) when offsets are 0.
    idx = 5 * (size // 8) + 10
    raw[0, idx, :4] = [0, 0, np.log(4), np.log(8)]  # w = 32, h = 64
    raw[0, idx, 4] = 0.9
    raw[0, idx, 5] = 1.0
    boxes, scores = decode_yolox(raw, size)
    assert np.allclose(boxes[idx], [64, 8, 96, 72])
    assert scores[idx, 0] == np.float32(0.9)


def _logo(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((200, 300, 3), 255, np.uint8)
    for _ in range(40):
        p1 = tuple(int(v) for v in rng.integers(0, [300, 200]))
        p2 = tuple(int(v) for v in rng.integers(0, [300, 200]))
        color = tuple(int(v) for v in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            cv2.rectangle(img, p1, p2, color, -1)
        else:
            cv2.line(img, p1, p2, color, 4)
    cv2.putText(img, "ACME", (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 0, 0), 6)
    return img


def test_brand_logo_matching(tmp_path):
    acme, other = _logo(1), _logo(2)
    cv2.imwrite(str(tmp_path / "acme.png"), acme)
    cv2.imwrite(str(tmp_path / "other.png"), other)
    rec = BrandRecognizer(
        [BrandSpec("Acme", template_paths=[tmp_path / "acme.png"]),
         BrandSpec("Other", template_paths=[tmp_path / "other.png"])],
        min_inliers=12,
    )
    assert rec.enabled
    # The logo printed on a bag: scaled, rotated slightly, on a coloured background.
    bag = np.full((400, 320, 3), (40, 90, 160), np.uint8)
    m = cv2.getRotationMatrix2D((150, 100), 8, 0.8)
    warped = cv2.warpAffine(acme, m, (300, 200), borderValue=(40, 90, 160))
    bag[100:300, 10:310] = warped
    votes = rec.recognize(bag)
    assert max(votes, key=votes.get) == "Acme"
    assert rec.recognize(np.full((300, 300, 3), 127, np.uint8)) == {}


def test_brand_colour_signature():
    # Orange bag: OpenCV hue ~10-20.
    rec = BrandRecognizer([BrandSpec("Orange Co", colors=[{"lo": [5, 150, 120], "hi": [22, 255, 255]}])])
    orange = np.zeros((200, 150, 3), np.uint8)
    orange[:] = (0, 120, 255)
    assert rec.recognize(orange) == {"Orange Co": 1}
    blue = np.zeros((200, 150, 3), np.uint8)
    blue[:] = (255, 80, 0)
    assert rec.recognize(blue) == {}


def test_demographics_decoding_without_models():
    cfg = ModelsConfig(age="", gender="")
    est = DemographicsEstimator(cfg)
    probs = np.zeros(8)
    probs[4] = 1.0  # (25-32) bucket
    assert est.decode_age(probs) == 28.5
    assert est.decode_age(np.array([[41.0]])) == 41.0  # regression output
    gender, conf = est.decode_gender(np.array([[2.0, 0.0]]))  # logits
    assert gender == "male" and 0.85 < conf < 0.9


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "edge.toml"
    p.write_text('[camera]\ndevice = "/dev/video2"\nwidth = 1920\n[analytics]\nprocess_fps = 6.0\n')
    cfg = Config.load(p)
    assert cfg.camera.device == "/dev/video2" and cfg.camera.width == 1920
    assert cfg.analytics.process_fps == 6.0
    p.write_text("[camera]\nresolution = 1\n")
    try:
        Config.load(p)
    except ValueError as exc:
        assert "camera.resolution" in str(exc)
    else:
        raise AssertionError("expected ValueError")
