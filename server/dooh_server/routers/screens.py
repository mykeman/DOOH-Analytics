from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_scope, require
from ..models import Role, Screen, utcnow
from ..schemas import ScreenCreated, ScreenIn, ScreenOut, ScreenUpdate
from ..security import generate_device_key
from ..tenancy import AccessScope

router = APIRouter(prefix="/api/v1/screens", tags=["screens"])


def screen_out(screen: Screen) -> ScreenOut:
    out = ScreenOut.model_validate(screen)
    if screen.last_seen_at is not None:
        window = timedelta(seconds=get_settings().offline_after_seconds)
        out.online = utcnow() - screen.last_seen_at <= window
    return out


def _get_screen(db: Session, scope: AccessScope, screen_id: int, minimum: Role) -> Screen:
    screen = db.get(Screen, screen_id)
    if screen is None or screen.account_id not in scope.account_ids:
        raise HTTPException(status_code=404, detail="Screen not found")
    require(scope, screen.account_id, minimum)
    return screen


@router.get("", response_model=list[ScreenOut])
def list_screens(
    account_id: int | None = Query(default=None, description="Include this account's subtree"),
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    if account_id is not None:
        require(scope, account_id, Role.viewer)
        ids = scope.tree.descendants(account_id) & scope.account_ids
    else:
        ids = scope.account_ids
    if not ids:
        return []
    screens = db.scalars(
        select(Screen).where(Screen.account_id.in_(ids)).order_by(Screen.name)
    ).all()
    return [screen_out(s) for s in screens]


@router.post("", response_model=ScreenCreated, status_code=201)
def create_screen(
    body: ScreenIn, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    require(scope, body.account_id, Role.editor)
    key, prefix, key_hash = generate_device_key()
    screen = Screen(
        account_id=body.account_id,
        name=body.name.strip(),
        location=body.location,
        timezone=body.timezone,
        agent_settings=body.agent_settings,
        key_prefix=prefix,
        key_hash=key_hash,
    )
    db.add(screen)
    db.commit()
    return ScreenCreated(**screen_out(screen).model_dump(), device_key=key)


@router.get("/{screen_id}", response_model=ScreenOut)
def get_screen(
    screen_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    return screen_out(_get_screen(db, scope, screen_id, Role.viewer))


@router.patch("/{screen_id}", response_model=ScreenOut)
def update_screen(
    screen_id: int,
    body: ScreenUpdate,
    scope: AccessScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    screen = _get_screen(db, scope, screen_id, Role.editor)
    data = body.model_dump(exclude_unset=True)
    if "account_id" in data and data["account_id"] != screen.account_id:
        # Moving a screen between accounts needs editor rights on both sides.
        require(scope, data["account_id"], Role.editor)
    for field, value in data.items():
        if value is not None:
            setattr(screen, field, value)
    db.commit()
    return screen_out(screen)


@router.post("/{screen_id}/rotate-key", response_model=ScreenCreated)
def rotate_key(
    screen_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    screen = _get_screen(db, scope, screen_id, Role.editor)
    key, prefix, key_hash = generate_device_key()
    screen.key_prefix, screen.key_hash = prefix, key_hash
    db.commit()
    return ScreenCreated(**screen_out(screen).model_dump(), device_key=key)


@router.delete("/{screen_id}", status_code=204)
def delete_screen(
    screen_id: int, scope: AccessScope = Depends(get_scope), db: Session = Depends(get_db)
):
    screen = _get_screen(db, scope, screen_id, Role.admin)
    db.delete(screen)
    db.commit()
