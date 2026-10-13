"""Audience analytics, always restricted to the caller's accessible accounts."""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_scope, require
from ..models import AudienceEvent, Role, Screen
from ..schemas import AGE_BUCKETS
from ..tenancy import AccessScope
from .screens import screen_out

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


class Filters(BaseModel):
    account_ids: set[int]
    screen_ids: list[int] | None
    start_utc: datetime
    end_utc: datetime
    tz: ZoneInfo

    model_config = {"arbitrary_types_allowed": True}


def get_filters(
    account_id: int | None = Query(default=None, description="Account (includes sub-accounts)"),
    screen_id: list[int] | None = Query(default=None),
    start: date | None = Query(default=None, description="First day (inclusive), local time"),
    end: date | None = Query(default=None, description="Last day (inclusive), local time"),
    tz: str = Query(default="UTC", description="IANA timezone used for day/hour buckets"),
    scope: AccessScope = Depends(get_scope),
) -> Filters:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail=f"Unknown timezone {tz!r}")
    if account_id is not None:
        require(scope, account_id, Role.viewer)
        account_ids = scope.tree.descendants(account_id) & scope.account_ids
    else:
        account_ids = scope.account_ids
    today = datetime.now(zone).date()
    end = end or today
    start = start or end - timedelta(days=6)
    if start > end:
        raise HTTPException(status_code=422, detail="start must not be after end")
    if (end - start).days > 366:
        raise HTTPException(status_code=422, detail="Date range is limited to one year")

    def to_utc(d: date) -> datetime:
        return datetime.combine(d, time.min, zone).astimezone(timezone.utc).replace(tzinfo=None)

    return Filters(
        account_ids=account_ids,
        screen_ids=screen_id,
        start_utc=to_utc(start),
        end_utc=to_utc(end + timedelta(days=1)),
        tz=zone,
    )


def _where(f: Filters):
    cond = [
        AudienceEvent.account_id.in_(f.account_ids or {-1}),
        AudienceEvent.started_at >= f.start_utc,
        AudienceEvent.started_at < f.end_utc,
    ]
    if f.screen_ids:
        cond.append(AudienceEvent.screen_id.in_(f.screen_ids))
    return and_(*cond)


