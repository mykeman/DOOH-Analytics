"""Command line entry point: ``dooh-server serve | create-admin | seed-demo``."""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select


def _init_db():
    from .config import get_settings
    from .db import create_all, init_engine, session_factory

    init_engine(get_settings().database_url)
    create_all()
    return session_factory()()


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run(
        "dooh_server.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips=args.forwarded_allow_ips,
    )


def cmd_create_admin(args: argparse.Namespace) -> None:
    from .models import User
    from .security import hash_password

    password = args.password or getpass.getpass("Password: ")
    if len(password) < 10:
        sys.exit("Password must be at least 10 characters")
    db = _init_db()
    email = args.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=args.name or "", password_hash=hash_password(password))
        db.add(user)
    else:
        user.password_hash = hash_password(password)
    user.is_superadmin = True
    user.is_active = True
    db.commit()
    print(f"Super-admin {email} ready.")


def cmd_seed_demo(args: argparse.Namespace) -> None:
    from .demo import seed_demo

    db = _init_db()
    seed_demo(db, days=args.days)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dooh-server")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="Run the API and dashboard")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--forwarded-allow-ips", default="127.0.0.1")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("create-admin", help="Create or reset a super-admin user")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--password", help="Prompted for when omitted")
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("seed-demo", help="Create demo accounts, users, screens and events")
    p.add_argument("--days", type=int, default=14)
    p.set_defaults(func=cmd_seed_demo)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
