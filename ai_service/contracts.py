"""Dependency-light contracts for Campaign Studio V2.

The legacy ``ai`` package is a Colab runner.  V2 keeps its orchestration and
provider contracts in this package so importing the website or running CPU
tests never imports a segmentation or diffusion runtime.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence


def canonical_json(value: Any) -> str:
    """Serialize JSON values in the form used for provenance hashes."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _finite(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


class ProductLockStatus(str, Enum):
    """Decision made by the product-lock gate."""

    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    ABSTAINED = "abstained"


class PointLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclass(frozen=True)
class NormalizedPoint:
    """A point in canonical EXIF-normalized image coordinates."""

    x: float
    y: float
    label: PointLabel = PointLabel.POSITIVE

    def __post_init__(self) -> None:
        x = _finite(self.x, "point.x")
        y = _finite(self.y, "point.y")
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError("point coordinates must be in [0, 1]")
        label = self.label if isinstance(self.label, PointLabel) else PointLabel(str(self.label))
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)
        object.__setattr__(self, "label", label)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, label: PointLabel | str | None = None) -> "NormalizedPoint":
        if not isinstance(value, Mapping):
            raise ValueError("point must be an object")
        unknown = set(value) - {"x", "y", "label"}
        if unknown:
            raise ValueError(f"unknown point fields: {', '.join(sorted(unknown))}")
        point_label = label if label is not None else value.get("label", PointLabel.POSITIVE)
        return cls(value.get("x"), value.get("y"), point_label)

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "label": self.label.value}


