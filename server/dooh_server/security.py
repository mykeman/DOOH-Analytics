from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import get_settings

DEVICE_KEY_PREFIX = "dooh"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.token_ttl_minutes),
        "typ": "user",
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "user":
        return None
    try:
        return int(payload["sub"])
    except (KeyError, ValueError):
        return None


def _hash_device_secret(secret: str) -> str:
    # Device secrets are 256-bit random values, so a fast hash is sufficient (bcrypt
    # would add latency to every ingest request for no security gain). It is not
    # keyed with DOOH_SECRET_KEY so that rotating that key does not unenroll devices.
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_device_key() -> tuple[str, str, str]:
    """Return ``(full_key, prefix, hash)``. Only the prefix and hash are stored."""
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return f"{DEVICE_KEY_PREFIX}_{prefix}_{secret}", prefix, _hash_device_secret(secret)


def parse_device_key(key: str) -> tuple[str, str] | None:
    parts = key.split("_", 2)
    if len(parts) != 3 or parts[0] != DEVICE_KEY_PREFIX:
        return None
    return parts[1], parts[2]


def verify_device_secret(secret: str, key_hash: str) -> bool:
    return hmac.compare_digest(_hash_device_secret(secret), key_hash)
