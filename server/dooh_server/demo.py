"""Synthetic demo data so the dashboard can be explored without cameras."""

from __future__ import annotations

import math
import random
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, AudienceEvent, Brand, Membership, Role, Screen, User, utcnow
from .schemas import AGE_BUCKETS
from .security import generate_device_key, hash_password

DEMO_PASSWORD = "demo-password"

AGE_WEIGHTS = [2, 6, 18, 24, 20, 14, 10, 6]


def _user(db: Session, email: str, name: str, superadmin: bool = False) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email, name=name, password_hash=hash_password(DEMO_PASSWORD), is_superadmin=superadmin
        )
        db.add(user)
        db.flush()
    return user


def _events_for(screen: Screen, days: int, base_rate: float, brands: list[str], rng: random.Random):
    now = utcnow()
    start = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    t = start
    while t < now:
        hour = t.hour
        # Two daily peaks (lunch and evening) plus quieter nights.
        shape = 0.15 + math.exp(-((hour - 12.5) ** 2) / 6) + 0.8 * math.exp(-((hour - 18) ** 2) / 5)
        weekend = 1.35 if t.weekday() >= 5 else 1.0
        n = max(0, int(rng.gauss(base_rate * shape * weekend, 3)))
        for _ in range(n):
            started = t + timedelta(seconds=rng.uniform(0, 3600))
            if started >= now:
                continue
            dwell = max(0.8, rng.lognormvariate(1.3, 0.7))
            viewed = rng.random() < 0.38
            attention = min(dwell, rng.lognormvariate(0.9, 0.6)) if viewed else 0.0
            has_bag = rng.random() < 0.3
            brand = rng.choice(brands) if has_bag and rng.random() < 0.55 else None
            face_seen = viewed or rng.random() < 0.3
            yield AudienceEvent(
                event_uuid=str(uuid.UUID(int=rng.getrandbits(128))),
                screen_id=screen.id,
                account_id=screen.account_id,
                started_at=started,
                ended_at=started + timedelta(seconds=dwell),
                dwell_seconds=round(dwell, 2),
                attention_seconds=round(attention, 2),
                viewed=viewed,
                age_bucket=rng.choices(AGE_BUCKETS, AGE_WEIGHTS)[0] if face_seen else None,
                gender=rng.choice(["male", "female"]) if face_seen else None,
                has_bag=has_bag,
                brand=brand,
            )
        t += timedelta(hours=1)


def seed_demo(db: Session, days: int = 14, seed: int = 7) -> dict:
    rng = random.Random(seed)
    if db.scalar(select(Account).where(Account.name == "Metro Media Network")):
        print("Demo data already present.")
        return {}

    agency = Account(name="Metro Media Network")
    db.add(agency)
    db.flush()
    mall = Account(name="Harbour Mall", parent_id=agency.id)
    rail = Account(name="City Rail", parent_id=agency.id)
    db.add_all([mall, rail])
    db.flush()
    mall_north = Account(name="North Wing", parent_id=mall.id)
    db.add(mall_north)
    db.flush()

    for name in ["Northwind Apparel", "Bluebird Books", "Summit Sports"]:
        db.add(Brand(account_id=agency.id, name=name, colors=[]))
    db.add(Brand(account_id=mall.id, name="Harbour Mall Gift Bag", colors=[]))

    admin = _user(db, "admin@demo.local", "Network Admin", superadmin=True)
    agency_mgr = _user(db, "ops@demo.local", "Agency Ops")
    mall_viewer = _user(db, "client@harbourmall.example", "Harbour Mall Marketing")
    db.add_all(
        [
            Membership(user_id=agency_mgr.id, account_id=agency.id, role=Role.editor.value),
            Membership(user_id=mall_viewer.id, account_id=mall.id, role=Role.viewer.value),
        ]
    )

    screens_spec = [
        (mall, "Atrium Portrait 1", "Ground floor atrium", 26),
        (mall, "Food Court Wall", "Level 2 food court", 32),
        (mall_north, "North Entrance", "North wing entrance", 18),
        (rail, "Central Platform 3", "Central Station", 40),
        (rail, "Concourse Video Wall", "Central Station concourse", 55),
    ]
    keys = {}
    brand_names = ["Northwind Apparel", "Bluebird Books", "Summit Sports"]
    for account, name, location, rate in screens_spec:
        key, prefix, key_hash = generate_device_key()
        screen = Screen(
            account_id=account.id,
            name=name,
            location=location,
            timezone="UTC",
            key_prefix=prefix,
            key_hash=key_hash,
            last_seen_at=utcnow() - timedelta(seconds=rng.choice([20, 40, 3600])),
            agent_version="0.1.0",
            last_status={"camera_ok": True, "fps": round(rng.uniform(8, 15), 1),
                         "people_now": rng.randint(0, 6), "viewers_now": rng.randint(0, 3),
                         "queue_depth": 0},
            agent_settings={},
        )
        db.add(screen)
        db.flush()
        keys[name] = key
        extra = ["Harbour Mall Gift Bag"] if account in (mall, mall_north) else []
        db.add_all(list(_events_for(screen, days, rate, brand_names + extra, rng)))
    db.commit()

    print(f"Demo data created. All demo users use the password {DEMO_PASSWORD!r}:")
    print("  admin@demo.local            super-admin (sees everything)")
    print("  ops@demo.local              editor on 'Metro Media Network' and all sub-accounts")
    print("  client@harbourmall.example  viewer on 'Harbour Mall' only")
    return {"admin": admin.id, "keys": keys}
