"""SQLAlchemy 2 models for users, products and durable generation jobs."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB as PostgreSQLJSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym
from sqlalchemy.sql import func

from ai_core.schemas import (
    GenerationLifecycleStatus,
    ProductLockStatus as CanonicalProductLockStatus,
)

from app.database import Base


# Keep the production type JSONB while allowing the contract/migration tests
# to build an isolated SQLite schema without a PostgreSQL compiler.
JSONB = JSON().with_variant(PostgreSQLJSONB, "postgresql")


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


ProductLockRevisionStatus = CanonicalProductLockStatus


class GenerationAttemptOutcome(str, enum.Enum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class GenerationVariantStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    QUALITY_REVIEW = "quality_review"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class CostLedgerStatus(str, enum.Enum):
    RESERVED = "reserved"
    CHARGED = "charged"
    RELEASED = "released"
    UNKNOWN = "unknown"


# Explicit aliases make the V2 boundary discoverable without changing the V1
# ``GenerationStatus`` enum or its PostgreSQL type.
GenerationStatusV2 = GenerationLifecycleStatus
ProductLockStatus = ProductLockRevisionStatus


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
    product_lock_revisions: Mapped[list["ProductLockRevision"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
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
    original_exif_orientation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="products")
    generations: Mapped[list["Generation"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    lock_revisions: Mapped[list["ProductLockRevision"]] = relationship(
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
    platform_captions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
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

    # V2 fields are additive and nullable so a populated V1 database can be
    # upgraded without rewriting or reinterpreting historical jobs.
    v2_contract_version: Mapped[str | None] = mapped_column(String(20))
    lifecycle_status: Mapped[str | None] = mapped_column(String(40), index=True)
    product_lock_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_lock_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    request_v2: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_v2_hash: Mapped[str | None] = mapped_column(String(64))
    provider_selection: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider_execution_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider_execution_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    variant_count: Mapped[int | None] = mapped_column(SmallInteger)
    budget_authorized_micros: Mapped[int | None] = mapped_column(BigInteger)
    budget_reserved_micros: Mapped[int | None] = mapped_column(BigInteger)
    budget_charged_micros: Mapped[int | None] = mapped_column(BigInteger)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    unknown_remote_completion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cancellation_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    retryable_error: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_provider_error_code: Mapped[str | None] = mapped_column(String(80))
    # Semantic aliases keep adapters/tests independent from the physical
    # nullable-column names used to distinguish V2 from the legacy schema.
    contract_version = synonym("v2_contract_version")
    generation_request_v2 = synonym("request_v2")
    target_lock_revision_id = synonym("product_lock_revision_id")
    provider_usage = synonym("provider_execution_snapshot")
    v2_attempts: Mapped[list["GenerationAttempt"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )
    v2_variants: Mapped[list["GenerationVariant"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )
    cost_ledger: Mapped[list["ProviderCostLedger"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )
    product_lock_revision: Mapped["ProductLockRevision | None"] = relationship(
        back_populates="generations", foreign_keys=[product_lock_revision_id]
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


class ProductLockRevision(Base):
    """Immutable product-lock revision owned by one product and tenant.

    The service layer only creates rows and performs explicit state
    transitions.  There is no update endpoint for source bytes, prompts or
    provenance; refinement creates another row and supersedes the parent.
    """

    __tablename__ = "product_lock_revisions"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "revision", name="uq_product_lock_product_revision"
        ),
        CheckConstraint(
            "revision > 0", name="ck_product_lock_revision_positive"
        ),
        CheckConstraint(
            "length(source_sha256) = 64", name="ck_product_lock_source_hash_length"
        ),
        Index("ix_product_lock_owner_status", "owner_id", "status", "created_at"),
        Index("ix_product_lock_product_status", "product_id", "status", "revision"),
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
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProductLockRevisionStatus.PROCESSING.value
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    source_mime: Mapped[str] = mapped_column(String(50), nullable=False)
    source_width: Mapped[int] = mapped_column(Integer, nullable=False)
    source_height: Mapped[int] = mapped_column(Integer, nullable=False)
    source_exif_orientation: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_lock_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    target_geometry: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mask_storage_key: Mapped[str | None] = mapped_column(String(500))
    mask_sha256: Mapped[str | None] = mapped_column(String(64))
    cutout_storage_key: Mapped[str | None] = mapped_column(String(500))
    cutout_sha256: Mapped[str | None] = mapped_column(String(64))
    prompts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    scene_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    model_provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped["Product"] = relationship(
        back_populates="lock_revisions", foreign_keys=[product_id]
    )
    owner: Mapped["User"] = relationship(
        back_populates="product_lock_revisions", foreign_keys=[owner_id]
    )
    parent_revision: Mapped["ProductLockRevision | None"] = relationship(
        remote_side=[id], foreign_keys=[parent_revision_id]
    )
    generations: Mapped[list["Generation"]] = relationship(
        back_populates="product_lock_revision",
        foreign_keys="Generation.product_lock_revision_id",
    )


class GenerationAttempt(Base):
    """Durable provider attempt receipt, including uncertain completion."""

    __tablename__ = "generation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "attempt", name="uq_generation_attempt_number"
        ),
        Index("ix_generation_attempt_provider_request", "provider_request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False, default=GenerationAttemptOutcome.ACCEPTED.value
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    generation: Mapped["Generation"] = relationship(back_populates="v2_attempts")


class GenerationVariant(Base):
    """One immutable V2 visual variant and its QA/manifest receipt."""

    __tablename__ = "generation_variants"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "variant_index", name="uq_generation_variant_index"
        ),
        Index("ix_generation_variant_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=GenerationVariantStatus.PENDING.value
    )
    artifact_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    qa_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    checksum: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    generation: Mapped["Generation"] = relationship(back_populates="v2_variants")


class ProviderCostLedger(Base):
    """Idempotent reservations/charges for a hard cumulative budget."""

    __tablename__ = "provider_cost_ledger"
    __table_args__ = (
        UniqueConstraint("ledger_key", name="uq_provider_cost_ledger_key"),
        Index("ix_provider_cost_generation", "generation_id", "attempt"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger_key: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=CostLedgerStatus.RESERVED.value
    )
    authorized_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    charged_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    usage_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    generation: Mapped["Generation"] = relationship(back_populates="cost_ledger")
