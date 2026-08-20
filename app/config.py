"""Environment settings for τrend multi-user."""

from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / "secrets.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "τrend"
    host: str = "0.0.0.0"
    port: int = 8511
    # SQLite for local bootstrap; set DATABASE_URL=postgresql+psycopg://... for Postgres
    database_url: str = f"sqlite:///{ROOT / 'data' / 'trend_multi.db'}"
    session_secret: str = "dev-change-me"
    admin_user_ids: str = ""  # comma-separated ints

    xai_api_key: str = ""
    xai_model: str = "grok-4.6"
    xai_imagine_model: str = "grok-imagine-image-2.0"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8511/api/auth/google/callback"

    # Public site URL for email verification links (no trailing slash)
    public_base_url: str = "http://127.0.0.1:8511"

    # SMTP — required for email/password registration (fail closed if unset)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False  # use SMTPS (port 465) instead of STARTTLS

    daily_limit_coach: int = 20
    daily_limit_vision: int = 10
    daily_limit_imagine: int = 5

    data_dir: Path = ROOT / "data"
    public_dir: Path = ROOT / "public"

    @property
    def admin_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.admin_user_ids.split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
