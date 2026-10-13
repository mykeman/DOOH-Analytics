# Architecture

## Edge agent (`edge/dooh_edge`)

```
camera.py      UVC capture thread (V4L2, MJPG), reconnects, always serves the newest frame
perception.py  frame → FrameObs
  detectors/face.py          YuNet face boxes + 5 landmarks          (cv2.FaceDetectorYN)
  attention.py               solvePnP head pose → yaw/pitch → attentive?
  detectors/objects.py       YOLOX / YOLOv8 ONNX: person, handbag, backpack, suitcase
  perception.assign_*        faces → body head-region, bags → nearest body
tracker.py     greedy IoU / centre-distance tracker with constant-velocity prediction
agent.py       per track: throttled age/gender + bag-brand classification on crops
  detectors/demographics.py  ONNX age (class midpoints or regression) + gender
  detectors/brands.py        ORB logo matching (RANSAC) + HSV colour + optional classifier
sessions.py    accumulates per-person evidence → AudienceEvent when the track ends
outbox.py      SQLite queue (WAL); retention + cap on unsent events
sync.py        batched upload, heartbeat with live status, remote settings & brand templates
local_ui.py    on-device dashboard + optional annotated preview (localhost)
simulator.py   synthetic FrameObs for demos and tests
```

Design choices:

* **Event per person, decided on-device.** All per-frame data stays in memory on
  the thin client. Only the final summary (timings, buckets, brand) is stored
  and uploaded, which keeps bandwidth tiny and avoids transmitting personal data.
* **Attention = head pose.** With the camera at the screen, a face oriented
  towards the camera is oriented towards the screen. Attention time is credited
  only between consecutive attentive frames (≤ 0.5 s apart), so stalls and
  single-frame glitches do not inflate it.
* **Cheap per frame, expensive per person.** Detection runs each processed frame;
  age/gender and brand classifiers run every Nth frame of each person and stop
  once enough samples are collected. Results are aggregated (median age,
  confidence-weighted gender vote, brand vote threshold) to suppress noise.
* **No re-identification.** Tracks are purely geometric and expire after
  `track_timeout_seconds`; no face embeddings are computed.

## Central server (`server/dooh_server`)

* FastAPI + SQLAlchemy 2; SQLite for small installs, PostgreSQL for production.
* `models.py` – `Account` (tree via `parent_id`), `User`, `Membership(role)`,
  `Screen` (hashed device key), `Brand`/`BrandTemplate`, `AudienceEvent`.
* `tenancy.py` – `AccessScope` resolves a user's effective role on every
  account (membership on an account ⇒ same role on its whole subtree; highest
  role wins). Every router derives its queries from `scope.account_ids`; objects
  outside the scope return **404** (not 403) so their existence is not revealed.
* Device auth: `X-Device-Key: dooh_<prefix>_<secret>`. Lookup by prefix, constant
  time compare of SHA-256(secret). Keys are shown once and can be rotated.
* Events carry the account at capture time (`audience_events.account_id`), so
  moving a screen to another client does not hand over its history.
* Ingest is idempotent on the agent-generated `event_id`.
* Analytics are computed with SQL aggregates (totals, breakdowns, per screen)
  plus a single streamed pass for timezone-aware hour/day buckets. For very
  large networks, add an hourly rollup table populated at ingest time.

### API overview

| | |
|---|---|
| `POST /api/v1/auth/login` | email + password → bearer token |
| `GET /api/v1/accounts` · `POST` · `PATCH/DELETE /{id}` | account tree |
| `GET/POST/PATCH/DELETE /api/v1/accounts/{id}/members[/{user}]` | users and roles |
| `GET/POST /api/v1/screens` · `PATCH/DELETE /{id}` · `POST /{id}/rotate-key` | screens |
| `GET/POST /api/v1/accounts/{id}/brands` · `POST /api/v1/brands/{id}/templates` | brands |
| `GET /api/v1/analytics/summary?account_id&screen_id&start&end&tz` | dashboard data |
| `GET /api/v1/analytics/export.csv` | raw events |
| `POST /api/v1/ingest/events` · `/heartbeat` · `GET /config` | edge agents (device key) |

Interactive docs: `/docs`.
