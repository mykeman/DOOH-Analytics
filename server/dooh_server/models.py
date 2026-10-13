"""Database models.

All timestamps are stored as naive UTC datetimes so behaviour is identical on
SQLite and PostgreSQL.

Tenancy model
-------------
``Account`` rows form a tree. A top-level account is typically the network
operator / agency; its sub-accounts are clients (and may be nested further,
e.g. client -> region). A ``Membership`` grants a user a role on one account
and, implicitly, on every descendant of that account - never on its parent or
siblings. Screens, brands and audience events belong to exactly one account.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, enum.Enum):
    viewer = "viewer"  # read analytics
    editor = "editor"  # + manage screens and brands
    admin = "admin"  # + manage users and sub-accounts

    @property
    def rank(self) -> int:
        return {"viewer": 1, "editor": 2, "admin": 3}[self.value]


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    parent: Mapped[Account | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Account]] = relationship(back_populates="parent")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "account_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default=Role.viewer.value)

    user: Mapped[User] = relationship(back_populates="memberships")
    account: Mapped[Account] = relationship()


class Screen(Base):
    __tablename__ = "screens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(300), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # Device credentials: the key is shown once at creation; only a hash is kept.
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_status: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Settings pushed to the edge agent (thresholds etc.). Merged over agent defaults.
    agent_settings: Mapped[dict] = mapped_column(JSON, default=dict)

    account: Mapped[Account] = relationship()


class Brand(Base):
    """A retail brand whose shopping bags should be recognised.

    Brands are inherited down the account tree: a brand defined on an agency
    account is detected on every screen of every client below it.
    """

    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("account_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    # Optional dominant bag colours as HSV ranges: [{"lo": [h,s,v], "hi": [h,s,v]}]
    colors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    templates: Mapped[list[BrandTemplate]] = relationship(
        back_populates="brand", cascade="all, delete-orphan"
    )


class BrandTemplate(Base):
    """A logo image used for feature matching on the edge."""

    __tablename__ = "brand_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    brand: Mapped[Brand] = relationship(back_populates="templates")


class AudienceEvent(Base):
    """One anonymous person's pass in front of a screen.

    No images, face embeddings or identifiers that could re-identify a person
    are stored - only the aggregated observation.
    """

    __tablename__ = "audience_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_uuid: Mapped[str] = mapped_column(String(36), unique=True)
    screen_id: Mapped[int] = mapped_column(
        ForeignKey("screens.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    dwell_seconds: Mapped[float] = mapped_column(Float)
    attention_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    viewed: Mapped[bool] = mapped_column(Boolean, default=False)
    age_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    has_bag: Mapped[bool] = mapped_column(Boolean, default=False)
    brand: Mapped[str | None] = mapped_column(String(120), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