@dataclass(frozen=True)
class NormalizedBox:
    """A box in canonical EXIF-normalized image coordinates."""

    x: float
    y: float
    width: float
    height: float
    type: str = "box"

    def __post_init__(self) -> None:
        if self.type != "box":
            raise ValueError("box.type must be 'box'")
        values = {name: _finite(getattr(self, name), f"box.{name}") for name in ("x", "y", "width", "height")}
        if values["x"] < 0 or values["y"] < 0:
            raise ValueError("box origin must be in [0, 1]")
        if values["width"] <= 0 or values["height"] <= 0:
            raise ValueError("box dimensions must be positive")
        if values["x"] + values["width"] > 1 or values["y"] + values["height"] > 1:
            raise ValueError("box must remain inside [0, 1] image bounds")
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NormalizedBox":
        if not isinstance(value, Mapping):
            raise ValueError("box must be an object")
        unknown = set(value) - {"type", "x", "y", "width", "height"}
        if unknown:
            raise ValueError(f"unknown box fields: {', '.join(sorted(unknown))}")
        return cls(value.get("x"), value.get("y"), value.get("width"), value.get("height"), value.get("type", "box"))

    def to_dict(self) -> dict[str, Any]:
        return {"type": "box", "x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class RefinementContract:
    """Strict positive/negative point and optional box refinement request."""

    positive_points: tuple[NormalizedPoint, ...] = ()
    negative_points: tuple[NormalizedPoint, ...] = ()
    box: NormalizedBox | None = None

    def __post_init__(self) -> None:
        positives = tuple(
            point if isinstance(point, NormalizedPoint) else NormalizedPoint.from_mapping(point, label=PointLabel.POSITIVE)
            for point in self.positive_points
        )
        negatives = tuple(
            point if isinstance(point, NormalizedPoint) else NormalizedPoint.from_mapping(point, label=PointLabel.NEGATIVE)
            for point in self.negative_points
        )
        box = self.box if self.box is None or isinstance(self.box, NormalizedBox) else NormalizedBox.from_mapping(self.box)
        if len(positives) + len(negatives) > 64:
            raise ValueError("refinement contains too many points")
        object.__setattr__(self, "positive_points", positives)
        object.__setattr__(self, "negative_points", negatives)
        object.__setattr__(self, "box", box)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "RefinementContract":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("refinement must be an object")
        allowed = {"positive_points", "negative_points", "points", "box", "target_box", "bbox"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown refinement fields: {', '.join(sorted(unknown))}")
        positives = list(value.get("positive_points", ()) or ())
        negatives = list(value.get("negative_points", ()) or ())
        for point in value.get("points", ()) or ():
            parsed = NormalizedPoint.from_mapping(point)
            (positives if parsed.label is PointLabel.POSITIVE else negatives).append(parsed)
        return cls(
            positive_points=tuple(positives),
            negative_points=tuple(negatives),
            box=(
                None
                if value.get("box", value.get("target_box", value.get("bbox"))) is None
                else NormalizedBox.from_mapping(value.get("box", value.get("target_box", value.get("bbox"))))
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "positive_points": [point.to_dict() for point in self.positive_points],
            "negative_points": [point.to_dict() for point in self.negative_points],
        }
        if self.box is not None:
            result["box"] = self.box.to_dict()
        return result


@dataclass(frozen=True)
class MaskMetrics:
    coverage_fraction: float
    bbox: tuple[int, int, int, int] | None
    connected_components: int
    edge_touch_fraction: float
    hole_fraction: float
    foreground_pixels: int
    total_pixels: int
    score: float
    suspicious_reasons: tuple[str, ...] = ()
    perimeter_fraction: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("coverage_fraction", "edge_touch_fraction", "hole_fraction", "score"):
            value = _finite(getattr(self, field_name), f"metrics.{field_name}")
            if not 0 <= value <= 1:
                raise ValueError(f"metrics.{field_name} must be in [0, 1]")
            object.__setattr__(self, field_name, value)
        perimeter = _finite(self.perimeter_fraction, "metrics.perimeter_fraction")
        if not 0 <= perimeter <= 1:
            raise ValueError("metrics.perimeter_fraction must be in [0, 1]")
        object.__setattr__(self, "perimeter_fraction", perimeter)
        if self.connected_components < 0 or self.foreground_pixels < 0 or self.total_pixels <= 0:
            raise ValueError("mask metric counts are invalid")
        object.__setattr__(self, "suspicious_reasons", tuple(str(item) for item in self.suspicious_reasons))

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_fraction": self.coverage_fraction,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "connected_components": self.connected_components,
            "edge_touch_fraction": self.edge_touch_fraction,
            "hole_fraction": self.hole_fraction,
            "foreground_pixels": self.foreground_pixels,
            "total_pixels": self.total_pixels,
            "score": self.score,
            "suspicious_reasons": list(self.suspicious_reasons),
            "perimeter_fraction": self.perimeter_fraction,
        }

    # Readable aliases used by callers that do not need to know the internal
    # field names; all point to the same canonical measurements.
    @property
    def area_fraction(self) -> float:
        return self.coverage_fraction

    @property
    def component_count(self) -> int:
        return self.connected_components

    @property
    def mask_quality_score(self) -> float:
        return self.score


@dataclass(frozen=True)
class ProductLock:
    """Immutable-style, content-addressed product-lock output.

    The canonical image and artifact bytes are deliberately stored as bytes,
    not mutable ``PIL.Image`` instances.  Refinement returns a new instance;
    callers cannot change a previously accepted lock in place.
    """

    lock_id: str
    revision: int
    status: ProductLockStatus
    source_sha256: str
    canonical_image_sha256: str
    mask_sha256: str
    cutout_sha256: str
    provenance_sha256: str
    width: int
    height: int
    canonical_image_png: bytes = field(repr=False)
    mask_png: bytes = field(repr=False)
    cutout_png: bytes = field(repr=False)
    metrics: MaskMetrics
    reasons: tuple[str, ...] = ()
    algorithm: str = "canonical-binary-mask-v1"
    source_mime: str = "image/png"
    refinement: RefinementContract | None = None

    def __post_init__(self) -> None:
        if not self.lock_id:
            raise ValueError("lock_id is required")
        if self.revision < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("invalid lock dimensions or revision")
        for name in ("source_sha256", "canonical_image_sha256", "mask_sha256", "cutout_sha256", "provenance_sha256"):
            value = str(getattr(self, name))
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        object.__setattr__(self, "status", self.status if isinstance(self.status, ProductLockStatus) else ProductLockStatus(str(self.status)))
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        # Defensive copies ensure a caller cannot mutate a bytearray after construction.
        for name in ("canonical_image_png", "mask_png", "cutout_png"):
            value = bytes(getattr(self, name))
            object.__setattr__(self, name, value)

    @property
    def accepted(self) -> bool:
        return self.status is ProductLockStatus.ACCEPTED

    @property
    def mask_bytes(self) -> bytes:
        return self.mask_png

    @property
    def cutout_bytes(self) -> bytes:
        return self.cutout_png

    @property
    def canonical_image_bytes(self) -> bytes:
        return self.canonical_image_png

    def to_dict(self, *, include_artifact_bytes: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "lock_id": self.lock_id,
            "revision": self.revision,
            "status": self.status.value,
            "source_sha256": self.source_sha256,
            "canonical_image_sha256": self.canonical_image_sha256,
            "mask_sha256": self.mask_sha256,
            "cutout_sha256": self.cutout_sha256,
            "provenance_sha256": self.provenance_sha256,
            "width": self.width,
            "height": self.height,
            "metrics": self.metrics.to_dict(),
            "reasons": list(self.reasons),
            "algorithm": self.algorithm,
            "source_mime": self.source_mime,
            "refinement": self.refinement.to_dict() if self.refinement else None,
        }
        if include_artifact_bytes:
            result.update({"canonical_image_png": self.canonical_image_png, "mask_png": self.mask_png, "cutout_png": self.cutout_png})
        return result


@dataclass(frozen=True)
class SegmentationResult:
    mask: Any
    confidence: float | None = None
    model: str = "unknown"
    provider: str = "local"
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.confidence is not None:
            confidence = _finite(self.confidence, "segmentation confidence")
            if not 0 <= confidence <= 1:
                raise ValueError("segmentation confidence must be in [0, 1]")
            object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class Proposal:
    box: NormalizedBox
    score: float
    label: str = "product"
    proposal_id: str = ""

    def __post_init__(self) -> None:
        box = self.box if isinstance(self.box, NormalizedBox) else NormalizedBox.from_mapping(self.box)
        score = _finite(self.score, "proposal.score")
        if not 0 <= score <= 1:
            raise ValueError("proposal.score must be in [0, 1]")
        object.__setattr__(self, "box", box)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "label", str(self.label))
        object.__setattr__(self, "proposal_id", str(self.proposal_id))


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str = "unknown"
    model: str = "unknown"
    endpoint: str = "images/generations"
    supports_generation: bool = True
    supports_edit: bool = False
    supports_inpainting: bool = False
    deterministic_seed: bool = False
    max_width: int = 4096
    max_height: int = 4096
    max_pixels: int = 16_000_000
    input_formats: tuple[str, ...] = ("image/png", "image/jpeg", "image/webp")
    output_formats: tuple[str, ...] = ("image/png", "image/jpeg", "image/webp")
    estimated_cost_usd: float = 0.0
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        cost = _finite(self.estimated_cost_usd, "estimated_cost_usd")
        if cost < 0:
            raise ValueError("estimated cost cannot be negative")
        object.__setattr__(self, "estimated_cost_usd", cost)
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))

    @property
    def supports_deterministic_seed(self) -> bool:
        return self.deterministic_seed

    @property
    def supports_image_edit(self) -> bool:
        return self.supports_edit


