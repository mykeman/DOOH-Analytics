"""Endpoints called by edge agents, authenticated with the per-screen device key."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_device_screen
from ..models import AudienceEvent, BrandTemplate, Screen, utcnow
from ..schemas import (
    DeviceBrand,
    DeviceConfigOut,
    EventBatchIn,
    HeartbeatIn,
    IngestResult,
    to_naive_utc,
)
from ..tenancy import AccountTree
from .brands import effective_brands

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


@router.post("/events", response_model=IngestResult)
def ingest_events(
    body: EventBatchIn,
    screen: Screen = Depends(get_device_screen),
    db: Session = Depends(get_db),
) -> IngestResult:
    if len(body.events) > get_settings().max_events_per_batch:
        raise HTTPException(status_code=413, detail="Too many events in one batch")
    # Uploads are retried by the agent, so dedupe on the agent-generated event id.
    ids = list({e.event_id for e in body.events})
    existing: set[str] = set()
    for i in range(0, len(ids), 500):
        existing.update(
            db.scalars(
                select(AudienceEvent.event_uuid).where(AudienceEvent.event_uuid.in_(ids[i : i + 500]))
            )
        )
    accepted = 0
    seen: set[str] = set()
    for e in body.events:
        if e.event_id in existing or e.event_id in seen:
            continue
        seen.add(e.event_id)
        db.add(
            AudienceEvent(
                event_uuid=e.event_id,
                screen_id=screen.id,
                account_id=screen.account_id,
                started_at=to_naive_utc(e.started_at),
                ended_at=to_naive_utc(e.ended_at),
                dwell_seconds=e.dwell_seconds,
                attention_seconds=min(e.attention_seconds, e.dwell_seconds),
                viewed=e.viewed,
                age_bucket=e.age_bucket,
                gender=e.gender,
                has_bag=e.has_bag or e.brand is not None,
                brand=e.brand,
            )
        )
        accepted += 1
    db.commit()
    return IngestResult(accepted=accepted, duplicates=len(body.events) - accepted)


@router.post("/heartbeat", status_code=204)
def heartbeat(
    body: HeartbeatIn,
    screen: Screen = Depends(get_device_screen),
    db: Session = Depends(get_db),
) -> None:
    screen.last_seen_at = utcnow()
    screen.agent_version = body.agent_version
    screen.last_status = body.status
    db.commit()


@router.get("/config", response_model=DeviceConfigOut)
def device_config(
    screen: Screen = Depends(get_device_screen), db: Session = Depends(get_db)
) -> DeviceConfigOut:
    tree = AccountTree.load(db)
    brands = [
        DeviceBrand(
            name=b.name,
            colors=b.colors or [],
            templates=[
                {"id": t.id, "sha256": t.sha256, "url": f"/api/v1/ingest/brand-templates/{t.id}"}
                for t in b.templates
            ],
        )
        for b, _ in effective_brands(db, tree, screen.account_id)
    ]
    return DeviceConfigOut(
        screen_id=screen.id,
        screen_name=screen.name,
        timezone=screen.timezone,
        settings=screen.agent_settings or {},
        brands=brands,
    )


@router.get("/brand-templates/{template_id}")
def device_template(
    template_id: int,
    screen: Screen = Depends(get_device_screen),
    db: Session = Depends(get_db),
) -> Response:
    template = db.get(BrandTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    tree = AccountTree.load(db)
    if template.brand.account_id not in tree.ancestors(screen.account_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return Response(content=template.data, media_type=template.content_type)
