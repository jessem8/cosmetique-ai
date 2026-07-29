"""SQLAlchemy 2 models for users, products and durable generation jobs."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class CampaignLanguage(str, enum.Enum):
    FR = "fr"
    EN = "en"


class GenerationStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


class GenerationStage(str, enum.Enum):
    ANALYSIS = "analysis"
    EXTRACTION = "extraction"
    ART_DIRECTION = "art_direction"
    BACKGROUND = "background"
    COMPOSITION = "composition"
    COPY = "copy"
    EXPORT = "export"
    PACKAGING = "packaging"


class AssetFormat(str, enum.Enum):
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="users_email_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    products: Mapped[list["Product"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    original_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    original_mime: Mapped[str] = mapped_column(String(50), nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_width: Mapped[int] = mapped_column(Integer, nullable=False)
    original_height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="products")
    generations: Mapped[list["Generation"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class Generation(Base):
    __tablename__ = "generations"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "idempotency_key", name="uq_generation_product_idempotency"
        ),
        Index("ix_generations_queue", "status", "next_attempt_at", "created_at"),
        Index("ix_generations_cursor", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_generation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generations.id", ondelete="SET NULL")
    )

    language: Mapped[CampaignLanguage] = mapped_column(
        SAEnum(
            CampaignLanguage,
            name="campaignlanguage",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    status: Mapped[GenerationStatus] = mapped_column(
        SAEnum(
            GenerationStatus,
            name="generationstatus",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=GenerationStatus.PENDING,
        nullable=False,
    )
    stage: Mapped[GenerationStage | None] = mapped_column(
        SAEnum(
            GenerationStage,
            name="generationstage",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    completed_stages: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_job_id: Mapped[str | None] = mapped_column(String(200))
    runtime_id: Mapped[str | None] = mapped_column(String(200))

    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    candidate_boxes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    copy_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    artifact_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    bundle_storage_key: Mapped[str | None] = mapped_column(String(500))
    bundle_checksum: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    product: Mapped["Product"] = relationship(back_populates="generations")
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "artifact_name", name="uq_asset_generation_name"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_name: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[AssetFormat | None] = mapped_column(
        SAEnum(
            AssetFormat,
            name="assetformat",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    mime: Mapped[str] = mapped_column(String(80), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    generation: Mapped["Generation"] = relationship(back_populates="assets")
