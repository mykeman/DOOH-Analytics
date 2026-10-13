"""Durable local queue of audience events (SQLite).

Events are written here first and uploaded asynchronously, so the agent keeps
counting through network outages and server maintenance.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .sessions import AudienceEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    created REAL NOT NULL,
    payload TEXT NOT NULL,
    sent_at REAL
);
CREATE INDEX IF NOT EXISTS events_unsent ON events(sent_at, created);
CREATE INDEX IF NOT EXISTS events_created ON events(created);
"""


class Outbox:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)

    def add(self, event: AudienceEvent, created: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO events (id, created, payload) VALUES (?, ?, ?)",
                (event.event_id, created or time.time(), json.dumps(asdict(event))),
            )

    def pending(self, limit: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload FROM events WHERE sent_at IS NULL ORDER BY created LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def mark_sent(self, ids: list[str]) -> None:
        now = time.time()
        with self._lock:
            self._db.executemany(
                "UPDATE events SET sent_at = ? WHERE id = ?", [(now, i) for i in ids]
            )

    def pending_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM events WHERE sent_at IS NULL").fetchone()[0]

    def prune(self, retention_days: int, max_unsent: int) -> int:
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            n = self._db.execute(
                "DELETE FROM events WHERE sent_at IS NOT NULL AND created < ?", (cutoff,)
            ).rowcount
            # If the server has been unreachable for very long, drop the oldest
            # unsent events rather than filling the disk.
            excess = (
                self._db.execute("SELECT COUNT(*) FROM events WHERE sent_at IS NULL").fetchone()[0]
                - max_unsent
            )
            if excess > 0:
                n += self._db.execute(
                    "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE sent_at IS NULL "
                    "ORDER BY created LIMIT ?)",
                    (excess,),
                ).rowcount
        return n

    def today_summary(self, now: float | None = None) -> dict:
        """Local-time "today" stats for the on-device dashboard."""
        now = now or time.time()
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._lock:
            rows = self._db.execute(
                "SELECT created, payload FROM events WHERE created >= ?", (midnight.timestamp(),)
            ).fetchall()
        hourly = [[0, 0] for _ in range(24)]
        impressions = viewers = branded = 0
        attention = 0.0
        brands: dict[str, int] = {}
        for created, payload in rows:
            e = json.loads(payload)
            hour = datetime.fromtimestamp(created).hour
            impressions += 1
            hourly[hour][0] += 1
            if e["viewed"]:
                viewers += 1
                attention += e["attention_seconds"]
                hourly[hour][1] += 1
            if e.get("brand"):
                branded += 1
                brands[e["brand"]] = brands.get(e["brand"], 0) + 1
        return {
            "impressions": impressions,
            "viewers": viewers,
            "view_rate": round(viewers / impressions, 4) if impressions else 0.0,
            "avg_attention_seconds": round(attention / viewers, 2) if viewers else 0.0,
            "branded_bag_carriers": branded,
            "brands": sorted(brands.items(), key=lambda kv: kv[1], reverse=True),
            "hourly": [{"hour": h, "impressions": v[0], "viewers": v[1]} for h, v in enumerate(hourly)],
        }

    def close(self) -> None:
        with self._lock:
            self._db.close()
