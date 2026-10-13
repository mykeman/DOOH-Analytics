"""Image -> FrameObs: runs the detectors and groups faces and bags per person."""

from __future__ import annotations

import logging

import numpy as np

from .attention import head_pose, is_attentive
from .config import AnalyticsConfig, ModelsConfig
from .detectors.brands import BrandRecognizer
from .detectors.demographics import DemographicsEstimator
from .detectors.face import FaceDetector
from .detectors.objects import ObjectDetector
from .types import BagObs, Box, FaceObs, FrameObs, PersonObs

log = logging.getLogger(__name__)


def assign_faces(persons: list[tuple[Box, float]], faces: list[FaceObs]) -> list[PersonObs]:
    """Attach each face to the body box whose head region contains it.

    Faces without a matching body (e.g. body occluded or cut off by the frame)
    become people of their own.
    """
    people = [PersonObs(box=b, score=s) for b, s in persons]
    for face in sorted(faces, key=lambda f: f.score, reverse=True):
        best, best_area = None, float("inf")
        for p in people:
            if p.face is not None:
                continue
            head = Box(p.box.x1, p.box.y1, p.box.x2, p.box.y1 + 0.45 * p.box.h)
            if head.contains(face.box.cx, face.box.cy) and p.box.area < best_area:
                best, best_area = p, p.box.area
        if best is not None:
            best.face = face
        else:
            people.append(PersonObs(box=face.box, score=face.score, face=face))
    return people


def assign_bags(people: list[PersonObs], bags: list[BagObs]) -> None:
    """Attach each bag to the nearest person whose (widened) body box holds it."""
    for bag in bags:
        best, best_dist = None, float("inf")
        for p in people:
            if p.box.h <= 0:
                continue
            # Bags are carried beside the body: widen the box sideways.
            reach = p.box.expand(0.35, 0.05)
            if not reach.contains(bag.box.cx, bag.box.cy):
                continue
            dist = abs(bag.box.cx - p.box.cx) / max(p.box.w, 1.0)
            if dist < best_dist:
                best, best_dist = p, dist
        if best is not None:
            best.bags.append(bag)


class Perception:
    def __init__(self, models: ModelsConfig, analytics: AnalyticsConfig):
        self.analytics = analytics
        self.face = FaceDetector(models.path(models.face), analytics.face_score)
        self.demographics: DemographicsEstimator | None = None
        if models.age or models.gender:
            try:
                self.demographics = DemographicsEstimator(models)
            except FileNotFoundError as exc:
                log.warning("Demographics disabled: %s", exc)
        self.objects: ObjectDetector | None = None
        if models.objects:
            try:
                self.objects = ObjectDetector(
                    models.path(models.objects), models.objects_format, models.objects_input_size, models.threads
                )
            except FileNotFoundError as exc:
                log.warning("Person/bag detection disabled (tracking faces only): %s", exc)
        self.brands: BrandRecognizer | None = None

    def detect(self, image: np.ndarray, t: float) -> FrameObs:
        a = self.analytics
        h, w = image.shape[:2]
        self.face.set_threshold(a.face_score)
        faces = self.face.detect(image, a.min_face_px)
        for f in faces:
            pose = head_pose(f.landmarks, w, h) if f.landmarks is not None else None
            if pose:
                f.yaw, f.pitch = pose
            f.attentive = is_attentive(
                f.yaw, f.pitch, a.attention_yaw_deg, a.attention_pitch_deg, a.pitch_offset_deg
            )
        if self.objects is not None:
            persons, bags = self.objects.detect(image, a.person_score, a.bag_score)
            people = assign_faces(persons, faces)
            assign_bags(people, bags)
        else:
            people = [PersonObs(box=f.box, score=f.score, face=f) for f in faces]
        return FrameObs(timestamp=t, width=w, height=h, people=people)

    def estimate_demographics(self, image: np.ndarray, face: FaceObs) -> None:
        if self.demographics is None:
            return
        if min(face.box.w, face.box.h) < self.analytics.min_demographics_face_px:
            return
        # Strongly turned faces give unreliable estimates.
        if face.yaw is not None and abs(face.yaw) > 45:
            return
        face.age, face.gender, face.gender_conf = self.demographics.estimate(image, face.box)

    def recognize_brand(self, image: np.ndarray, bag: BagObs) -> None:
        recognizer = self.brands  # swapped atomically by the sync thread
        if recognizer is None or not recognizer.enabled:
            return
        votes = recognizer.recognize(bag.box.crop(image))
        if votes:
            bag.brand = max(votes, key=votes.get)
