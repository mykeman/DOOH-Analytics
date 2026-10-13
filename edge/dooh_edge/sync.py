"""Communication with the central server: event upload, heartbeat, remote config."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import requests

from . import __version__
from .config import ServerConfig
from .outbox import Outbox

log = logging.getLogger(__name__)


class SyncClient:
    def __init__(
        self,
        cfg: ServerConfig,
        outbox: Outbox,
        status_fn: Callable[[], dict],
        on_config: Callable[[dict], None] | None = None,
        template_dir: Path | None = None,
    ):
        self.cfg = cfg
        self.outbox = outbox
        self.status_fn = status_fn
        self.on_config = on_config
        self.template_dir = template_dir
        self.session = requests.Session()
        self.session.headers.update(
            {"X-Device-Key": cfg.device_key, "User-Agent": f"dooh-edge/{__version__}"}
        )
        self.session.verify = cfg.verify_tls
        self.base = cfg.url.rstrip("/")
        self.connected = False
        self.last_error: str | None = None
        self.last_upload_at: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = 0.0
        self._next_retry = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.url and self.cfg.device_key)

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _fail(self, msg: str) -> None:
        self.connected = False
        self.last_error = msg
        self._backoff = min(max(self._backoff * 2, 5.0), 300.0)
        self._next_retry = time.monotonic() + self._backoff
        log.warning("Server sync: %s (retry in %.0fs)", msg, self._backoff)

    def _ok(self) -> None:
        self.connected = True
        self.last_error = None
        self._backoff = 0.0

    # -- operations ----------------------------------------------------------

    def upload_pending(self) -> int:
        """Upload queued events in batches. Returns the number acknowledged."""
        sent = 0
        while not self._stop.is_set():
            batch = self.outbox.pending(self.cfg.batch_size)
            if not batch:
                break
            try:
                r = self.session.post(
                    self._url("/api/v1/ingest/events"), json={"events": batch}, timeout=self.cfg.timeout
                )
            except requests.RequestException as exc:
                self._fail(f"upload failed: {exc}")
                break
            if r.status_code == 422:
                # A malformed event must not block the queue forever: upload the
                # batch one by one and drop the ones the server rejects.
                sent += self._upload_individually(batch)
                continue
            if r.status_code != 200:
                self._fail(f"upload rejected: HTTP {r.status_code} {r.text[:200]}")
                break
            self.outbox.mark_sent([e["event_id"] for e in batch])
            sent += len(batch)
            self._ok()
            self.last_upload_at = time.time()
        return sent

    def _upload_individually(self, batch: list[dict]) -> int:
        ok = 0
        for event in batch:
            try:
                r = self.session.post(
                    self._url("/api/v1/ingest/events"), json={"events": [event]}, timeout=self.cfg.timeout
                )
            except requests.RequestException as exc:
                self._fail(f"upload failed: {exc}")
                return ok
            if r.status_code == 200:
                ok += 1
            elif r.status_code == 422:
                log.error("Server rejected event %s, dropping: %s", event.get("event_id"), r.text[:300])
            else:
                self._fail(f"upload rejected: HTTP {r.status_code}")
                return ok
            self.outbox.mark_sent([event["event_id"]])
        return ok

    def heartbeat(self) -> None:
        try:
            r = self.session.post(
                self._url("/api/v1/ingest/heartbeat"),
                json={"agent_version": __version__, "status": self.status_fn()},
                timeout=self.cfg.timeout,
            )
        except requests.RequestException as exc:
            self._fail(f"heartbeat failed: {exc}")
            return
        if r.status_code == 204:
            self._ok()
        else:
            self._fail(f"heartbeat rejected: HTTP {r.status_code}")

    def fetch_config(self) -> dict | None:
        try:
            r = self.session.get(self._url("/api/v1/ingest/config"), timeout=self.cfg.timeout)
        except requests.RequestException as exc:
            self._fail(f"config fetch failed: {exc}")
            return None
        if r.status_code != 200:
            self._fail(f"config fetch rejected: HTTP {r.status_code}")
            return None
        config = r.json()
        if self.template_dir is not None:
            for brand in config.get("brands", []):
                brand["template_paths"] = [
                    p for t in brand.get("templates", []) if (p := self._download_template(t))
                ]
        if self.on_config:
            self.on_config(config)
        return config

    def _download_template(self, t: dict) -> Path | None:
        assert self.template_dir is not None
        self.template_dir.mkdir(parents=True, exist_ok=True)
        path = self.template_dir / f"{t['sha256']}.img"
        if path.exists():
            return path
        try:
            r = self.session.get(self._url(t["url"]), timeout=self.cfg.timeout)
        except requests.RequestException as exc:
            log.warning("Template download failed: %s", exc)
            return None
        if r.status_code != 200 or hashlib.sha256(r.content).hexdigest() != t["sha256"]:
            log.warning("Template %s download failed or corrupted", t.get("id"))
            return None
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return path

    # -- background loop -----------------------------------------------------

    def start(self) -> SyncClient:
        if not self.enabled:
            log.warning("No server url/device_key configured: events are only stored locally")
            return self
        self._thread = threading.Thread(target=self._run, name="sync", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        next_upload = next_hb = next_cfg = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            backing_off = lambda: time.monotonic() < self._next_retry  # noqa: E731
            if not backing_off() and now >= next_cfg:
                next_cfg = now + self.cfg.config_interval
                try:
                    self.fetch_config()
                except Exception:  # never let a bad config kill the sync thread
                    log.exception("Applying remote config failed")
            if not backing_off() and now >= next_hb:
                next_hb = now + self.cfg.heartbeat_interval
                self.heartbeat()
            if not backing_off() and now >= next_upload:
                next_upload = now + self.cfg.upload_interval
                self.upload_pending()
            self._stop.wait(1.0)
        # Best effort final flush on shutdown.
        self._stop.clear()
        self.upload_pending()
        self._stop.set()
