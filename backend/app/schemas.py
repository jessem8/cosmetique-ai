"""Strict browser-facing schemas for the versioned API."""
from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictFloat,
    field_validator,
    model_validator,
)

from app.models import (
    AssetFormat,
    CampaignLanguage,
    GenerationStage,
    GenerationStatus,
)
from ai_core.schemas import (
    BudgetEnvelope,
    GenerationManifestV2,
    GenerationRequestV2,
    NormalizedBox,
    ProductLockPrompts,
    ProductLockRevisionCreate,
    ProductLockStatus,
    ProviderCapabilities,
    ProviderSelection,
    SceneSpec,
    SourceImageBinding,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_bcrypt_password_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Le mot de passe ne doit pas dépasser 72 octets UTF-8.")
    return value


class UserCreate(StrictModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        _validate_bcrypt_password_bytes(value)
        if not any(character.isupper() for character in value):
            raise ValueError("Le mot de passe doit contenir au moins une majuscule.")
        if not any(character.isdigit() for character in value):
            raise ValueError("Le mot de passe doit contenir au moins un chiffre.")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailStr
    is_active: bool
    created_at: datetime


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        return _validate_bcrypt_password_bytes(value)


class Token(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class TokenData(StrictModel):
    user_id: uuid.UUID | None = None


class ProductOut(StrictModel):
    id: uuid.UUID
    name: str
    brand: str | None
    category: str
    image_url: str
    source_sha256: str | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_exif_orientation: int | None = None
    created_at: datetime


class ProductList(StrictModel):
    items: list[ProductOut]
    total: int = Field(ge=0)


class ProductSummary(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=100)
    category: str = Field(min_length=1, max_length=80)


class TargetBox(StrictModel):
    type: Literal["box"]
    x: StrictFloat
    y: StrictFloat
    width: StrictFloat
    height: StrictFloat

    @model_validator(mode="after")
    def box_is_normalized(self) -> "TargetBox":
        values = (self.x, self.y, self.width, self.height)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Les coordonnées doivent être finies.")
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("La boîte doit être positive et normalisée.")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("La boîte doit rester dans l'image.")
        return self


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class GenerationCreate(StrictModel):
    language: CampaignLanguage
    seed: int = Field(default=42, strict=True, ge=0, le=4_294_967_295)
    audience: str | None = Field(default=None, max_length=300)
    benefits: list[str] = Field(default_factory=list, max_length=12)
    ingredients: list[str] = Field(default_factory=list, max_length=30)
    verified_claims: list[str] = Field(default_factory=list, max_length=20)
    cta: str | None = Field(default=None, max_length=80)
    creative_direction: str | None = Field(default=None, max_length=500)
    target_hint: TargetBox | None = None
    source_generation_id: uuid.UUID | None = None

    @field_validator("audience", "cta", "creative_direction")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("benefits", "ingredients", "verified_claims")
    @classmethod
    def clean_text_lists(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or len(item) > 240:
                raise ValueError("Chaque élément doit contenir entre 1 et 240 caractères.")
            normalized = item.casefold()
            if normalized not in seen:
                seen.add(normalized)
                result.append(item)
        return result


class ErrorOut(StrictModel):
    code: str
    message: str


class CandidateBox(TargetBox):
    id: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0, le=1)


class AmbiguityOut(StrictModel):
    original_url: str
    candidates: list[CandidateBox] = Field(min_length=2, max_length=20)


class ArtifactOut(StrictModel):
    name: str
    url: str
    mime: str
    sha256: str
    bytes: int = Field(ge=1)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    format: AssetFormat | None = None


class GenerationOut(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: uuid.UUID
    product_id: uuid.UUID
    product: ProductSummary
    source_generation_id: uuid.UUID | None
    status: GenerationStatus
    stage: GenerationStage | None
    completed_stages: list[GenerationStage]
    language: CampaignLanguage
    seed: int
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    error: ErrorOut | None = None
    ambiguity: AmbiguityOut | None = None
    copy_data: dict[str, Any] | None = Field(default=None, alias="copy")
    artifacts: list[ArtifactOut] | None = None


class GenerationHistory(StrictModel):
    items: list[GenerationOut]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Campaign Studio V2 contracts.  These are API-facing projections of the
# canonical ai_core models.  Private storage keys and provider credentials are
# intentionally absent from every response model.


class ProductLockRevisionOut(StrictModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: uuid.UUID
    product_id: uuid.UUID
    revision: int = Field(ge=1)
    status: ProductLockStatus
    source: SourceImageBinding
    parent_revision_id: uuid.UUID | None = None
    prompts: ProductLockPrompts
    scene: SceneSpec | None = None
    target_box: NormalizedBox | None = None
    source_image_url: str | None = None
    mask_url: str | None = None
    cutout_url: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    model_provenance: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class ProductLockAction(StrictModel):
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return _clean_text(value)


class ProductLockRevisionList(StrictModel):
    items: list[ProductLockRevisionOut]
    total: int = Field(ge=0)


class ProductLockRootCreate(ProductLockRevisionCreate):
    """Compatibility body for the root Studio V2 endpoint.

    The canonical endpoint keeps ``product_id`` in the URL; this projection
    is only for clients mounted below ``/studio/v2``.
    """

    product_id: uuid.UUID


class ProviderProfileOut(StrictModel):
    """Safe capability projection; no host, token, or secret is returned."""

    selection: ProviderSelection
    capabilities: ProviderCapabilities
    id: str | None = None
    name: str | None = None
    model: str | None = None
    description: str | None = None
    capability_flags: list[str] = Field(default_factory=list)


class GenerationV2Out(StrictModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_lock_revision_id: uuid.UUID
    contract_version: str
    lifecycle_status: str
    # ``status`` is a compatibility projection for the Studio V2 browser;
    # lifecycle_status remains the canonical persisted field.
    status: str | None = None
    request_hash: str
    request: GenerationRequestV2
    provider: ProviderSelection
    provider_execution_plan: dict[str, Any] | None = None
    variant_count: int = Field(ge=1, le=32)
    attempt_count: int = Field(ge=0)
    provider_request_id: str | None = None
    unknown_remote_completion: bool = False
    cancellation_requested: bool = False
    budget_authorized_micros: int = Field(ge=0)
    budget_reserved_micros: int = Field(ge=0)
    budget_charged_micros: int = Field(ge=0)
    variants: list[dict[str, Any]] = Field(default_factory=list)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    error: ErrorOut | None = None


class GenerationV2History(StrictModel):
    items: list[GenerationV2Out]
    next_cursor: str | None = None


class GenerationV2CancelOut(StrictModel):
    id: uuid.UUID
    lifecycle_status: str
    cancellation_requested: bool


# Semantic aliases for integrations that use the shorter V2 naming.
GenerationCreateV2 = GenerationRequestV2
GenerationResponseV2 = GenerationV2Out
ProductLockCreate = ProductLockRevisionCreate
