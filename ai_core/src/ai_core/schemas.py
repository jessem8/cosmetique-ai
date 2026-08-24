"""The canonical, model-free contracts shared by the V1 and V2 services.

The package deliberately contains validation only.  It must be safe to import
from the website, the queue worker and the Colab service without importing an
inference framework or touching a provider.  V1 models remain unchanged in
shape; the V2 models below are additive and intentionally strict.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class FrozenStrictModel(StrictModel):
    """Strict contract object that cannot be changed after validation."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True, populate_by_name=True
    )


class CampaignLanguage(str, Enum):
    FR = 'fr'
    EN = 'en'


class NormalizedBox(StrictModel):
    type: Literal['box'] = 'box'
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode='after')
    def stays_inside_image(self) -> 'NormalizedBox':
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError('box must stay inside the normalized image')
        return self


class NormalizedPoint(StrictModel):
    """A point in the EXIF-normalized source-image coordinate system."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def finite(self) -> "NormalizedPoint":
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("normalized coordinates must be finite")
        return self


class NormalizedPolygon(StrictModel):
    """A bounded polygon used by a product-lock mask or QA receipt."""

    points: list[NormalizedPoint] = Field(min_length=3, max_length=512)


class SourceImageBinding(FrozenStrictModel):
    """The exact source bytes and orientation used for a product lock.

    ``sha256`` is owned by the website/database and is never accepted from a
    provider.  Width and height describe the EXIF-corrected image, so every
    normalized coordinate has one unambiguous frame of reference.
    """

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime: Literal["image/jpeg", "image/png", "image/webp"]
    width: int = Field(gt=0, le=20_000)
    height: int = Field(gt=0, le=20_000)
    exif_orientation: int = Field(default=1, ge=1, le=8)

    @field_validator("sha256")
    @classmethod
    def lowercase_hash(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def pixel_cap(self) -> "SourceImageBinding":
        if self.width * self.height > 100_000_000:
            raise ValueError("source image exceeds the hard pixel cap")
        return self


class ProductLockStatus(str, Enum):
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    VALIDATED = "validated"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class GenerationLifecycleStatus(str, Enum):
    """Durable provider-neutral V2 job lifecycle."""

    ACCEPTED = "accepted"
    QUEUED = "queued"
    WAITING_FOR_PROVIDER = "waiting_for_provider"
    PROCESSING_LOCK = "processing_lock"
    PLANNING_SCENE = "planning_scene"
    GENERATING = "generating"
    COMPOSITING = "compositing"
    QUALITY_REVIEW = "quality_review"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProductLockPrompts(StrictModel):
    """Human-authored, evidence-bound prompts for a lock revision."""

    audience: str | None = Field(default=None, max_length=300)
    benefits: list[str] = Field(default_factory=list, max_length=12)
    ingredients: list[str] = Field(default_factory=list, max_length=30)
    verified_claims: list[str] = Field(default_factory=list, max_length=20)
    cta: str | None = Field(default=None, max_length=80)
    creative_direction: str | None = Field(default=None, max_length=500)
    scene_prompt: str | None = Field(default=None, max_length=1_000)

    @field_validator("audience", "cta", "creative_direction", "scene_prompt")
    @classmethod
    def clean_optional_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("benefits", "ingredients", "verified_claims")
    @classmethod
    def clean_prompt_lists(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or len(item) > 240:
                raise ValueError("prompt values must be bounded and non-empty")
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                output.append(item)
        return output


class SceneSpec(FrozenStrictModel):
    """A provider-neutral description of the requested scene composition."""

    scene_prompt: str | None = Field(default=None, max_length=1_000)
    prompt: str | None = Field(default=None, max_length=1_000)
    negative_prompt: str | None = Field(default=None, max_length=1_000)
    target_box: NormalizedBox | None = None
    target_point: NormalizedPoint | None = None
    placement: Literal["center", "left", "right", "top", "bottom", "custom"] = "center"
    preserve_product_pixels: bool = True

    @field_validator("scene_prompt", "negative_prompt")
    @classmethod
    def clean_scene_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("scene prompt must not be blank")
        return value

    @model_validator(mode="after")
    def require_scene_prompt(self) -> "SceneSpec":
        if not self.scene_prompt and not self.prompt:
            raise ValueError("scene prompt must not be blank")
        if self.scene_prompt and self.prompt and self.scene_prompt != self.prompt:
            raise ValueError("scene_prompt and prompt must match")
        return self


class ProviderSelection(FrozenStrictModel):
    """An opaque allowlisted provider profile, never a browser-supplied URL.

    The backend applies the tenant/server allowlist in addition to this
    provider-neutral validation.  In particular this model has no URL, token,
    API-key or arbitrary header field by design.
    """

    provider: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        validation_alias=AliasChoices("provider", "provider_id", "provider_name"),
    )
    profile: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
        validation_alias=AliasChoices("profile", "profile_id", "provider_profile"),
    )
    model: str | None = Field(
        default=None,
        max_length=120,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:/@-]*$",
        validation_alias=AliasChoices("model", "model_id"),
    )

    @model_validator(mode="after")
    def reject_endpoint_like_values(self) -> "ProviderSelection":
        for value in (self.provider, self.profile, self.model):
            if value and ("://" in value or "\\" in value or "?" in value):
                raise ValueError("provider selection must reference an allowlisted profile")
        return self


class ProviderCapabilities(FrozenStrictModel):
    """Safe capability metadata exposed by the server for a provider profile."""

    provider: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    profile: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    supports_async: bool = True
    supports_cancellation: bool = False
    supports_unknown_completion_reconciliation: bool = True
    supports_product_lock: bool = True
    supports_mask_preservation: bool = True
    supports_scene_generation: bool = True
    supports_custom_direction: bool = True
    supports_qa_provenance: bool = True
    max_variants: int = Field(default=4, ge=1, le=32)
    max_budget_micros: int = Field(default=0, ge=0, le=10_000_000_000)
    estimated_cost_per_variant_micros: int = Field(default=0, ge=0, le=10_000_000_000)
    pipeline_version: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=500)


class BudgetEnvelope(FrozenStrictModel):
    """Hard cumulative authorization envelope in integer micro-units."""

    max_cost_micros: int = Field(ge=0, le=10_000_000_000)
    max_attempts: int = Field(default=2, ge=1, le=8)

    @model_validator(mode="before")
    @classmethod
    def accept_bare_cost_for_api_compatibility(cls, value: Any):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return {"max_cost_micros": value}
        return value


class ProductLockRevisionCreate(StrictModel):
    """Browser input for creating/refining a lock revision."""

    source_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
        validation_alias=AliasChoices("source_sha256", "source_hash"),
    )
    source_orientation: int = Field(
        default=1,
        ge=1,
        le=8,
        validation_alias=AliasChoices("source_orientation", "exif_orientation"),
    )
    target_box: NormalizedBox | None = None
    target_hint: NormalizedBox | None = None
    mask_polygon: NormalizedPolygon | None = None
    # Refinement points are kept in the immutable request snapshot.  They are
    # deliberately separate from ``mask_polygon``: points are guidance for an
    # installed segmentation adapter (or its explicit CPU fallback), never a
    # browser-supplied mask or an assertion that a model ran.
    positive_points: list[NormalizedPoint] = Field(default_factory=list, max_length=64)
    negative_points: list[NormalizedPoint] = Field(default_factory=list, max_length=64)
    prompts: ProductLockPrompts = Field(default_factory=ProductLockPrompts)
    scene: SceneSpec | None = None

    @field_validator("source_sha256")
    @classmethod
    def normalize_hash(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @model_validator(mode="after")
    def one_target_alias(self) -> "ProductLockRevisionCreate":
        if self.target_box is not None and self.target_hint is not None:
            raise ValueError("provide only one target box")
        if len(self.positive_points) + len(self.negative_points) > 64:
            raise ValueError("provide at most 64 correction points")
        return self

    @property
    def normalized_target(self) -> NormalizedBox | None:
        return self.target_box or self.target_hint


class ProductLockRevision(FrozenStrictModel):
    """Public immutable lock snapshot; storage keys are intentionally absent."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True, protected_namespaces=()
    )

    id: uuid.UUID
    product_id: uuid.UUID
    revision: int = Field(ge=1)
    status: ProductLockStatus
    source: SourceImageBinding
    parent_revision_id: uuid.UUID | None = None
    prompts: ProductLockPrompts
    scene: SceneSpec | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    model_provenance: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None