@dataclass(frozen=True)
class CostPreflight:
    allowed: bool
    estimated_cost_usd: float
    credits_available_usd: float | None = None
    reason: str | None = None
    checked_at: str | None = None

    def __post_init__(self) -> None:
        cost = _finite(self.estimated_cost_usd, "estimated_cost_usd")
        if cost < 0:
            raise ValueError("estimated cost cannot be negative")
        object.__setattr__(self, "estimated_cost_usd", cost)
        if self.credits_available_usd is not None:
            available = _finite(self.credits_available_usd, "credits_available_usd")
            if available < 0:
                raise ValueError("credits cannot be negative")
            object.__setattr__(self, "credits_available_usd", available)


@dataclass(frozen=True)
class GenerationSpec:
    prompt: str
    width: int = 1024
    height: int = 1024
    seed: int | None = None
    model: str | None = None
    image_bytes: bytes | None = field(default=None, repr=False)
    image_mime: str = "image/png"
    mask_bytes: bytes | None = field(default=None, repr=False)
    negative_prompt: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not str(self.prompt).strip():
            raise ValueError("prompt must not be empty")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("generation dimensions must be positive")
        if self.seed is not None and not 0 <= int(self.seed) <= 4_294_967_295:
            raise ValueError("seed must be a uint32")
        object.__setattr__(self, "prompt", str(self.prompt).strip())
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.image_bytes is not None:
            object.__setattr__(self, "image_bytes", bytes(self.image_bytes))
        if self.mask_bytes is not None:
            object.__setattr__(self, "mask_bytes", bytes(self.mask_bytes))


