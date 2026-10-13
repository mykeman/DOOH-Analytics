import math

import cv2
import numpy as np
import pytest

from dooh_edge.agent import Agent
from dooh_edge.attention import FACE_MODEL_3D, camera_matrix, head_pose, is_attentive
from dooh_edge.config import AnalyticsConfig, Config
from dooh_edge.detectors.demographics import age_bucket
from dooh_edge.detectors.face import parse_yunet
from dooh_edge.outbox import Outbox
from dooh_edge.perception import assign_bags, assign_faces
from dooh_edge.sessions import SessionAggregator
from dooh_edge.simulator import Simulator
from dooh_edge.tracker import Tracker
from dooh_edge.types import BagObs, Box, FaceObs, FrameObs, PersonObs


def project_face(yaw_deg: float, pitch_deg: float, w=1280, h=720) -> np.ndarray:
    """Landmarks of the generic face model turned by yaw (about y) and pitch (about x)."""
    ry, rx = math.radians(yaw_deg), math.radians(pitch_deg)
    rot_y = np.array([[math.cos(ry), 0, -math.sin(ry)], [0, 1, 0], [math.sin(ry), 0, math.cos(ry)]])
    rot_x = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    rvec, _ = cv2.Rodrigues(rot_y @ rot_x)
    pts, _ = cv2.projectPoints(FACE_MODEL_3D, rvec, np.array([80.0, -40.0, 1600.0]), camera_matrix(w, h), np.zeros(4))
    return pts.reshape(5, 2)


@pytest.mark.parametrize("yaw,pitch", [(0, 0), (20, 0), (-35, 0), (0, 15), (0, -20), (25, 10)])
def test_head_pose_recovers_rotation(yaw, pitch):
    est_yaw, est_pitch = head_pose(project_face(yaw, pitch), 1280, 720)
    assert est_yaw == pytest.approx(yaw, abs=1.0)
    assert abs(est_pitch) == pytest.approx(abs(pitch), abs=1.5)


def test_head_pose_sign_conventions():
    frontal = project_face(0, 0)
    # Nose tip moves down relative to eyes/mouth when the head tilts down...
    down = frontal.copy()
    down[2, 1] += 8
    assert head_pose(down, 1280, 720)[1] > 10
    up = frontal.copy()
    up[2, 1] -= 8
    assert head_pose(up, 1280, 720)[1] < -10
    # ...and towards the image right when the head turns right.
    right = frontal.copy()
    right[2, 0] += 8
    assert head_pose(right, 1280, 720)[0] > 10


def test_attention_window():
    assert is_attentive(10, 5, 30, 25)
    assert not is_attentive(45, 0, 30, 25)
    assert not is_attentive(0, 30, 30, 25)
    # Camera above the screen: viewers look ~15 degrees below it.
    assert is_attentive(0, 35, 30, 25, pitch_offset=15)
    assert not is_attentive(None, None, 30, 25)


def test_parse_yunet_rows():
    row = np.array([[100, 50, 80, 90, 120, 80, 160, 80, 140, 100, 125, 120, 155, 120, 0.93]], dtype=np.float32)
    faces = parse_yunet(row)
    assert len(faces) == 1
    f = faces[0]
    assert (f.box.x1, f.box.y1, f.box.x2, f.box.y2) == (100, 50, 180, 140)
    assert f.landmarks.shape == (5, 2)
    assert f.score == pytest.approx(0.93)
    assert parse_yunet(row, min_size=100) == []
    assert parse_yunet(None) == []


@pytest.mark.parametrize("age,bucket", [(3, "0-12"), (12.6, "0-12"), (13.0, "13-17"), (17.9, "13-17"),
                                         (18.0, "18-24"), (44.9, "35-44"), (45.0, "45-54"), (80, "65+")])
def test_age_bucket(age, bucket):
    assert age_bucket(age) == bucket


def test_assign_faces_and_bags():
    persons = [(Box(100, 100, 200, 500), 0.9), (Box(400, 120, 500, 520), 0.8)]
    faces = [
        FaceObs(box=Box(130, 110, 170, 160), score=0.9),  # head of person 1
        FaceObs(box=Box(800, 100, 840, 150), score=0.8),  # no body detected
    ]
    people = assign_faces(persons, faces)
    assert people[0].face is faces[0]
    assert people[1].face is None
    assert len(people) == 3 and people[2].face is faces[1]

    bags = [BagObs(box=Box(200, 300, 240, 360), score=0.6), BagObs(box=Box(1000, 300, 1040, 340), score=0.6)]
    assign_bags(people, bags)
    assert people[0].bags == [bags[0]]
    assert not people[1].bags


def _person(x: float, attentive=False, age=None, gender=None, brand=None, bag=False) -> PersonObs:
    face = FaceObs(box=Box(x + 10, 10, x + 50, 60), score=0.9, attentive=attentive, age=age, gender=gender,
                   gender_conf=0.9 if gender else 0.0)
    bags = [BagObs(box=Box(x, 200, x + 30, 240), score=0.6, brand=brand)] if (bag or brand) else []
    return PersonObs(box=Box(x, 0, x + 60, 300), score=0.9, face=face, bags=bags)