class GenerationRequestV2(FrozenStrictModel):
    """Immutable browser-to-worker V2 generation request snapshot."""

    contract_version: Literal["2.0.0", "2.1.0"] = "2.0.0"
    product_lock_revision_id: uuid.UUID = Field(
        validation_alias=AliasChoices(
            "product_lock_revision_id",
            "product_lock_id",
            "lock_revision_id",
            "target_revision_id",
        )
    )
    language: CampaignLanguage
    seed: int = Field(default=42, ge=0, le=4_294_967_295)
    audience: str | None = Field(default=None, max_length=300)
    benefits: list[str] = Field(default_factory=list, max_length=12)
    ingredients: list[str] = Field(default_factory=list, max_length=30)
    verified_claims: list[str] = Field(default_factory=list, max_length=20)
    cta: str | None = Field(default=None, max_length=80)
    creative_direction: str | None = Field(default=None, max_length=500)
    scene_prompt: str | None = Field(default=None, max_length=1_000)
    target_box: NormalizedBox | None = None
    target_hint: NormalizedBox | None = None
    scene: SceneSpec | None = None
    provider: ProviderSelection
    variant_count: int = Field(default=1, ge=1, le=32)
    budget: BudgetEnvelope

    @field_validator("audience", "cta", "creative_direction", "scene_prompt")
    @classmethod
    def clean_generation_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("benefits", "ingredients", "verified_claims")
    @classmethod
    def clean_generation_lists(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or len(item) > 240:
                raise ValueError("prompt values must be bounded and non-empty")
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                output.append(item)
        return output

    @model_validator(mode="after")
    def normalize_and_bound_request(self) -> "GenerationRequestV2":
        if self.target_box is not None and self.target_hint is not None:
            raise ValueError("provide only one target box")
        if self.scene is not None and self.scene.target_box is not None:
            target = self.target_box or self.target_hint
            if target is not None and target != self.scene.target_box:
                raise ValueError("scene and request target boxes must match")
        if self.variant_count > self.budget.max_attempts * 32:
            raise ValueError("variant count exceeds the bounded request envelope")
        return self


class ProviderExecutionPlan(FrozenStrictModel):
    """Worker snapshot of allowlisted provider capabilities and request hash."""

    selection: ProviderSelection
    capabilities: ProviderCapabilities
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_id: str | None = Field(default=None, max_length=200)
    timeout_seconds: int = Field(default=1_800, ge=1, le=7_200)


class AttemptProvenance(FrozenStrictModel):
    attempt: int = Field(ge=1, le=32)
    provider_request_id: str | None = Field(default=None, max_length=200)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    outcome: Literal["accepted", "processing", "succeeded", "failed", "unknown", "cancelled"]
    error_code: str | None = Field(default=None, max_length=80)
    retryable: bool = False


class CostProvenance(FrozenStrictModel):
    currency: Literal["USD", "EUR", "MICRO_USD"] = "MICRO_USD"
    estimated_micros: int = Field(ge=0, le=10_000_000_000)
    authorized_micros: int = Field(ge=0, le=10_000_000_000)
    charged_micros: int = Field(default=0, ge=0, le=10_000_000_000)
    ledger_key: str = Field(min_length=1, max_length=200)
    charge_final: bool = False

    @model_validator(mode="after")
    def within_authorization(self) -> "CostProvenance":
        if self.charged_micros > self.authorized_micros:
            raise ValueError("provider charge exceeds the authorization envelope")
        return self


class VariantQAManifest(FrozenStrictModel):
    passed: bool
    score: float = Field(ge=0, le=1)
    checks: dict[str, bool] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    reviewed_at: datetime | None = None


class GenerationVariantManifest(FrozenStrictModel):
    variant_index: int = Field(ge=0, le=31)
    artifact_name: str = Field(min_length=1, max_length=200)
    storage_key: str | None = Field(default=None, max_length=500)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mime: str = Field(min_length=1, max_length=100)
    byte_size: int = Field(ge=0, le=100_000_000)
    qa: VariantQAManifest

    @model_validator(mode="after")
    def safe_artifact_names(self) -> "GenerationVariantManifest":
        for value in (self.artifact_name, self.storage_key):
            if value and (
                value.startswith(("/", "\\"))
                or ".." in value.replace("\\", "/").split("/")
                or "\\" in value
            ):
                raise ValueError("variant artifact paths must stay inside private storage")
        return self


class GenerationManifestV2(FrozenStrictModel):
    schema_version: Literal["2.0.0", "2.1.0"] = "2.0.0"
    generation_id: uuid.UUID
    product_lock_revision_id: uuid.UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: ProviderSelection
    variants: list[GenerationVariantManifest] = Field(min_length=1, max_length=32)
    attempts: list[AttemptProvenance] = Field(default_factory=list, max_length=32)
    cost: CostProvenance | None = None

    @model_validator(mode="after")
    def variant_indices_unique(self) -> "GenerationManifestV2":
        indices = [variant.variant_index for variant in self.variants]
        if len(indices) != len(set(indices)):
            raise ValueError("variant indices must be unique")
        return self


class CandidateBox(NormalizedBox):
    id: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0, le=1)


