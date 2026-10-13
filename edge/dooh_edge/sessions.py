"""Turn per-frame track observations into one anonymous event per person."""

from __future__ import annotations

import statistics
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import AnalyticsConfig
from .detectors.demographics import age_bucket
from .tracker import Track
from .types import PersonObs

# Gap between frames above which time is not credited as attention (e.g. the
# pipeline stalled); keeps one slow frame from inflating attention time.
MAX_FRAME_GAP = 0.5


@dataclass
class PersonSession:
    track_id: int
    started: float
    last_seen: float
    frames: int = 0
    face_frames: int = 0
    attention_seconds: float = 0.0
    attentive_now: bool = False
    ages: list[float] = field(default_factory=list)
    gender_scores: Counter = field(default_factory=Counter)
    demographic_samples: int = 0
    bag_frames: int = 0
    brand_votes: Counter = field(default_factory=Counter)
    brand_checks: int = 0


@dataclass
class AudienceEvent:
    event_id: str
    started_at: str
    ended_at: str
    dwell_seconds: float
    attention_seconds: float
    viewed: bool
    age_bucket: str | None
    gender: str | None
    has_bag: bool
    brand: str | None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class SessionAggregator:
    def __init__(self, cfg: AnalyticsConfig):
        self.cfg = cfg
        self.sessions: dict[int, PersonSession] = {}

    # -- per-frame ---------------------------------------------------------

    def observe(self, track: Track, obs: PersonObs, t: float) -> PersonSession:
        s = self.sessions.get(track.id)
        if s is None:
            s = PersonSession(track_id=track.id, started=t, last_seen=t)
            self.sessions[track.id] = s
        gap = t - s.last_seen
        s.frames += 1
        face = obs.face
        attentive = bool(face and face.attentive)
        # Credit the interval since the previous frame when the person was
        # attentive at both ends of it (conservative: no credit on transitions).
        if attentive and s.attentive_now and 0 < gap <= MAX_FRAME_GAP:
            s.attention_seconds += gap
        s.attentive_now = attentive
        if face is not None:
            s.face_frames += 1
            if face.age is not None:
                s.ages.append(face.age)
            if face.gender is not None:
                s.gender_scores[face.gender] += face.gender_conf or 1.0
            if face.age is not None or face.gender is not None:
                s.demographic_samples += 1
        if obs.bags:
            s.bag_frames += 1
            for bag in obs.bags:
                if bag.brand:
                    s.brand_votes[bag.brand] += 1
        s.last_seen = t
        return s

    # The expensive classifiers run on every Nth frame of each person, and stop
    # once enough evidence has been collected for that person.

    def wants_demographics(self, track_id: int) -> bool:
        s = self.sessions.get(track_id)
        if s is None:
            return True
        if s.demographic_samples >= self.cfg.demographics_max_samples:
            return False
        return s.frames % max(1, self.cfg.demographics_every) == 0

    def wants_brand(self, track_id: int) -> bool:
        s = self.sessions.get(track_id)
        if s is None:
            return True
        if s.brand_votes and s.brand_votes.most_common(1)[0][1] >= 3 * self.cfg.brand_min_votes:
            return False
        return s.frames % max(1, self.cfg.brand_every) == 0

    # -- end of track ------------------------------------------------------

    def finish(self, track: Track) -> AudienceEvent | None:
        s = self.sessions.pop(track.id, None)
        if s is None:
            return None
        dwell = s.last_seen - s.started
        if dwell < self.cfg.min_track_seconds or s.frames < 2:
            return None
        gender = None
        if s.gender_scores:
            top, top_score = s.gender_scores.most_common(1)[0]
            total = sum(s.gender_scores.values())
            if top_score / total >= 0.6:
                gender = top
        age = age_bucket(statistics.median(s.ages)) if len(s.ages) >= 2 else None
        has_bag = s.bag_frames >= self.cfg.bag_min_frames
        brand = None
        if has_bag and s.brand_votes:
            name, votes = s.brand_votes.most_common(1)[0]
            if votes >= self.cfg.brand_min_votes:
                brand = name
        attention = min(s.attention_seconds, dwell)
        return AudienceEvent(
            event_id=str(uuid.uuid4()),
            started_at=_iso(s.started),
            ended_at=_iso(s.last_seen),
            dwell_seconds=round(dwell, 2),
            attention_seconds=round(attention, 2),
            viewed=attention >= self.cfg.min_attention_seconds,
            age_bucket=age,
            gender=gender,
            has_bag=has_bag,
            brand=brand,
        )

    def live_counts(self, now: float, recent: float = 1.0) -> tuple[int, int]:
        """``(people_in_view, looking_now)`` for the status display."""
        active = [s for s in self.sessions.values() if now - s.last_seen <= recent]
        return len(active), sum(1 for s in active if s.attentive_now)
