from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)


ShortText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200),
]
MediumText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500),
]
LongText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=3000),
]
Sha256 = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")
]
GitRevision = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$")
]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class CampaignLanguage(str, Enum):
    FR = "fr"
    EN = "en"


class GenerationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


class GenerationStage(str, Enum):
    ANALYSIS = "analysis"
    EXTRACTION = "extraction"
    ART_DIRECTION = "art_direction"
    BACKGROUND = "background"
    COMPOSITION = "composition"
    COPY = "copy"
    EXPORT = "export"
    PACKAGING = "packaging"


class AssetFormat(str, Enum):
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"


class ArtifactName(str, Enum):
    INSTAGRAM = "instagram.jpg"
    FACEBOOK = "facebook.jpg"
    LINKEDIN = "linkedin.jpg"
    COPY = "copy.json"
    CUTOUT = "cutout.png"
    MASK = "mask.png"
    BACKGROUND = "background.jpg"
    MANIFEST = "manifest.json"

    @classmethod
    def non_manifest(cls) -> tuple[ArtifactName, ...]:
        return tuple(member for member in cls if member is not cls.MANIFEST)


class ErrorCode(str, Enum):
    AI_SERVICE_UNAVAILABLE = "AI_SERVICE_UNAVAILABLE"
    AI_RUNTIME_LOST = "AI_RUNTIME_LOST"
    REMOTE_AUTH_FAILED = "REMOTE_AUTH_FAILED"
    REMOTE_PROTOCOL_ERROR = "REMOTE_PROTOCOL_ERROR"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    MASK_QUALITY_FAILED = "MASK_QUALITY_FAILED"
    CLAIM_SAFETY_FAILED = "CLAIM_SAFETY_FAILED"
    INVALID_GENERATION_REQUEST = "INVALID_GENERATION_REQUEST"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    ARTIFACT_CONTRACT_FAILED = "ARTIFACT_CONTRACT_FAILED"
    ARTIFACT_CHECKSUM_MISMATCH = "ARTIFACT_CHECKSUM_MISMATCH"
    ARTIFACT_STORAGE_FAILED = "ARTIFACT_STORAGE_FAILED"
    GENERATION_STALE = "GENERATION_STALE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class NormalizedBox(StrictModel):
    type: Literal["box"]
    x: float = Field(strict=True, ge=0.0, le=1.0)
    y: float = Field(strict=True, ge=0.0, le=1.0)
    width: float = Field(strict=True, gt=0.0, le=1.0)
    height: float = Field(strict=True, gt=0.0, le=1.0)

    @field_validator("x", "y", "width", "height")
    @classmethod
    def require_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("box coordinates must be finite")
        return value

    @model_validator(mode="after")
    def require_inside_image(self) -> NormalizedBox:
        tolerance = 1e-12
        if self.x + self.width > 1.0 + tolerance:
            raise ValueError("box exceeds image width")
        if self.y + self.height > 1.0 + tolerance:
            raise ValueError("box exceeds image height")
        return self


class PixelBox(StrictModel):
    x_min: int = Field(strict=True, ge=0)
    y_min: int = Field(strict=True, ge=0)
    x_max: int = Field(strict=True, gt=0)
    y_max: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def require_positive_area(self) -> PixelBox:
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise ValueError("pixel box must have positive area")
        return self