class ProductSnapshot(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    original_mime: Literal['image/jpeg', 'image/png', 'image/webp']
    original_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    original_width: int = Field(gt=0, le=20_000)
    original_height: int = Field(gt=0, le=20_000)

    @model_validator(mode='after')
    def enforce_hard_pixel_cap(self) -> 'ProductSnapshot':
        if self.original_width * self.original_height > 100_000_000:
            raise ValueError('source image exceeds the hard pixel cap')
        return self


class GenerationRequest(StrictModel):
    language: CampaignLanguage
    seed: int = Field(ge=0, le=4_294_967_295)
    audience: str | None = Field(default=None, max_length=300)
    benefits: list[str] = Field(default_factory=list, max_length=12)
    ingredients: list[str] = Field(default_factory=list, max_length=30)
    verified_claims: list[str] = Field(default_factory=list, max_length=20)
    cta: str | None = Field(default=None, max_length=80)
    creative_direction: str | None = Field(default=None, max_length=500)
    target_hint: NormalizedBox | None = None
    source_generation_id: str | None = Field(default=None, max_length=100)

    @field_validator('audience', 'cta', 'creative_direction', 'source_generation_id')
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator('benefits', 'ingredients', 'verified_claims')
    @classmethod
    def clean_text_lists(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or len(item) > 240:
                raise ValueError('generation text values must be bounded and non-empty')
            if item.casefold() not in seen:
                seen.add(item.casefold())
                result.append(item)
        return result


class PipelineSnapshot(StrictModel):
    product: ProductSnapshot
    generation: GenerationRequest


class PipelineSnapshotV2(FrozenStrictModel):
    """Complete immutable payload persisted before V2 provider submission."""

    product: ProductSnapshot
    source: SourceImageBinding
    generation: GenerationRequestV2
    lock_revision_id: uuid.UUID
    input_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


# Stable semantic aliases used by integrations that call the same objects by
# their shorter domain names.  They intentionally point to the exact same
# strict classes rather than creating looser compatibility models.
PromptSpec = ProductLockPrompts
LockRevisionStatus = ProductLockStatus
ProductLockRevisionStatus = ProductLockStatus
GenerationStatusV2 = GenerationLifecycleStatus
ProviderCapability = ProviderCapabilities
ProviderCapabilitiesSnapshot = ProviderCapabilities
VariantManifest = GenerationVariantManifest
GenerationVariant = GenerationVariantManifest
QAManifest = VariantQAManifest