def test_tracker_keeps_identity_and_times_out():
    tr = Tracker(timeout=1.0)
    t = 0.0
    ids = set()
    for i in range(20):
        seen, ended = tr.update([_person(100 + 5 * i), _person(800 - 5 * i)], t)
        ids.update(s.id for s in seen)
        assert not ended
        t += 0.1
    assert ids == {1, 2}
    _, ended = tr.update([], t + 1.5)
    assert {e.id for e in ended} == {1, 2}


def test_tracker_does_not_hand_track_to_new_entrant():
    tr = Tracker(timeout=1.5)
    # Person walks out at the left edge...
    for i in range(10):
        tr.update([_person(60 - 10 * i)], i * 0.1)
    # ...and 0.8 s later someone else enters at the same spot.
    seen, _ = tr.update([_person(10)], 1.7)
    assert seen[0].id != 1


def test_session_produces_event_with_majority_votes():
    cfg = AnalyticsConfig(min_attention_seconds=1.0, bag_min_frames=3, brand_min_votes=2)
    agg = SessionAggregator(cfg)
    tr = Tracker()
    t = 0.0
    for i in range(30):
        attentive = 5 <= i < 25  # 2 seconds of attention at 10 fps
        p = _person(100 + i, attentive=attentive, age=30 + (i % 3), gender="female" if i % 5 else "male",
                    brand="Acme" if i % 2 else None, bag=True)
        seen, _ = tr.update([p], t)
        agg.observe(seen[0], p, t)
        t += 0.1
    (track,) = tr.flush()
    event = agg.finish(track)
    assert event is not None
    assert event.dwell_seconds == pytest.approx(2.9, abs=0.01)
    assert event.attention_seconds == pytest.approx(1.9, abs=0.05)
    assert event.viewed
    assert event.age_bucket == "25-34"
    assert event.gender == "female"
    assert event.has_bag and event.brand == "Acme"


def test_short_tracks_are_discarded():
    agg = SessionAggregator(AnalyticsConfig(min_track_seconds=0.5))
    tr = Tracker()
    for i in range(3):
        p = _person(100)
        seen, _ = tr.update([p], i * 0.1)
        agg.observe(seen[0], p, i * 0.1)
    (track,) = tr.flush()
    assert agg.finish(track) is None


def test_attention_not_credited_across_stalls():
    agg = SessionAggregator(AnalyticsConfig())
    tr = Tracker(timeout=10)
    times = [0.0, 0.1, 0.2, 3.0, 3.1]  # 2.8 s pipeline stall in the middle
    for t in times:
        p = _person(100, attentive=True)
        seen, _ = tr.update([p], t)
        agg.observe(seen[0], p, t)
    (track,) = tr.flush()
    assert agg.finish(track).attention_seconds == pytest.approx(0.3, abs=0.01)


def test_agent_with_simulator_end_to_end(tmp_path):
    outbox = Outbox(tmp_path / "outbox.db")
    agent = Agent(Config(), outbox)
    sim = Simulator(people_per_minute=10, seed=5)
    t = 1_700_000_000.0
    for _ in range(int(600 / 0.1)):
        agent.process(sim.step(t, 0.1))
        t += 0.1
    agent.flush()
    pending = outbox.pending(10_000)
    # ~100 people in 10 minutes; tracking should neither merge nor split much.
    assert 80 <= len(pending) <= 115
    assert any(e["viewed"] for e in pending)
    assert any(e["brand"] for e in pending)
    assert all(e["attention_seconds"] <= e["dwell_seconds"] for e in pending)
    outbox.mark_sent([e["event_id"] for e in pending[:10]])
    assert outbox.pending_count() == len(pending) - 10


def test_outbox_prune_caps_unsent(tmp_path):
    from dooh_edge.sessions import AudienceEvent

    ob = Outbox(tmp_path / "o.db")
    for i in range(10):
        ob.add(AudienceEvent(f"evt-{i:04d}", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:05+00:00",
                             5, 0, False, None, None, False, None), created=1000 + i)
    assert ob.prune(retention_days=30, max_unsent=4) == 6
    assert [e["event_id"] for e in ob.pending(100)] == ["evt-0006", "evt-0007", "evt-0008", "evt-0009"]


def test_remote_settings_are_whitelisted_and_typed():
    cfg = AnalyticsConfig()
    changed = cfg.apply_remote({"attention_yaw_deg": "20", "process_fps": 5, "evil": 1, "min_face_px": "x"})
    assert set(changed) == {"attention_yaw_deg", "process_fps"}
    assert cfg.attention_yaw_deg == 20.0 and isinstance(cfg.attention_yaw_deg, float)


def test_frameobs_with_no_people_ends_tracks():
    agent = Agent(Config(), Outbox(":memory:"))
    for i in range(10):
        agent.process(FrameObs(timestamp=i * 0.1, width=640, height=480, people=[_person(100 + i)]))
    events = agent.process(FrameObs(timestamp=5.0, width=640, height=480))
    assert len(events) == 1
