"""UVC camera capture via OpenCV's V4L2 backend.

A background thread keeps reading so the analytics loop always gets the newest
frame (stale buffered frames would skew dwell timing), and reopens the device
when it is unplugged or stops delivering frames.
"""

from __future__ import annotations

import glob
import logging
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .config import CameraConfig

log = logging.getLogger(__name__)

_ROTATIONS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


@dataclass
class Frame:
    image: np.ndarray
    timestamp: float  # time.time() when captured
    seq: int


def _device_arg(device: str | int) -> str | int:
    if isinstance(device, int):
        return device
    if device.isdigit():
        return int(device)
    return device


def open_capture(cfg: CameraConfig) -> cv2.VideoCapture:
    device = _device_arg(cfg.device)
    is_v4l2 = isinstance(device, int) or str(device).startswith("/dev/video")
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2) if is_v4l2 else cv2.VideoCapture(device)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {cfg.device!r}")
    if is_v4l2:
        if cfg.fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cfg.fourcc[:4]))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        cap.set(cv2.CAP_PROP_FPS, cfg.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    log.info(
        "Camera %s opened at %dx%d @ %.1f fps",
        cfg.device,
        int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        cap.get(cv2.CAP_PROP_FPS),
    )
    return cap


class Camera:
    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._frame: Frame | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ok = False
        self.error: str | None = None
        dev = _device_arg(cfg.device)
        self._is_file = not (isinstance(dev, int) or str(dev).startswith("/dev/"))

    def start(self) -> Camera:
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=5)

    def _transform(self, image: np.ndarray) -> np.ndarray:
        if self.cfg.rotate in _ROTATIONS:
            image = cv2.rotate(image, _ROTATIONS[self.cfg.rotate])
        if self.cfg.flip:
            image = cv2.flip(image, 1)
        return image

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                cap = open_capture(self.cfg)
            except RuntimeError as exc:
                self.ok, self.error = False, str(exc)
                log.error("%s; retrying in %.0fs", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            backoff = 1.0
            failures = 0
            frame_interval = 1.0 / max(self.cfg.fps, 1)
            try:
                while not self._stop.is_set():
                    ok, image = cap.read()
                    if not ok or image is None:
                        failures += 1
                        if self._is_file:
                            log.info("End of video file; restarting from the beginning")
                            break
                        if failures >= 30:
                            raise RuntimeError("Camera stopped delivering frames")
                        time.sleep(0.05)
                        continue
                    failures = 0
                    image = self._transform(image)
                    with self._cond:
                        self._seq += 1
                        self._frame = Frame(image=image, timestamp=time.time(), seq=self._seq)
                        self.ok, self.error = True, None
                        self._cond.notify_all()
                    if self._is_file:
                        time.sleep(frame_interval)  # play files back in real time
            except RuntimeError as exc:
                self.ok, self.error = False, str(exc)
                log.error("%s; reopening", exc)
            finally:
                cap.release()

    def read(self, after_seq: int = 0, timeout: float = 2.0) -> Frame | None:
        """Return the newest frame with ``seq > after_seq``, waiting up to ``timeout``."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._frame is None or self._frame.seq <= after_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._stop.is_set():
                    return None
                self._cond.wait(remaining)
            return self._frame


def list_cameras() -> list[dict]:
    """Probe /dev/video* nodes and report which ones deliver frames."""
    out = []
    for dev in sorted(glob.glob("/dev/video*"), key=lambda d: int(d[10:]) if d[10:].isdigit() else 0):
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        info = {"device": dev, "opened": cap.isOpened(), "frame": False}
        if cap.isOpened():
            ok, img = cap.read()
            info["frame"] = bool(ok and img is not None)
            info["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            info["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            info["fps"] = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        out.append(info)
    return out
