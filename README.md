# DOOH Audience Analytics

Anonymous audience measurement for digital out-of-home (DOOH) LED screens.

A UVC (USB) camera mounted at each screen is read by a small **edge agent** on
the screen's Linux thin client. The agent counts the people passing the screen,
measures **how long each person looks at it**, estimates **age group and
gender**, and detects **branded shopping bags**. Only anonymous per-person
summaries leave the device – never images. The agents report to a **central
server** that hosts a multi-tenant dashboard: screens are organised into
**accounts and sub-accounts** so each client only ever sees their own screens.

```
 LED screen ── UVC camera
                  │  frames (never stored or sent)
          ┌───────▼─────────────── thin client ─────────────┐
          │ dooh-edge                                        │
          │  face (YuNet) + head pose  → attention / viewing │
          │  person & bag detector (YOLOX)                   │
          │  age / gender classifiers                        │
          │  bag brand matcher (logos, colours, classifier)  │
          │  tracker → one anonymous event per person        │
          │  SQLite outbox ──► uploader      local dashboard │
          └──────────────────────│───────── :8080 ───────────┘
                                 │ HTTPS, per-screen device key
          ┌──────────────────────▼─────── central server ───┐
          │ dooh-server (FastAPI)  PostgreSQL / SQLite       │
          │ accounts tree · users & roles · screens · brands │
          │ analytics API · web dashboard · CSV export       │
          └──────────────────────────────────────────────────┘
```

## What is measured

Per person who passes the screen (one *event*):

| Field | Meaning |
|---|---|
| `dwell_seconds` | time the person was in the camera's view |
| `attention_seconds` | time their head was turned towards the screen (head yaw/pitch within a configurable window) |
| `viewed` | `attention_seconds ≥ min_attention_seconds` (default 1 s) |
| `age_bucket` | `0-12`, `13-17`, `18-24`, `25-34`, `35-44`, `45-54`, `55-64`, `65+` – median of several estimates; empty when the face was never seen clearly |
| `gender` | `male` / `female` majority vote with confidence threshold; empty when uncertain |
| `has_bag`, `brand` | a bag was carried (≥ 3 frames); the recognised brand if enough frames agreed |

The dashboard reports impressions, viewers and view rate, average and total
attention time, dwell time, audience by hour/day, age and gender breakdowns,
branded-bag carriers per brand (and how often bag carriers of each brand looked
at the screen), and a per-screen comparison with live status.

## Repository layout

| Path | |
|---|---|
| `edge/` | `dooh-edge` agent for the thin clients (Python, OpenCV, ONNX Runtime) |
| `server/` | `dooh-server` central API + dashboard (FastAPI, SQLAlchemy) |
| `deploy/edge/` | installer, systemd unit and example config for thin clients |
| `deploy/server/` | Dockerfile, docker-compose (PostgreSQL + Caddy HTTPS), systemd unit |
| `scripts/fetch_models.sh` | downloads the default open models |
| `docs/` | [architecture](docs/ARCHITECTURE.md), [privacy & compliance](docs/PRIVACY.md), [installation guide](docs/INSTALL.md) |

## Quick start (no camera needed)

```bash
# 1. Central server with demo data
pip install -e "server[dev]"
export DOOH_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
dooh-server seed-demo          # demo accounts, users, screens and two weeks of events
dooh-server serve --port 8000  # open http://localhost:8000  (admin@demo.local / demo-password)

# 2. Add a screen in the dashboard (Screens → Add a screen) and copy its device key, then
pip install -e "edge[dev]"
dooh-edge simulate --server http://localhost:8000 --device-key dooh_xxx --db /tmp/edge.db
# Live on-device dashboard: http://127.0.0.1:8080
```

`dooh-edge simulate` runs the real tracking, aggregation, outbox and upload
code on synthetic people, so everything except the camera and neural networks
can be exercised end-to-end.

Demo logins (password `demo-password`):

* `admin@demo.local` – super-admin, sees everything
* `ops@demo.local` – editor on the agency account and all of its clients
* `client@harbourmall.example` – viewer on the *Harbour Mall* client only

## Deploying

See **[docs/INSTALL.md](docs/INSTALL.md)**. In short:

* **Server:** `docker compose -f deploy/server/docker-compose.yml up -d`, then
  `dooh-server create-admin`.
* **Each thin client:** create the screen in the dashboard, then
  `sudo deploy/edge/install.sh --server https://… --key dooh_…`.

## Accounts, sub-accounts and access

Accounts form a tree – e.g. *Network operator → Client → Region/Store*. A user
is added to an account with a role and automatically gets the same access to
every sub-account below it, but never to parent or sibling accounts.

| Role | Can |
|---|---|
| viewer | view dashboards and export CSV |
| editor | + add/edit screens, rotate device keys, manage brands |
| admin | + manage users and sub-accounts, delete screens |

Super-admins (created with `dooh-server create-admin`) manage top-level
accounts. Brands defined on an account apply to all screens below it, so an
agency can maintain a shared brand list while a client adds its own.

## Tests

```bash
cd edge && pytest      # head pose, tracker, sessions, decoders, logo matching, outbox …
cd server && pytest    # tenant isolation, roles, ingest/dedupe, device keys, analytics …
```

## Accuracy and limitations

* Age and gender from a single camera are **estimates**. Accuracy depends on
  the models, lighting, camera height and distance; validate against a manually
  annotated sample at each site type before using them commercially. The
  default open models (GoogLeNet age/gender) are a starting point – the model
  files, input preprocessing and output labels are configurable so you can drop
  in better or custom-trained ONNX models.
* Viewing is inferred from head pose, not eye gaze. Mount the camera at the
  screen (ideally centred just above it) and set `pitch_offset_deg`.
* Brand recognition from logos works when the logo is visible and reasonably
  large; colour signatures help for distinctive bags. For high accuracy, train a
  small classifier on photos of bags at your sites and set `brand_classifier`.
* People are not re-identified (by design), so someone who leaves the view and
  returns is counted again. Dense crowds with heavy occlusion reduce counts.
* Default models were not bundled or run in CI; the inference wrappers are
  unit-tested against synthetic model outputs. Run `dooh-edge check` and the
  installation preview on real hardware before going live.

**Before deploying, read [docs/PRIVACY.md](docs/PRIVACY.md).** Camera-based
audience analytics is regulated in many jurisdictions.
