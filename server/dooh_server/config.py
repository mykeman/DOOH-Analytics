"""Server settings, read from environment variables (prefix ``DOOH_``)."""

from __future__ import annotations

import os
import secrets
import warnings
from dataclasses import dataclass, field


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("DOOH_DATABASE_URL", "sqlite:///./dooh.db")
    )
    secret_key: str = field(default_factory=lambda: os.environ.get("DOOH_SECRET_KEY", ""))
    token_ttl_minutes: int = field(
        default_factory=lambda: int(os.environ.get("DOOH_TOKEN_TTL_MINUTES", "720"))
    )
    cors_origins: list[str] = field(default_factory=lambda: _env_list("DOOH_CORS_ORIGINS"))
    # A screen is reported "offline" when no heartbeat arrived within this window.
    offline_after_seconds: int = field(
        default_factory=lambda: int(os.environ.get("DOOH_OFFLINE_AFTER_SECONDS", "180"))
    )
    max_events_per_batch: int = 5000

    def __post_init__(self) -> None:
        if not self.secret_key:
            warnings.warn(
                "DOOH_SECRET_KEY is not set; using a random key. "
                "Login sessions will not survive a restart.",
                stacklevel=2,
            )
            self.secret_key = secrets.token_urlsafe(48)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
