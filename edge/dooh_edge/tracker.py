"""Lightweight multi-person tracker (greedy IoU + centre-distance matching).

Tracks only live for as long as a person stays in view. There is deliberately
no appearance-based re-identification: a person who leaves and comes back is a
new, anonymous track.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Box, PersonObs


@dataclass
class Track:
    id: int
    box: Box
    first_seen: float
    last_seen: float
    hits: int = 1
    # Smoothed velocity (pixels/second) used to predict the next position.
    vx: float = 0.0
    vy: float = 0.0
    obs: PersonObs | None = field(default=None, repr=False)

    def predicted(self, t: float) -> Box:
        dt = min(t - self.last_seen, 1.0)
        dx, dy = self.vx * dt, self.vy * dt
        b = self.box
        return Box(b.x1 + dx, b.y1 + dy, b.x2 + dx, b.y2 + dy)


class Tracker:
    def __init__(self, iou_threshold: float = 0.25, timeout: float = 1.5):
        self.iou_threshold = iou_threshold
        self.timeout = timeout
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    def _affinity(self, track: Track, obs: PersonObs, t: float) -> float:
        pred = track.predicted(t)
        iou = pred.iou(obs.box)
        if iou >= self.iou_threshold:
            return 1.0 + iou
        # Fall back to centre distance for small/fast-moving boxes, relative to
        # size - but only across short gaps, otherwise someone entering where
        # another person just left would inherit their track.
        if t - track.last_seen > 0.35:
            return 0.0
        scale = max(pred.w, pred.h, obs.box.w, obs.box.h, 1.0)
        dist = ((pred.cx - obs.box.cx) ** 2 + (pred.cy - obs.box.cy) ** 2) ** 0.5 / scale
        size_ratio = min(pred.area, obs.box.area) / max(pred.area, obs.box.area, 1e-9)
        if dist < 0.6 and size_ratio > 0.4:
            return 1.0 - dist
        return 0.0

    def update(self, people: list[PersonObs], t: float) -> tuple[list[Track], list[Track]]:
        """Associate this frame's people with tracks.

        Returns ``(active_tracks_seen_this_frame, ended_tracks)``.
        """
        pairs = []
        for tid, track in self.tracks.items():
            for j, obs in enumerate(people):
                a = self._affinity(track, obs, t)
                if a > 0:
                    pairs.append((a, tid, j))
        pairs.sort(reverse=True)
        used_tracks: set[int] = set()
        used_obs: set[int] = set()
        seen: list[Track] = []
        for _a, tid, j in pairs:
            if tid in used_tracks or j in used_obs:
                continue
            used_tracks.add(tid)
            used_obs.add(j)
            track = self.tracks[tid]
            obs = people[j]
            dt = t - track.last_seen
            if dt > 0:
                alpha = 0.5
                track.vx = alpha * (obs.box.cx - track.box.cx) / dt + (1 - alpha) * track.vx
                track.vy = alpha * (obs.box.cy - track.box.cy) / dt + (1 - alpha) * track.vy
            track.box = obs.box
            track.last_seen = t
            track.hits += 1
            track.obs = obs
            seen.append(track)
        for j, obs in enumerate(people):
            if j in used_obs:
                continue
            track = Track(id=self._next_id, box=obs.box, first_seen=t, last_seen=t, obs=obs)
            self._next_id += 1
            self.tracks[track.id] = track
            seen.append(track)
        ended = [tr for tr in self.tracks.values() if t - tr.last_seen > self.timeout]
        for tr in ended:
            del self.tracks[tr.id]
        return seen, ended

    def flush(self) -> list[Track]:
        ended = list(self.tracks.values())
        self.tracks.clear()
        return ended