@dataclass(frozen=True)
class ExecutionPlan:
    request_id: str
    provider: str
    operation: str
    capabilities: ProviderCapabilities
    spec: GenerationSpec
    estimated_cost_usd: float
    attempt: int = 0
    max_attempts: int = 1

    def __post_init__(self) -> None:
        try:
            uuid.UUID(str(self.request_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("request_id must be a UUID") from exc
        if self.attempt < 0 or self.max_attempts < 1 or self.attempt >= self.max_attempts:
            raise ValueError("invalid execution attempt")
        object.__setattr__(self, "estimated_cost_usd", max(0.0, _finite(self.estimated_cost_usd, "estimated_cost_usd")))


@dataclass(frozen=True)
class ProviderRequest:
    """Transport-neutral request; body is hidden from repr to avoid leaks."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: Mapping[str, Any] | None = field(default=None, repr=False)
    files: Mapping[str, Any] | None = field(default=None, repr=False)
    data: Mapping[str, Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", str(self.method).upper())
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        if self.json_body is not None:
            object.__setattr__(self, "json_body", MappingProxyType(dict(self.json_body)))
        if self.data is not None:
            object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
        if self.files is not None:
            object.__setattr__(self, "files", MappingProxyType(dict(self.files)))


@dataclass(frozen=True)
class NormalizedImageResult:
    image_bytes: bytes = field(repr=False)
    mime_type: str
    width: int
    height: int
    sha256: str
    request_id: str
    provider: str
    model: str
    attempts: int = 1
    usage: Mapping[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    deterministic_seed_claim: bool = False
    source_url: str | None = None

    def __post_init__(self) -> None:
        value = bytes(self.image_bytes)
        digest = sha256_bytes(value)
        if self.sha256 != digest:
            raise ValueError("normalized image sha256 does not match bytes")
        if self.width <= 0 or self.height <= 0 or self.attempts < 1:
            raise ValueError("invalid normalized image metadata")
        object.__setattr__(self, "image_bytes", value)
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
        if self.cost_usd is not None:
            cost = _finite(self.cost_usd, "cost_usd")
            if cost < 0:
                raise ValueError("cost cannot be negative")
            object.__setattr__(self, "cost_usd", cost)


@dataclass(frozen=True)
class ScenePlan:
    category: str
    prompt: str
    negative_prompt: str
    width: int
    height: int
    seed: int | None
    provider_model: str | None = None
    remote_seed_deterministic: bool = False
    plan_sha256: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("scene dimensions must be positive")
        payload = {
            "category": self.category,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "provider_model": self.provider_model,
            "remote_seed_deterministic": self.remote_seed_deterministic,
            "metadata": dict(self.metadata),
        }
        expected = sha256_bytes(canonical_json(payload).encode("utf-8"))
        object.__setattr__(self, "plan_sha256", self.plan_sha256 or expected)
        if self.plan_sha256 and self.plan_sha256 != expected:
            raise ValueError("plan_sha256 does not match plan contents")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class ProductTransform:
    """Deterministic placement transform for restoring the canonical product."""

    x: int
    y: int
    width: int
    height: int
    source_bbox: tuple[int, int, int, int]
    interpolation: str = "lanczos"

    def __post_init__(self) -> None:
        if min(self.x, self.y, self.width, self.height) < 0 or self.width == 0 or self.height == 0:
            raise ValueError("invalid product transform")
        if len(self.source_bbox) != 4 or any(int(value) < 0 for value in self.source_bbox):
            raise ValueError("invalid source bbox")

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height, "source_bbox": list(self.source_bbox), "interpolation": self.interpolation}


@dataclass(frozen=True)
class PixelComparison:
    compared_pixels: int
    differing_pixels: int
    exact: bool
    max_channel_delta: int
    sha256_before: str | None = None
    sha256_after: str | None = None

    def __post_init__(self) -> None:
        if self.compared_pixels < 0 or self.differing_pixels < 0 or self.differing_pixels > self.compared_pixels:
            raise ValueError("invalid pixel comparison counts")
        if self.max_channel_delta < 0:
            raise ValueError("max channel delta cannot be negative")


@dataclass(frozen=True)
class QAResult:
    passed: bool
    status: str
    findings: tuple[str, ...] = ()
    duplicate_checked: bool = False
    person_checked: bool = False
    hands_checked: bool = False
    pixel_comparison: PixelComparison | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(str(item) for item in self.findings))


@dataclass(frozen=True)
class VariantManifest:
    variant_id: str
    request_id: str
    lock_id: str
    provider: str
    model: str
    seed: int | None
    remote_seed_deterministic: bool
    prompt_sha256: str
    image_sha256: str
    pixel_comparison: PixelComparison
    qa: QAResult
    cost_usd: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)
    usage: Mapping[str, Any] = field(default_factory=dict, repr=False)
    attempts: int = 1

    def __post_init__(self) -> None:
        if not self.remote_seed_deterministic and self.metadata.get("deterministic_remote_seed") is True:
            raise ValueError("remote seed determinism cannot be claimed without provider capability")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
        if self.attempts < 1:
            raise ValueError("manifest attempts must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "request_id": self.request_id,
            "lock_id": self.lock_id,
            "provider": self.provider,
            "model": self.model,
            "seed": self.seed,
            "remote_seed_deterministic": self.remote_seed_deterministic,
            "prompt_sha256": self.prompt_sha256,
            "image_sha256": self.image_sha256,
            "pixel_comparison": {
                "compared_pixels": self.pixel_comparison.compared_pixels,
                "differing_pixels": self.pixel_comparison.differing_pixels,
                "exact": self.pixel_comparison.exact,
                "max_channel_delta": self.pixel_comparison.max_channel_delta,
            },
            "qa": {"passed": self.qa.passed, "status": self.qa.status, "findings": list(self.qa.findings)},
            "cost_usd": self.cost_usd,
            "metadata": dict(self.metadata),
            "usage": dict(self.usage),
            "attempts": self.attempts,
        }


def new_request_id() -> str:
    return str(uuid.uuid4())


__all__ = [
    "CostPreflight",
    "ExecutionPlan",
    "GenerationSpec",
    "MaskMetrics",
    "NormalizedBox",
    "NormalizedImageResult",
    "NormalizedPoint",
    "PixelComparison",
    "PointLabel",
    "ProductLock",
    "ProductLockStatus",
    "ProductTransform",
    "Proposal",
    "ProviderCapabilities",
    "ProviderRequest",
    "QAResult",
    "RefinementContract",
    "ScenePlan",
    "SegmentationResult",
    "VariantManifest",
    "canonical_json",
    "new_request_id",
    "sha256_bytes",
]
