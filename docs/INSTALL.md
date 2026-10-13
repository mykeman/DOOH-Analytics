# Installation guide

## 1. Central server

Requirements: a Linux host with Docker, a DNS name pointing at it, ports 80/443 open.

```bash
git clone <this repo> dooh && cd dooh
cp deploy/server/.env.example deploy/server/.env
# edit .env: DOOH_DOMAIN, DOOH_SECRET_KEY (long random), POSTGRES_PASSWORD
docker compose -f deploy/server/docker-compose.yml up -d --build
docker compose -f deploy/server/docker-compose.yml exec server \
    dooh-server create-admin --email you@example.com
```

Open `https://<DOOH_DOMAIN>/` and sign in. Then:

1. **Accounts & users** – create a top-level account for your network, then a
   sub-account per client (and further levels if useful, e.g. per mall).
   Add users to the account they should see, with the right role.
2. **Brands** – add the retail brands whose bags you want to recognise and upload
   a clean, tightly cropped logo image for each (several images per brand are
   fine: different bag designs, light/dark variants).
3. **Screens** – add each screen under the client account it belongs to. The
   device key is shown **once**; copy it for the thin-client install.

Without Docker: `pip install "./server[postgres]"`, set `DOOH_DATABASE_URL` and
`DOOH_SECRET_KEY`, run `dooh-server serve` behind nginx/Caddy (see
`deploy/server/dooh-server.service`). SQLite is fine for small networks.

Server settings (environment variables):

| Variable | Default | |
|---|---|---|
| `DOOH_DATABASE_URL` | `sqlite:///./dooh.db` | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pw@host/db` |
| `DOOH_SECRET_KEY` | random per start | signs login tokens – **set it** |
| `DOOH_TOKEN_TTL_MINUTES` | `720` | dashboard session length |
| `DOOH_OFFLINE_AFTER_SECONDS` | `180` | screen shown offline after this long without a heartbeat |
| `DOOH_CORS_ORIGINS` | – | comma-separated origins if the API is used from another site |

## 2. Thin client at each screen

Supported: Debian 11+/Ubuntu 20.04+ (x86_64 or arm64, e.g. Intel NUC-class or
Raspberry Pi 5), Python ≥ 3.10, any UVC webcam. Throughput depends heavily on
the CPU: check the processing fps shown on the local dashboard, and on weaker
hardware lower `process_fps` (5 is plenty for dwell/attention) or
`objects_input_size` (416 or 320).

### Camera placement

* Mount the camera at the screen – ideally centred just above it, facing the
  audience. Viewing is inferred from people facing the camera, so the camera
  must see what the screen "sees".
* Faces should be at least ~60 px tall at the typical viewing distance for age
  and gender (≥ 36 px for counting attention). A 1080p camera with a 70–90°
  lens covers roughly 1–6 m.
* Avoid strong backlight (windows or the screen's glow behind people).

### Install

```bash
git clone <this repo> dooh && cd dooh
sudo deploy/edge/install.sh --server https://analytics.example.com --key dooh_xxx_yyy
```

This creates a `dooh` service user (in the `video` group), installs the agent
into `/opt/dooh-edge/venv`, downloads the models to `/var/lib/dooh-edge/models`,
writes `/etc/dooh-edge/edge.toml` (mode 640) and starts the `dooh-edge` systemd
service. Check it with:

```bash
dooh-edge list-cameras                       # which /dev/video* works
/opt/dooh-edge/venv/bin/dooh-edge -c /etc/dooh-edge/edge.toml check
journalctl -u dooh-edge -f
```

### Aim and calibrate

1. Set `debug_preview = true` under `[local_ui]` and restart the service.
2. Open `http://127.0.0.1:8080/` on the thin client. The preview shows tracked
   people, faces (green = looking at the screen) with their yaw/pitch, and bags.
3. Stand where the audience stands and look at the screen centre. If the face
   is not green, set `pitch_offset_deg` to the displayed pitch.
4. Set `debug_preview = false` again and restart.

Thresholds can also be tuned centrally per screen – the screen's
`agent_settings` (e.g. `{"attention_yaw_deg": 25, "min_attention_seconds": 2}`)
are pushed to the agent every few minutes.

### On-screen dashboard

`http://127.0.0.1:8080/` shows live and today's numbers for that screen and
keeps working when the central server is unreachable – useful for venue staff or
as a kiosk page. Bind it to `0.0.0.0` to open it from the local network.

### Offline behaviour

Events are written to `/var/lib/dooh-edge/outbox.db` first and uploaded in
batches; during outages they queue (up to `storage.max_unsent`) and upload when
the connection returns. Uploads are idempotent, so retries never double count.
