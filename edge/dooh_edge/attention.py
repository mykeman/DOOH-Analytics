"""Head-pose estimation from the five YuNet facial landmarks.

Because the camera is mounted at the screen, a person whose head points at the
camera (small yaw and pitch) is looking at the screen. We fit a generic 3D face
model to the landmarks with ``solvePnP`` and read yaw/pitch off the rotation.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

# Generic face model in millimetres, in a camera-like frame: x to the image
# right, y down, z away from the camera. Order matches YuNet landmarks:
# right eye, left eye, nose tip, right mouth corner, left mouth corner
# (the subject's right eye appears on the image left).
FACE_MODEL_3D = np.array(
    [
        [-32.0, -35.0, 30.0],
        [32.0, -35.0, 30.0],
        [0.0, 0.0, 0.0],
        [-26.0, 30.0, 25.0],
        [26.0, 30.0, 25.0],
    ],
    dtype=np.float64,
)


def camera_matrix(width: int, height: int) -> np.ndarray:
    # Without calibration, a focal length of ~ the image width is a decent
    # approximation for typical 60-80 degree UVC lenses.
    f = float(width)
    return np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1]], dtype=np.float64)


def head_pose(landmarks: np.ndarray, width: int, height: int) -> tuple[float, float] | None:
    """Return ``(yaw, pitch)`` in degrees, 0/0 meaning facing the camera.

    Positive yaw = head turned towards the image right; positive pitch = looking down.
    """
    pts = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
    ok, rvec, _tvec = cv2.solvePnP(
        FACE_MODEL_3D,
        pts,
        camera_matrix(width, height),
        np.zeros(4),
        flags=cv2.SOLVEPNP_EPNP,
    )
    if not ok:
        return None
    rot, _ = cv2.Rodrigues(rvec)
    # The model's forward axis (-z, pointing out of the face toward the camera)
    # expressed in camera coordinates.
    fwd = rot @ np.array([0.0, 0.0, -1.0])
    if fwd[2] >= 0:  # degenerate fit: face pointing away
        return None
    yaw = math.degrees(math.atan2(fwd[0], -fwd[2]))
    pitch = math.degrees(math.atan2(fwd[1], -fwd[2]))
    return yaw, pitch


def is_attentive(
    yaw: float | None, pitch: float | None, max_yaw: float, max_pitch: float, pitch_offset: float = 0.0
) -> bool:
    if yaw is None or pitch is None:
        return False
    return abs(yaw) <= max_yaw and abs(pitch - pitch_offset) <= max_pitch
