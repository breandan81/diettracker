"""ORM models for multi-user τrend."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # Photo upload/analyze/Imagine — invite-only until monetized / moderation exists
    photos_allowed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    settings: Mapped[list["UserSetting"]] = relationship(back_populates="user")
    weights: Mapped[list["Weight"]] = relationship(back_populates="user")
    photos: Mapped[list["Photo"]] = relationship(back_populates="user")
    ingest_tokens: Mapped[list["IngestToken"]] = relationship(back_populates="user")


class UserSetting(Base):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_setting"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="settings")


class Weight(Base):
    __tablename__ = "weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(32))  # YYYY-MM-DD
    logged_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weight: Mapped[float] = mapped_column(Float)
    body_fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="weights")


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    bmi_point: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    appearance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    appearance_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_overall: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    projection_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    projection_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    projection_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    projection_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    projection_goal_lb: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_created_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="photos")


class IngestToken(Base):
    __tablename__ = "ingest_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    user: Mapped[User] = relationship(back_populates="ingest_tokens")


class AiUsageDaily(Base):
    __tablename__ = "ai_usage_daily"
    __table_args__ = (
        UniqueConstraint("user_id", "day", "kind", name="uq_ai_usage_day_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(32))  # coach | vision | imagine
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class AppConfig(Base):
    """Admin-tunable key/value overrides (quotas, flags)."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class PhotoInvite(Base):
    """One-time (or multi-use) codes that grant photos_allowed."""

    __tablename__ = "photo_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    code_prefix: Mapped[str] = mapped_column(String(16), default="")  # for admin display
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
