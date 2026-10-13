"""Agent configuration, loaded from a TOML file (default /etc/dooh-edge/edge.toml).

Analytics thresholds can additionally be overridden per screen from the central
server (``agent_settings`` on the screen); see :meth:`AnalyticsConfig.apply_remote`.
"""

from __future__ import annotations

import dataclasses
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/etc/dooh-edge/edge.toml")


@dataclass
class ServerConfig:
    url: str = ""
    device_key: str = ""
    upload_interval: float = 15.0
    heartbeat_interval: float = 30.0
    config_interval: float = 300.0
    batch_size: int = 500
    timeout: float = 20.0
    # Path to a CA bundle, or false to disable verification (not recommended).
    verify_tls: bool | str = True


@dataclass
class CameraConfig:
    # A /dev/videoN path, a V4L2 index, or (for testing) a video file path.
    device: str | int = "/dev/video0"
    width: int = 1280
    height: int = 720
    fps: int = 15
    fourcc: str = "MJPG"
    rotate: int = 0  # 0, 90, 180, 270
    flip: bool = False  # mirror horizontally


@dataclass
class ModelsConfig:
    dir: str = "/var/lib/dooh-edge/models"
    face: str = "face_detection_yunet_2023mar.onnx"
    # Age / gender classifiers (ONNX). Leave empty to disable demographics.
    age: str = "age_googlenet.onnx"
    gender: str = "gender_googlenet.onnx"
    # Preprocessing for the age/gender models (defaults match the ONNX model-zoo
    # GoogLeNet age/gender models, which expect BGR input with mean subtraction).
    demographics_input_size: int = 224
    demographics_mean: list[float] = field(
        default_factory=lambda: [78.4263377603, 87.7689143744, 114.895847746]
    )
    demographics_scale: float = 1.0
    demographics_swap_rb: bool = False
    # Midpoint age of every class the age model outputs (Levi-Hassner buckets).
    age_class_midpoints: list[float] = field(
        default_factory=lambda: [1, 5, 10, 17.5, 28.5, 40.5, 50.5, 70]
    )
    gender_labels: list[str] = field(default_factory=lambda: ["male", "female"])
    # Person & bag detector (COCO classes). Leave empty to track faces only.
    objects: str = "object_detection_yolox_2022nov.onnx"
    objects_format: str = "yolox"  # "yolox" or "yolov8"
    objects_input_size: int = 640
    # Optional custom brand classifier for bag crops + a text file of labels.
    brand_classifier: str = ""
    brand_labels: str = ""
    brand_input_size: int = 224
    threads: int = 2

    def path(self, name: str) -> Path | None:
        if not name:
            return None
        p = Path(name)
        return p if p.is_absolute() else Path(self.dir) / p


@dataclass
class AnalyticsConfig:
    process_fps: float = 10.0
    face_score: float = 0.7
    min_face_px: int = 36
    # Faces must be at least this big for age/gender to be estimated.
    min_demographics_face_px: int = 60
    person_score: float = 0.45
    bag_score: float = 0.35
    # Head pose window (degrees) that counts as "looking at the screen". The
    # camera is assumed to sit at the screen, so a frontal face is a viewer.
    attention_yaw_deg: float = 30.0
    attention_pitch_deg: float = 25.0
    # Head pitch of someone looking at the screen centre, as seen by the camera:
    # positive when the camera is mounted above the screen (viewers look below
    # it), negative when mounted below.
    pitch_offset_deg: float = 0.0
    # A person counts as a "viewer" after this much cumulative attention.
    min_attention_seconds: float = 1.0
    # Tracks shorter than this are discarded as noise.
    min_track_seconds: float = 0.5
    # A track ends after not being seen for this long.
    track_timeout_seconds: float = 1.5
    iou_match: float = 0.25
    # Run age/gender every N processed frames per person, up to max samples.
    demographics_every: int = 3
    demographics_max_samples: int = 8
    brand_every: int = 3
    brand_min_votes: int = 2
    bag_min_frames: int = 3
    # Logo matching (ORB feature matching against brand templates).
    logo_min_inliers: int = 12
    # Fraction of bag pixels inside a brand's HSV colour ranges to count as a vote.
    color_min_fraction: float = 0.35

    # Keys the central server may override per screen.
    REMOTE_KEYS = (
        "process_fps", "face_score", "min_face_px", "min_demographics_face_px", "person_score",
        "bag_score", "attention_yaw_deg", "attention_pitch_deg", "pitch_offset_deg",
        "min_attention_seconds", "min_track_seconds", "track_timeout_seconds", "brand_min_votes",
        "bag_min_frames", "logo_min_inliers", "color_min_fraction",
    )

    def apply_remote(self, settings: dict) -> list[str]:
        changed = []
        for key, value in (settings or {}).items():
            if key not in self.REMOTE_KEYS:
                log.warning("Ignoring unknown remote setting %r", key)
                continue
            current = getattr(self, key)
            try:
                value = type(current)(value)
            except (TypeError, ValueError):
                log.warning("Ignoring invalid remote setting %s=%r", key, value)
                continue
            if value != current:
                setattr(self, key, value)
                changed.append(key)
        return changed


@dataclass
class StorageConfig:
    path: str = "/var/lib/dooh-edge/outbox.db"
    retention_days: int = 30
    # Upper bound on unsent events kept while the server is unreachable.
    max_unsent: int = 1_000_000


@dataclass
class LocalUiConfig:
    enabled: bool = True
    bind: str = "127.0.0.1"
    port: int = 8080
    # Annotated camera preview for installation/aiming. Off by default: it is
    # the only place a camera image is ever exposed, and only on this device.
    debug_preview: bool = False


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    local_ui: LocalUiConfig = field(default_factory=LocalUiConfig)

    @classmethod
    def load(cls, path: str | Path | None) -> Config:
        cfg = cls()
        if path is None:
            return cfg
        path = Path(path)
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        for section, values in data.items():
            target = getattr(cfg, section, None)
            if target is None or not dataclasses.is_dataclass(target):
                raise ValueError(f"{path}: unknown section [{section}]")
            known = {f.name for f in dataclasses.fields(target)}
            for key, value in values.items():
                if key not in known:
                    raise ValueError(f"{path}: unknown key {section}.{key}")
                setattr(target, key, value)
        return cfg
