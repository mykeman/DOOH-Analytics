"""Synthetic audience generator.

Produces the same ``FrameObs`` structures perception would, so the tracker,
session logic, outbox, uploader and dashboards can be exercised end-to-end
without a camera or models (demos, CI, load testing the server).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .types import BagObs, Box, FaceObs, FrameObs, PersonObs


@dataclass
class _Walker:
    x: float
    y: float
    vx: float
    h: float
    pause_at: float
    pause_for: float
    look_from: float
    look_to: float
    age: float
    gender: str
    face_visible: bool
    brand: str | None
    has_bag: bool
    born: float
    paused: float = 0.0
    state: str = field(default="walking")


class Simulator:
    def __init__(
        self,
        people_per_minute: float = 20.0,
        brands: list[str] | None = None,
        width: int = 1280,
        height: int = 720,
        seed: int | None = None,
    ):
        self.rate = people_per_minute / 60.0
        self.brands = brands or ["Northwind Apparel", "Bluebird Books", "Summit Sports"]
        self.width, self.height = width, height
        self.rng = random.Random(seed)
        self.walkers: list[_Walker] = []

    def _spawn(self, t: float) -> _Walker:
        r = self.rng
        h = r.uniform(260, 520)  # person height in pixels ~ distance from the camera
        left = r.random() < 0.5
        speed = r.uniform(70, 200) * (h / 400)
        stops = r.random() < 0.35
        looks = r.random() < 0.45
        crossing = self.width / speed
        look_from = r.uniform(0.1, 0.6) * crossing
        has_bag = r.random() < 0.3
        return _Walker(
            x=-80 if left else self.width + 80,
            y=r.uniform(self.height * 0.55, self.height * 0.95),
            vx=speed if left else -speed,
            h=h,
            pause_at=r.uniform(0.3, 0.6) * crossing if stops else 1e9,
            pause_for=r.uniform(2, 12) if stops else 0,
            look_from=look_from,
            look_to=look_from + (r.uniform(0.8, 8) if looks else 0),
            age=max(5, min(85, r.gauss(36, 14))),
            gender=r.choice(["male", "female"]),
            face_visible=r.random() < 0.8,
            brand=r.choice(self.brands) if has_bag and r.random() < 0.6 else None,
            has_bag=has_bag,
            born=t,
        )

    def step(self, t: float, dt: float) -> FrameObs:
        r = self.rng
        # Poisson arrivals.
        n_new = 0
        p = self.rate * dt
        while r.random() < p and n_new < 5:
            n_new += 1
            p *= 0.5
        for _ in range(n_new):
            self.walkers.append(self._spawn(t))

        people = []
        alive = []
        for w in self.walkers:
            age_t = t - w.born
            if w.state == "walking" and age_t >= w.pause_at:
                w.state = "paused"
            if w.state == "paused":
                w.paused += dt
                if w.paused >= w.pause_for:
                    w.state = "leaving"
                    w.look_to = min(w.look_to, age_t)  # attention ends as they leave
            else:
                w.x += w.vx * dt
            if w.x < -150 or w.x > self.width + 150:
                continue
            alive.append(w)
            if w.x < 0 or w.x > self.width:
                continue  # just outside the frame
            bw = w.h * 0.38
            body = Box(w.x - bw / 2, w.y - w.h, w.x + bw / 2, w.y)
            person = PersonObs(box=body, score=0.9)
            looking = w.look_from <= age_t <= w.look_to
            if w.face_visible and (looking or r.random() < 0.5):
                fs = w.h * 0.13
                face_box = Box(w.x - fs / 2, w.y - w.h + fs * 0.2, w.x + fs / 2, w.y - w.h + fs * 1.2)
                yaw = r.gauss(0, 8) if looking else r.choice([-1, 1]) * r.uniform(40, 80)
                person.face = FaceObs(
                    box=face_box,
                    score=0.9,
                    yaw=yaw,
                    pitch=r.gauss(0, 6),
                    attentive=looking,
                    age=w.age + r.gauss(0, 5),
                    gender=w.gender if r.random() < 0.85 else ("female" if w.gender == "male" else "male"),
                    gender_conf=r.uniform(0.6, 0.98),
                )
            if w.has_bag and r.random() < 0.8:
                side = 1 if w.vx > 0 else -1
                bag_box = Box(w.x + side * bw * 0.4 - 30, w.y - w.h * 0.45, w.x + side * bw * 0.4 + 30, w.y - w.h * 0.25)
                person.bags.append(BagObs(box=bag_box, score=0.7, label="handbag", brand=w.brand))
            people.append(person)
        self.walkers = alive
        return FrameObs(timestamp=t, width=self.width, height=self.height, people=people)