def _rate(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


def _totals(db: Session, f: Filters) -> dict:
    E = AudienceEvent
    row = db.execute(
        select(
            func.count(E.id),
            func.sum(case((E.viewed, 1), else_=0)),
            func.avg(E.dwell_seconds),
            func.sum(E.attention_seconds),
            func.sum(case((E.has_bag, 1), else_=0)),
            func.sum(case((E.brand.is_not(None), 1), else_=0)),
        ).where(_where(f))
    ).one()
    impressions, viewers, avg_dwell, total_attention, bags, branded = row
    impressions = impressions or 0
    viewers = viewers or 0
    avg_attention = db.scalar(select(func.avg(E.attention_seconds)).where(_where(f), E.viewed))
    return {
        "impressions": impressions,
        "viewers": viewers,
        "view_rate": _rate(viewers, impressions),
        "avg_dwell_seconds": round(avg_dwell or 0.0, 2),
        "avg_attention_seconds": round(avg_attention or 0.0, 2),
        "total_attention_seconds": round(total_attention or 0.0, 1),
        "bag_carriers": bags or 0,
        "branded_bag_carriers": branded or 0,
        "branded_bag_rate": _rate(branded or 0, impressions),
    }


def _breakdown(db: Session, f: Filters, column) -> list[tuple]:
    E = AudienceEvent
    return db.execute(
        select(
            column,
            func.count(E.id),
            func.sum(case((E.viewed, 1), else_=0)),
            func.avg(case((E.viewed, E.attention_seconds), else_=None)),
        )
        .where(_where(f))
        .group_by(column)
    ).all()


def _demographics(db: Session, f: Filters) -> dict:
    E = AudienceEvent
    gender = {
        (g or "unknown"): {"impressions": n, "viewers": v or 0, "avg_attention_seconds": round(a or 0, 2)}
        for g, n, v, a in _breakdown(db, f, E.gender)
    }
    age_rows = {b: (n, v or 0, a or 0) for b, n, v, a in _breakdown(db, f, E.age_bucket)}
    age = [
        {
            "bucket": b,
            "impressions": age_rows.get(b, (0, 0, 0))[0],
            "viewers": age_rows.get(b, (0, 0, 0))[1],
            "avg_attention_seconds": round(age_rows.get(b, (0, 0, 0))[2], 2),
        }
        for b in [*AGE_BUCKETS, None]
    ]
    age[-1]["bucket"] = "unknown"
    matrix_rows = db.execute(
        select(E.age_bucket, E.gender, func.count(E.id))
        .where(_where(f), E.viewed)
        .group_by(E.age_bucket, E.gender)
    ).all()
    matrix = [
        {"bucket": b or "unknown", "gender": g or "unknown", "viewers": n} for b, g, n in matrix_rows
    ]
    return {"gender": gender, "age": age, "age_gender_viewers": matrix}


def _brands(db: Session, f: Filters) -> list[dict]:
    E = AudienceEvent
    rows = _breakdown(db, f, E.brand)
    out = [
        {
            "brand": b,
            "carriers": n,
            "viewers": v or 0,
            "view_rate": _rate(v or 0, n),
            "avg_attention_seconds": round(a or 0, 2),
        }
        for b, n, v, a in rows
        if b is not None
    ]
    out.sort(key=lambda r: r["carriers"], reverse=True)
    # Bags whose brand could not be identified are listed last.
    unbranded = db.scalar(
        select(func.count(E.id)).where(_where(f), E.has_bag, E.brand.is_(None))
    )
    if unbranded:
        out.append(
            {"brand": "Unbranded / unknown", "carriers": unbranded, "viewers": None,
             "view_rate": None, "avg_attention_seconds": None}
        )
    return out


def _timeseries(db: Session, f: Filters) -> dict:
    E = AudienceEvent
    span = f.end_utc - f.start_utc
    granularity = "hour" if span <= timedelta(days=3) else "day"
    buckets: Counter = Counter()
    viewed_buckets: Counter = Counter()
    attention: Counter = Counter()
    hour_of_day = [[0, 0] for _ in range(24)]  # [impressions, viewers]
    weekday = [[0, 0] for _ in range(7)]
    stmt = select(E.started_at, E.viewed, E.attention_seconds).where(_where(f))
    for started, viewed, att in db.execute(stmt.execution_options(yield_per=5000)):
        local = started.replace(tzinfo=timezone.utc).astimezone(f.tz)
        key = (
            local.replace(minute=0, second=0, microsecond=0)
            if granularity == "hour"
            else local.replace(hour=0, minute=0, second=0, microsecond=0)
        ).strftime("%Y-%m-%dT%H:%M" if granularity == "hour" else "%Y-%m-%d")
        buckets[key] += 1
        hour_of_day[local.hour][0] += 1
        weekday[local.weekday()][0] += 1
        if viewed:
            viewed_buckets[key] += 1
            attention[key] += att
            hour_of_day[local.hour][1] += 1
            weekday[local.weekday()][1] += 1

    # Emit every bucket in range (including empty ones) so charts have no gaps.
    points = []
    seen = set()
    cursor = f.start_utc.replace(tzinfo=timezone.utc).astimezone(f.tz)
    end_local = f.end_utc.replace(tzinfo=timezone.utc).astimezone(f.tz)
    while cursor < end_local:
        key = cursor.strftime("%Y-%m-%dT%H:%M" if granularity == "hour" else "%Y-%m-%d")
        if key not in seen:  # the repeated hour when DST ends maps to one key
            seen.add(key)
            points.append(
                {
                    "t": key,
                    "impressions": buckets.get(key, 0),
                    "viewers": viewed_buckets.get(key, 0),
                    "attention_seconds": round(attention.get(key, 0.0), 1),
                }
            )
        if granularity == "hour":
            # Step in UTC so DST transitions neither skip nor repeat hours.
            cursor = (cursor.astimezone(timezone.utc) + timedelta(hours=1)).astimezone(f.tz)
        else:
            cursor = datetime.combine(cursor.date() + timedelta(days=1), time.min, f.tz)
    return {
        "granularity": granularity,
        "points": points,
        "hour_of_day": [{"hour": h, "impressions": v[0], "viewers": v[1]} for h, v in enumerate(hour_of_day)],
        "weekday": [{"weekday": d, "impressions": v[0], "viewers": v[1]} for d, v in enumerate(weekday)],
    }


def _by_screen(db: Session, f: Filters) -> list[dict]:
    E = AudienceEvent
    rows = db.execute(
        select(
            E.screen_id,
            func.count(E.id),
            func.sum(case((E.viewed, 1), else_=0)),
            func.avg(case((E.viewed, E.attention_seconds), else_=None)),
            func.avg(E.dwell_seconds),
            func.sum(case((E.brand.is_not(None), 1), else_=0)),
        )
        .where(_where(f))
        .group_by(E.screen_id)
    ).all()
    stats = {r[0]: r[1:] for r in rows}
    stmt: Select = select(Screen).where(Screen.account_id.in_(f.account_ids or {-1}))
    if f.screen_ids:
        stmt = stmt.where(Screen.id.in_(f.screen_ids))
    out = []
    for s in db.scalars(stmt.order_by(Screen.name)):
        n, v, att, dwell, branded = stats.get(s.id, (0, 0, None, None, 0))
        out.append(
            {
                "screen_id": s.id,
                "name": s.name,
                "location": s.location,
                "account_id": s.account_id,
                "impressions": n,
                "viewers": v or 0,
                "view_rate": _rate(v or 0, n),
                "avg_attention_seconds": round(att or 0, 2),
                "avg_dwell_seconds": round(dwell or 0, 2),
                "branded_bag_carriers": branded or 0,
            }
        )
    return out


@router.get("/summary")
def summary(f: Filters = Depends(get_filters), db: Session = Depends(get_db)) -> dict:
    return {
        "range": {"start_utc": f.start_utc.isoformat() + "Z", "end_utc": f.end_utc.isoformat() + "Z"},
        "totals": _totals(db, f),
        "demographics": _demographics(db, f),
        "brands": _brands(db, f),
        "timeseries": _timeseries(db, f),
        "screens": _by_screen(db, f),
    }


@router.get("/live")
def live(
    account_id: int | None = Query(default=None),
    screen_id: list[int] | None = Query(default=None),
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Latest heartbeat status (people in front of the screen right now, etc.)."""
    if account_id is not None:
        require(scope, account_id, Role.viewer)
        ids = scope.tree.descendants(account_id) & scope.account_ids
    else:
        ids = scope.account_ids
    stmt = select(Screen).where(Screen.account_id.in_(ids or {-1}))
    if screen_id:
        stmt = stmt.where(Screen.id.in_(screen_id))
    return [screen_out(s).model_dump(mode="json") for s in db.scalars(stmt.order_by(Screen.name))]


@router.get("/export.csv")
def export_csv(f: Filters = Depends(get_filters), db: Session = Depends(get_db)):
    E = AudienceEvent
    names = dict(
        db.execute(
            select(Screen.id, Screen.name).where(Screen.account_id.in_(f.account_ids or {-1}))
        ).all()
    )
    stmt = (
        select(E)
        .where(_where(f))
        .order_by(E.started_at)
        .execution_options(yield_per=2000)
    )

    def rows():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            ["started_at_utc", "ended_at_utc", "screen_id", "screen", "dwell_seconds",
             "attention_seconds", "viewed", "age_bucket", "gender", "has_bag", "brand"]
        )
        for (e,) in db.execute(stmt):
            w.writerow(
                [e.started_at.isoformat(), e.ended_at.isoformat(), e.screen_id,
                 names.get(e.screen_id, ""), round(e.dwell_seconds, 2),
                 round(e.attention_seconds, 2), int(e.viewed), e.age_bucket or "",
                 e.gender or "", int(e.has_bag), e.brand or ""]
            )
            if buf.tell() > 64 * 1024:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audience-events.csv"},
    )
