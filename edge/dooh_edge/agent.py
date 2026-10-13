"""The edge agent: frames -> people -> tracks -> anonymous audience events."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .camera import Camera
from .config import Config
from .detectors.brands import BrandRecognizer, BrandSpec
from .outbox import Outbox
from .perception import Perception
from .sessions import AudienceEvent, SessionAggregator
from .tracker import Tracker
from .types import FrameObs

log = logging.getLogger(__name__)


class Agent:
    def __init__(self, cfg: Config, outbox: Outbox, perception: Perception | None = None):
        self.cfg = cfg
        self.outbox = outbox
        self.perception = perception
        self.tracker = Tracker(cfg.analytics.iou_match, cfg.analytics.track_timeout_seconds)
        self.sessions = SessionAggregator(cfg.analytics)
        self.camera: Camera | None = None
        self.sync = None  # set by the CLI when a server is configured
        self.mode = "camera"  # or "simulator"
        self.started_at = time.time()
        self.frames_processed = 0
        self.events_emitted = 0
        self._fps = 0.0
        self._last_frame_t: float | None = None
        self._preview: bytes | None = None
        self._preview_lock = threading.Lock()
        self._stop = threading.Event()

    # -- core step (shared by the camera loop and the simulator) --------------

    def process(self, obs: FrameObs, image: np.ndarray | None = None) -> list[AudienceEvent]:
        t = obs.timestamp
        self.tracker.timeout = self.cfg.analytics.track_timeout_seconds
        seen, ended = self.tracker.update(obs.people, t)
        for track in seen:
            person = track.obs
            if image is not None and self.perception is not None:
                # Expensive classifiers run on a subset of frames per person.
                if person.face is not None and self.sessions.wants_demographics(track.id):
                    self.perception.estimate_demographics(image, person.face)
                if person.bags and self.sessions.wants_brand(track.id):
                    for bag in person.bags:
                        self.perception.recognize_brand(image, bag)
            self.sessions.observe(track, person, t)
        events = []
        for track in ended:
            event = self.sessions.finish(track)
            if event is not None:
                self.outbox.add(event, created=t)
                events.append(event)
        self.events_emitted += len(events)
        self.frames_processed += 1
        if self._last_frame_t is not None and t > self._last_frame_t:
            inst = 1.0 / (t - self._last_frame_t)
            self._fps = inst if self._fps == 0 else 0.9 * self._fps + 0.1 * inst
        self._last_frame_t = t
        return events

    def flush(self) -> list[AudienceEvent]:
        events = []
        for track in self.tracker.flush():
            event = self.sessions.finish(track)
            if event is not None:
                self.outbox.add(event)
                events.append(event)
        self.events_emitted += len(events)
        return events

    # -- remote configuration -------------------------------------------------

    def apply_remote_config(self, config: dict) -> None:
        changed = self.cfg.analytics.apply_remote(config.get("settings") or {})
        if changed:
            log.info("Applied remote settings: %s", ", ".join(changed))
        if self.perception is None:
            return
        specs = [
            BrandSpec(
                name=b["name"],
                colors=b.get("colors") or [],
                template_paths=[Path(p) for p in b.get("template_paths", [])],
            )
            for b in config.get("brands", [])
        ]
        m = self.cfg.models
        labels = []
        if m.brand_labels and (lp := m.path(m.brand_labels)) and lp.exists():
            labels = [line.strip() for line in lp.read_text().splitlines() if line.strip()]
        self.perception.brands = BrandRecognizer(
            specs,
            min_inliers=self.cfg.analytics.logo_min_inliers,
            color_min_fraction=self.cfg.analytics.color_min_fraction,
            classifier_path=m.path(m.brand_classifier),
            classifier_labels=labels,
            classifier_input=m.brand_input_size,
        )
        log.info("Brand recognition configured for %d brand(s)", len(specs))

    # -- status ----------------------------------------------------------------

    def status(self) -> dict:
        now = time.time()
        people, viewers = self.sessions.live_counts(now)
        return {
            "mode": self.mode,
            "camera_ok": bool(self.camera and self.camera.ok),
            "camera_error": self.camera.error if self.camera else None,
            "fps": round(self._fps, 1),
            "people_now": people,
            "viewers_now": viewers,
            "queue_depth": self.outbox.pending_count(),
            "events_emitted": self.events_emitted,
            "uptime_seconds": int(now - self.started_at),
            "server_connected": bool(self.sync and self.sync.connected),
            "server_error": self.sync.last_error if self.sync else None,
            "demographics_enabled": bool(self.perception and self.perception.demographics),
            "bag_detection_enabled": bool(self.perception and self.perception.objects),
            "brands_enabled": bool(self.perception and self.perception.brands and self.perception.brands.enabled),
        }

    def preview_jpeg(self) -> bytes | None:
        with self._preview_lock:
            return self._preview

    def _update_preview(self, image: np.ndarray, obs: FrameObs) -> None:
        img = image.copy()
        tracks = {id(t.obs): t.id for t in self.tracker.tracks.values()}
        for p in obs.people:
            b = p.box
            cv2.rectangle(img, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), (214, 120, 42), 2)
            cv2.putText(img, f"#{tracks.get(id(p), '?')}", (int(b.x1), int(b.y1) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (214, 120, 42), 2)
            if p.face:
                f = p.face.box
                color = (12, 163, 12) if p.face.attentive else (59, 59, 208)
                cv2.rectangle(img, (int(f.x1), int(f.y1)), (int(f.x2), int(f.y2)), color, 2)
                if p.face.yaw is not None:
                    cv2.putText(img, f"yaw {p.face.yaw:.0f} pitch {p.face.pitch:.0f}", (int(f.x1), int(f.y2) + 16),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            for bag in p.bags:
                g = bag.box
                cv2.rectangle(img, (int(g.x1), int(g.y1)), (int(g.x2), int(g.y2)), (0, 161, 237), 2)
                cv2.putText(img, bag.brand or bag.label, (int(g.x1), int(g.y2) + 16),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 161, 237), 1)
        scale = 640.0 / max(img.shape[1], 1)
        if scale < 1:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with self._preview_lock:
                self._preview = buf.tobytes()

    # -- camera loop -------------------------------------------------------------

    def run(self, camera: Camera) -> None:
        if self.perception is None:
            raise RuntimeError("Perception models are required to process camera frames")
        self.camera = camera
        last_seq = 0
        next_prune = 0.0
        while not self._stop.is_set():
            min_interval = 1.0 / max(self.cfg.analytics.process_fps, 0.1)
            frame = camera.read(after_seq=last_seq, timeout=2.0)
            if frame is None:
                # No frames: let tracks time out so people are not stuck "in view".
                self.process(FrameObs(timestamp=time.time(), width=0, height=0))
                continue
            last_seq = frame.seq
            started = time.monotonic()
            try:
                obs = self.perception.detect(frame.image, frame.timestamp)
                self.process(obs, frame.image)
                if self.cfg.local_ui.debug_preview:
                    self._update_preview(frame.image, obs)
            except Exception:
                log.exception("Frame processing failed")
            if time.monotonic() >= next_prune:
                next_prune = time.monotonic() + 3600
                removed = self.outbox.prune(self.cfg.storage.retention_days, self.cfg.storage.max_unsent)
                if removed:
                    log.info("Pruned %d old events from the local outbox", removed)
            elapsed = time.monotonic() - started
            if elapsed < min_interval:
                self._stop.wait(min_interval - elapsed)
        self.flush()

    def stop(self) -> None:
        self._stop.set()