class GenerationRequest(StrictModel):
    language: CampaignLanguage
    seed: int = Field(strict=True, ge=0, le=4_294_967_295)
    audience: MediumText | None = None
    benefits: tuple[MediumText, ...] = Field(default_factory=tuple, max_length=20)
    ingredients: tuple[MediumText, ...] = Field(default_factory=tuple, max_length=50)
    verified_claims: tuple[MediumText, ...] = Field(
        default_factory=tuple, max_length=30
    )
    cta: ShortText | None = None
    creative_direction: MediumText | None = None
    target_hint: NormalizedBox | None = None
    source_generation_id: ShortText | None = None

    @field_validator("benefits", "ingredients", "verified_claims", mode="before")
    @classmethod
    def normalize_text_sequences(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("benefits", "ingredients", "verified_claims")
    @classmethod
    def require_unique_text(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = [value.casefold() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate evidence values are not permitted")
        return values


class EvidenceKind(str, Enum):
    PRODUCT_NAME = "product_name"
    CATEGORY = "category"
    BRAND = "brand"
    AUDIENCE = "audience"
    BENEFIT = "benefit"
    INGREDIENT = "ingredient"
    VERIFIED_CLAIM = "verified_claim"
    CTA = "cta"


class EvidenceItem(StrictModel):
    id: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[a-z_]+-[1-9][0-9]*$")
    ]
    kind: EvidenceKind
    text: MediumText


class ClaimUsage(StrictModel):
    evidence_id: Annotated[
        str, StringConstraints(pattern=r"^[a-z_]+-[1-9][0-9]*$")
    ]
    rendered_text: MediumText


class PlatformCopy(StrictModel):
    text: LongText
    hashtags: tuple[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=2,
                max_length=80,
                pattern=r"^#[^\s#]+$",
            ),
        ],
        ...,
    ] = Field(default_factory=tuple, max_length=20)
    claims: tuple[ClaimUsage, ...] = Field(default_factory=tuple, max_length=30)

    @model_validator(mode="after")
    def require_unique_claim_receipts(self) -> PlatformCopy:
        evidence_ids = tuple(claim.evidence_id for claim in self.claims)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("platform claim evidence IDs must be unique")
        return self


class CopyPayload(StrictModel):
    language: CampaignLanguage
    instagram: PlatformCopy
    facebook: PlatformCopy
    linkedin: PlatformCopy


EvidenceId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[a-z_]+-[1-9][0-9]*$")
]


class PlatformCopySelection(StrictModel):
    """Evidence IDs selected by the language model; never arbitrary ad prose."""

    evidence_ids: tuple[EvidenceId, ...] = Field(
        default_factory=tuple, max_length=3
    )

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def normalize_evidence_ids(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("copy evidence selections must be unique")
        return values


class CopyPlan(StrictModel):
    """Constrained output accepted from Qwen before deterministic copy assembly."""

    language: CampaignLanguage
    instagram: PlatformCopySelection
    facebook: PlatformCopySelection
    linkedin: PlatformCopySelection


class CandidateBox(NormalizedBox):
    id: Annotated[str, StringConstraints(pattern=r"^candidate-[1-9][0-9]*$")]
    score: float = Field(strict=True, ge=0.0, le=1.0)

    @field_validator("score")
    @classmethod
    def require_finite_score(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("candidate score must be finite")
        return value


class ProductSnapshot(StrictModel):
    name: ShortText
    brand: ShortText | None = None
    category: ShortText
    original_mime: Literal["image/jpeg", "image/png", "image/webp"]
    original_sha256: Sha256
    original_width: int = Field(strict=True, gt=0, le=20_000)
    original_height: int = Field(strict=True, gt=0, le=20_000)

    @model_validator(mode="after")
    def enforce_hard_pixel_cap(self) -> ProductSnapshot:
        if self.original_width * self.original_height > 100_000_000:
            raise ValueError("source image exceeds the 100M hard pixel cap")
        return self


class PipelineSnapshot(StrictModel):
    product: ProductSnapshot
    generation: GenerationRequest


class RemoteJobRequest(StrictModel):
    generation_id: ShortText
    request_id: ShortText
    input_snapshot_hash: Sha256
    input: PipelineSnapshot


class ErrorPayload(StrictModel):
    code: ErrorCode
    message: ShortText


class DependencyRef(StrictModel):
    name: ShortText
    version: Annotated[
        str,
        StringConstraints(
            strict=True, strip_whitespace=True, min_length=1, max_length=80
        ),
    ]


class ModelRef(StrictModel):
    repo_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            strict=True,
            min_length=3,
            max_length=200,
            pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
        ),
    ]
    revision: GitRevision
    license: ShortText
    custom_code: StrictBool = False
    custom_code_reviewed: StrictBool = False

    @model_validator(mode="after")
    def require_custom_code_review(self) -> ModelRef:
        if self.custom_code and not self.custom_code_reviewed:
            raise ValueError("custom model code must be reviewed before enablement")
        return self


