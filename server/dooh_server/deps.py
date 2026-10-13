from __future__ import annotations

from datetime import timedelta

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Role, Screen, User, utcnow
from .security import decode_access_token, parse_device_key, verify_device_secret
from .tenancy import AccessScope

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise unauthorized
    user_id = decode_access_token(token)
    if user_id is None:
        raise unauthorized
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def get_scope(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AccessScope:
    return AccessScope(db, user)


def require(scope: AccessScope, account_id: int, minimum: Role) -> None:
    """Raise 404 for accounts the user cannot see at all, 403 for insufficient role."""
    if account_id not in scope.account_ids:
        raise HTTPException(status_code=404, detail="Account not found")
    if not scope.can(account_id, minimum):
        raise HTTPException(status_code=403, detail=f"Requires {minimum.value} role")


def get_device_screen(
    x_device_key: str | None = Header(default=None), db: Session = Depends(get_db)
) -> Screen:
    unauthorized = HTTPException(status_code=401, detail="Invalid device key")
    if not x_device_key:
        raise unauthorized
    parsed = parse_device_key(x_device_key)
    if parsed is None:
        raise unauthorized
    prefix, secret = parsed
    screen = db.scalar(select(Screen).where(Screen.key_prefix == prefix))
    if screen is None or not verify_device_secret(secret, screen.key_hash):
        raise unauthorized
    if not screen.is_active:
        raise HTTPException(status_code=403, detail="Screen is disabled")
    now = utcnow()
    # Avoid a write on every request; a minute of resolution is plenty.
    if screen.last_seen_at is None or now - screen.last_seen_at > timedelta(seconds=30):
        screen.last_seen_at = now
        db.commit()
    return screen
