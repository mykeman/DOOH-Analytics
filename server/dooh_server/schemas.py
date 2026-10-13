from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Role

AGE_BUCKETS = ["0-12", "13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
GENDERS = ["male", "female"]


def _check_tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc
    return value


def to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


# --- auth / users -----------------------------------------------------------


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


class MembershipOut(BaseModel):
    account_id: int
    account_name: str
    role: Role


class MeOut(BaseModel):
    id: int
    email: str
    name: str
    is_superadmin: bool
    memberships: list[MembershipOut]


class MemberOut(BaseModel):
    user_id: int
    email: str
    name: str
    role: Role
    is_active: bool


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MemberIn(BaseModel):
    email: str
    name: str = ""
    role: Role = Role.viewer
    # Required only when the user does not exist yet.
    password: str | None = Field(default=None, min_length=10)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v


class MemberUpdate(BaseModel):
    role: Role


# --- accounts ---------------------------------------------------------------


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None


class AccountUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AccountOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    path: str
    role: Role
    screen_count: int


# --- screens ----------------------------------------------------------------


class ScreenIn(BaseModel):
    account_id: int
    name: str = Field(min_length=1, max_length=200)
    location: str = ""
    timezone: str = "UTC"
    agent_settings: dict = Field(default_factory=dict)

    _tz = field_validator("timezone")(_check_tz)


class ScreenUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = None
    timezone: str | None = None
    is_active: bool | None = None
    account_id: int | None = None
    agent_settings: dict | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return None if v is None else _check_tz(v)


class ScreenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    name: str
    location: str
    timezone: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_seen_at: datetime | None
    agent_version: str | None
    last_status: dict | None
    agent_settings: dict
    online: bool = False


class ScreenCreated(ScreenOut):
    device_key: str


# --- brands -----------------------------------------------------------------


class HsvRange(BaseModel):
    lo: tuple[int, int, int]
    hi: tuple[int, int, int]


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    colors: list[HsvRange] = Field(default_factory=list)


class BrandTemplateOut(BaseModel):
    id: int
    filename: str
    sha256: str


class BrandOut(BaseModel):
    id: int
    account_id: int
    name: str
    colors: list[HsvRange]
    templates: list[BrandTemplateOut]
    inherited: bool = False


# --- ingest (edge -> server) ------------------------------------------------


class EventIn(BaseModel):
    event_id: str = Field(min_length=8, max_length=36)
    started_at: datetime
    ended_at: datetime
    dwell_seconds: float = Field(ge=0, le=24 * 3600)
    attention_seconds: float = Field(default=0, ge=0, le=24 * 3600)
    viewed: bool = False
    age_bucket: str | None = None
    gender: str | None = None
    has_bag: bool = False
    brand: str | None = Field(default=None, max_length=120)

    @field_validator("age_bucket")
    @classmethod
    def _age(cls, v: str | None) -> str | None:
        if v is not None and v not in AGE_BUCKETS:
            raise ValueError(f"age_bucket must be one of {AGE_BUCKETS}")
        return v

    @field_validator("gender")
    @classmethod
    def _gender(cls, v: str | None) -> str | None:
        if v is not None and v not in GENDERS:
            raise ValueError(f"gender must be one of {GENDERS}")
        return v


class EventBatchIn(BaseModel):
    events: list[EventIn]


class IngestResult(BaseModel):
    accepted: int
    duplicates: int


class HeartbeatIn(BaseModel):
    agent_version: str | None = None
    status: dict = Field(default_factory=dict)


class DeviceBrand(BaseModel):
    name: str
    colors: list[HsvRange]
    templates: list[dict]  # {"id", "sha256", "url"}


class DeviceConfigOut(BaseModel):
    screen_id: int
    screen_name: str
    timezone: str
    settings: dict
    brands: list[DeviceBrand]