class StageReceipt(StrictModel):
    stage: GenerationStage
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("stage receipt timestamps must be timezone-aware")
        return value


class ArtifactRecord(StrictModel):
    name: ArtifactName
    mime: Literal["image/jpeg", "image/png", "application/json"]
    bytes: int = Field(strict=True, gt=0)
    sha256: Sha256
    width: int | None = Field(default=None, strict=True, gt=0)
    height: int | None = Field(default=None, strict=True, gt=0)
    role: Literal[
        "platform_final",
        "campaign_copy",
        "product_cutout",
        "accepted_mask",
        "generated_background",
    ]

    @model_validator(mode="after")
    def require_dimension_pair(self) -> ArtifactRecord:
        if (self.width is None) != (self.height is None):
            raise ValueError("artifact width and height must be both set or both absent")
        return self


class Manifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    pipeline_version: Annotated[
        str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    ]
    generation_id: ShortText
    request_id: ShortText
    runtime_id: ShortText
    input_snapshot_hash: Sha256
    seed: int = Field(strict=True, ge=0, le=4_294_967_295)
    language: CampaignLanguage
    target_selection: NormalizedBox | None
    dependencies: tuple[DependencyRef, ...]
    models: dict[str, ModelRef]
    stage_receipts: tuple[StageReceipt, ...]
    artifacts: tuple[ArtifactRecord, ...]

    @model_validator(mode="after")
    def validate_complete_contract(self) -> Manifest:
        dependency_names = [item.name.casefold() for item in self.dependencies]
        if len(dependency_names) != len(set(dependency_names)):
            raise ValueError("dependency names must be unique")

        required_models = {"grounding_dino", "sam", "sdxl", "qwen"}
        allowed_models = required_models | {"birefnet"}
        model_names = set(self.models)
        if not required_models.issubset(model_names) or not model_names.issubset(
            allowed_models
        ):
            raise ValueError("manifest model set does not match the pipeline contract")

        stages = tuple(receipt.stage for receipt in self.stage_receipts)
        if stages != tuple(GenerationStage):
            raise ValueError("stage receipts must contain every stage in order")

        names = tuple(record.name for record in self.artifacts)
        if len(names) != len(set(names)) or set(names) != set(
            ArtifactName.non_manifest()
        ):
            raise ValueError("manifest must describe each non-manifest artifact once")
        return self


class JobResult(StrictModel):
    id: ShortText
    runtime_id: ShortText
    status: GenerationStatus
    stage: GenerationStage | None = None
    completed_stages: tuple[GenerationStage, ...] = Field(default_factory=tuple)
    error: ErrorPayload | None = None
    ambiguity: tuple[CandidateBox, ...] | None = Field(
        default=None, min_length=2, max_length=20
    )
    manifest: Manifest | None = None

    @model_validator(mode="after")
    def require_status_consistency(self) -> JobResult:
        if len(self.completed_stages) != len(set(self.completed_stages)):
            raise ValueError("completed stages must be unique")
        expected_prefix = tuple(GenerationStage)[: len(self.completed_stages)]
        if self.completed_stages != expected_prefix:
            raise ValueError("completed stages must be an ordered prefix")
        if self.status is GenerationStatus.PROCESSING and self.stage is None:
            raise ValueError("processing jobs require a current stage")
        if self.status is GenerationStatus.DONE:
            if self.manifest is None or self.error is not None:
                raise ValueError("done jobs require a manifest and no error")
        elif self.manifest is not None:
            raise ValueError("only done jobs may expose a manifest")
        if self.status is GenerationStatus.ERROR:
            if self.error is None:
                raise ValueError("error jobs require an error payload")
        elif self.error is not None or self.ambiguity is not None:
            raise ValueError("only error jobs may expose error details")
        if self.ambiguity is not None and (
            self.error is None or self.error.code is not ErrorCode.TARGET_AMBIGUOUS
        ):
            raise ValueError("candidate boxes require TARGET_AMBIGUOUS")
        return self
