"""``dooh-edge`` command line: run | simulate | list-cameras | snapshot | check."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .config import DEFAULT_CONFIG_PATH, Config

log = logging.getLogger("dooh_edge")


def _load_config(path: str | None) -> Config:
    if path is None and DEFAULT_CONFIG_PATH.exists():
        path = str(DEFAULT_CONFIG_PATH)
    cfg = Config.load(path)
    if path:
        log.info("Loaded configuration from %s", path)
    return cfg


def _install_signal_handlers(stop_fn) -> None:
    def handler(signum, _frame):
        log.info("Received signal %d, shutting down", signum)
        stop_fn()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _start_services(cfg: Config, agent):
    from .local_ui import start_local_ui
    from .sync import SyncClient

    screen = {"name": None}

    def on_config(config: dict) -> None:
        screen["name"] = config.get("screen_name")
        agent.apply_remote_config(config)

    sync = SyncClient(
        cfg.server,
        agent.outbox,
        agent.status,
        on_config=on_config,
        template_dir=Path(cfg.models.dir) / "brands",
    ).start()
    agent.sync = sync
    ui = None
    if cfg.local_ui.enabled:
        ui = start_local_ui(agent, cfg.local_ui.bind, cfg.local_ui.port, lambda: screen["name"])
    return sync, ui


def cmd_run(args) -> int:
    from .agent import Agent
    from .camera import Camera
    from .outbox import Outbox
    from .perception import Perception

    cfg = _load_config(args.config)
    perception = Perception(cfg.models, cfg.analytics)
    outbox = Outbox(cfg.storage.path)
    agent = Agent(cfg, outbox, perception)
    camera = Camera(cfg.camera).start()
    sync, ui = _start_services(cfg, agent)
    _install_signal_handlers(agent.stop)
    log.info("dooh-edge %s running", __version__)
    try:
        agent.run(camera)
    finally:
        camera.stop()
        sync.stop()
        if ui:
            ui.shutdown()
        outbox.close()
    return 0


def cmd_simulate(args) -> int:
    from .agent import Agent
    from .outbox import Outbox
    from .simulator import Simulator

    cfg = _load_config(args.config)
    if args.server:
        cfg.server.url = args.server
    if args.device_key:
        cfg.server.device_key = args.device_key
    if args.db:
        cfg.storage.path = args.db
    outbox = Outbox(cfg.storage.path)
    agent = Agent(cfg, outbox)
    agent.mode = "simulator"
    sim = Simulator(people_per_minute=args.rate, seed=args.seed)
    stop = threading.Event()
    _install_signal_handlers(stop.set)

    if args.backfill_hours:
        # Generate past traffic quickly with a synthetic clock, then exit.
        dt = 1.0 / cfg.analytics.process_fps
        t = time.time() - args.backfill_hours * 3600
        end = time.time()
        while t < end and not stop.is_set():
            agent.process(sim.step(t, dt))
            t += dt
        agent.flush()
        print(f"Generated {agent.events_emitted} events into {cfg.storage.path}")
        if cfg.server.url and cfg.server.device_key:
            from .sync import SyncClient

            n = SyncClient(cfg.server, outbox, agent.status).upload_pending()
            print(f"Uploaded {n} events to {cfg.server.url}")
        outbox.close()
        return 0

    sync, ui = _start_services(cfg, agent)
    log.info("Simulating %.0f people/minute; press Ctrl+C to stop", args.rate)
    dt = 1.0 / cfg.analytics.process_fps
    try:
        while not stop.is_set():
            agent.process(sim.step(time.time(), dt))
            stop.wait(dt)
    finally:
        agent.flush()
        sync.stop()
        if ui:
            ui.shutdown()
        outbox.close()
    return 0


def cmd_list_cameras(args) -> int:
    from .camera import list_cameras

    cams = list_cameras()
    if not cams:
        print("No /dev/video* devices found. Is the UVC camera plugged in?")
        return 1
    for c in cams:
        print(json.dumps(c))
    return 0


def cmd_snapshot(args) -> int:
    """Grab one frame (for aiming the camera during installation)."""
    import cv2

    from .camera import Camera

    cfg = _load_config(args.config)
    cam = Camera(cfg.camera).start()
    try:
        frame = cam.read(timeout=10)
        if frame is None:
            print(f"No frame from camera: {cam.error}", file=sys.stderr)
            return 1
        cv2.imwrite(args.output, frame.image)
        print(f"Saved {frame.image.shape[1]}x{frame.image.shape[0]} frame to {args.output}")
        return 0
    finally:
        cam.stop()


def cmd_check(args) -> int:
    """Validate configuration, models and server connectivity."""
    cfg = _load_config(args.config)
    ok = True
    m = cfg.models
    for label, name, required in [
        ("face detector", m.face, True),
        ("age model", m.age, False),
        ("gender model", m.gender, False),
        ("person/bag detector", m.objects, False),
        ("brand classifier", m.brand_classifier, False),
    ]:
        p = m.path(name)
        if p is None:
            print(f"[--] {label}: disabled")
        elif p.exists():
            print(f"[ok] {label}: {p}")
        else:
            print(f"[{'!!' if required else '--'}] {label}: missing {p}")
            ok = ok and not required
    if cfg.server.url and cfg.server.device_key:
        from .outbox import Outbox
        from .sync import SyncClient

        client = SyncClient(cfg.server, Outbox(":memory:"), lambda: {"check": True})
        config = client.fetch_config()
        if config:
            print(f"[ok] server: authenticated as screen '{config['screen_name']}' (id {config['screen_id']})")
            print(f"     {len(config['brands'])} brand(s) configured")
        else:
            print(f"[!!] server: {client.last_error}")
            ok = False
    else:
        print("[--] server: not configured (events stay local)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dooh-edge", description="DOOH audience analytics edge agent")
    parser.add_argument("-c", "--config", help=f"TOML config (default {DEFAULT_CONFIG_PATH})")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="Run the agent on the configured camera").set_defaults(func=cmd_run)

    p = sub.add_parser("simulate", help="Generate synthetic audience data (no camera needed)")
    p.add_argument("--rate", type=float, default=20, help="People per minute")
    p.add_argument("--seed", type=int)
    p.add_argument("--server", help="Override server.url")
    p.add_argument("--device-key", help="Override server.device_key")
    p.add_argument("--db", help="Override storage.path")
    p.add_argument("--backfill-hours", type=float, default=0, help="Generate past data quickly and exit")
    p.set_defaults(func=cmd_simulate)

    sub.add_parser("list-cameras", help="List V4L2 video devices").set_defaults(func=cmd_list_cameras)

    p = sub.add_parser("snapshot", help="Save one camera frame to a file (installation aid)")
    p.add_argument("-o", "--output", default="snapshot.jpg")
    p.set_defaults(func=cmd_snapshot)

    sub.add_parser("check", help="Check models and server connectivity").set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
