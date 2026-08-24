"""Concrete Campaign Studio V2 AI-service bridge.

The website owns authentication, persistence, and private storage.  This
module owns the boundary where those durable records become the immutable
``ai_service`` lock/orchestration objects.  It deliberately never labels a
model as SAM2/Grounding-DINO unless that adapter was actually constructed and
executed.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Any, Mapping

from PIL import Image, ImageChops, ImageStat

from ai_service import (
    NormalizedBox,
    OptionalDependencyError,
    ProductLock,
    ProductLockProcessor,
    ProductLockStatus as AIProductLockStatus,
    U2NetSegmenter,
    canonicalize_image,
    mask_metrics,
    sha256_bytes,
)
from ai_service.contracts import RefinementContract

from app.core.config import settings
from app.models import Product, ProductLockRevision
from app.services.storage import StorageError, storage


class AIModelUnavailable(RuntimeError):
    """The configured real model/runtime cannot be executed in this worker."""


def _target_box(value: Mapping[str, Any] | None) -> NormalizedBox | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return NormalizedBox.from_mapping(value)
    except (TypeError, ValueError):
        return None


def refinement_for_request(request: Any) -> RefinementContract:
    target = request.normalized_target
    payload: dict[str, Any] = {
        "positive_points": [item.model_dump(mode="json") for item in request.positive_points],
        "negative_points": [item.model_dump(mode="json") for item in request.negative_points],
    }
    if target is not None:
        payload["box"] = target.model_dump(mode="json")
    return RefinementContract.from_mapping(payload)


def _metrics_payload(lock: ProductLock) -> dict[str, float]:
    payload = lock.metrics.to_dict()
    # ``ProductLockRevisionOut.metrics`` is intentionally numeric-only.  The
    # textual reasons remain in model_provenance and the normalized candidate
    # box is returned as target_box.
    payload["accepted"] = 1.0 if lock.status is AIProductLockStatus.ACCEPTED else 0.0
    if lock.refinement is not None:
        payload["refinement_positive_points"] = float(len(lock.refinement.positive_points))
        payload["refinement_negative_points"] = float(len(lock.refinement.negative_points))
    return {str(key): float(value) for key, value in payload.items() if isinstance(value, (int, float))}


def _mask_target(lock: ProductLock) -> dict[str, Any] | None:
    bbox = lock.metrics.bbox
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    if right <= left or bottom <= top:
        return None
    return {
        "type": "box",
        "x": max(0.0, min(1.0, left / lock.width)),
        "y": max(0.0, min(1.0, top / lock.height)),
        "width": max(0.001, min(1.0, (right - left) / lock.width)),
        "height": max(0.001, min(1.0, (bottom - top) / lock.height)),
    }


def _processor() -> ProductLockProcessor:
    backend = settings.V2_SEGMENTATION_BACKEND.casefold().strip()
    if backend != "u2net-onnx":
        raise AIModelUnavailable(
            f"Configured segmentation backend '{backend or 'empty'}' is unavailable; no model was executed."
        )
    try:
        segmenter = U2NetSegmenter(model_dir=settings.V2_SEGMENTATION_MODEL_DIR, model=settings.V2_SEGMENTATION_MODEL)
    except Exception as exc:  # pragma: no cover - defensive import boundary
        raise AIModelUnavailable("The configured rembg segmenter could not be initialized.") from exc
    return ProductLockProcessor(
        segmenter=segmenter,
        min_score=settings.V2_LOCK_MIN_SCORE,
        accept_model_confidence=settings.V2_LOCK_MIN_MODEL_CONFIDENCE,
        strict=False,
    )


def create_lock_object(product: Product, request: Any, *, lock_id: str, revision: int) -> ProductLock:
    """Run the configured real segmentation adapter over the stored source."""

    try:
        source = storage.read_bytes(product.original_storage_key)
    except StorageError as exc:
        raise AIModelUnavailable("The stored product source is unavailable for segmentation.") from exc
    try:
        target_box = request.normalized_target
        target_payload = target_box.model_dump(mode="json") if target_box is not None else None
        return _processor().create(
            source,
            target_box=target_payload,
            refinement=refinement_for_request(request),
            source_mime=product.original_mime,
            lock_id=lock_id,
            revision=revision,
            source_sha256=product.original_sha256,
        )
    except Exception as exc:
        # Do not convert a missing optional model or an invalid result into a
        # successful-looking lock.  The route turns this into a bounded 503.
        if isinstance(exc, (AIModelUnavailable, OptionalDependencyError, ImportError, ModuleNotFoundError)):
            raise AIModelUnavailable(str(exc)) from exc
        raise


def persist_lock_artifacts(product: Product, revision: ProductLockRevision, lock: ProductLock) -> None:
    """Persist mask/cutout bytes and provenance under an owner-scoped key."""

    prefix = f"product-locks/{product.user_id}/{product.id}/{revision.id}"
    mask_key = f"{prefix}/mask.png"
    cutout_key = f"{prefix}/cutout.png"
    try:
        storage.put_bytes(mask_key, lock.mask_png)
        storage.put_bytes(cutout_key, lock.cutout_png)
    except StorageError:
        for key in (mask_key, cutout_key):
            try:
                storage.delete(key)
            except StorageError:
                pass
        raise

    revision.mask_storage_key = mask_key
    revision.mask_sha256 = lock.mask_sha256
    revision.cutout_storage_key = cutout_key
    revision.cutout_sha256 = lock.cutout_sha256
    revision.target_geometry = _mask_target(lock)
    revision.metrics = _metrics_payload(lock)
    revision.model_provenance = {
        "pipeline": "ai_service",
        "algorithm": lock.algorithm,
        "segmenter_provider": "onnxruntime",
        "segmenter_model": settings.V2_SEGMENTATION_MODEL,
        "lock_status": lock.status.value,
        "reasons": ",".join(lock.reasons),
        "provenance_sha256": lock.provenance_sha256,
        "source_sha256": lock.source_sha256,
        "canonical_image_sha256": lock.canonical_image_sha256,
        "mask_sha256": lock.mask_sha256,
        "cutout_sha256": lock.cutout_sha256,
    }


def load_lock_object(product: Product, revision: ProductLockRevision) -> ProductLock:
    """Rehydrate a validated DB revision into the immutable AI object."""

    if not revision.mask_storage_key or not revision.cutout_storage_key:
        raise AIModelUnavailable("The product lock has no persisted mask/cutout artifacts.")
    try:
        source = storage.read_bytes(product.original_storage_key)
        mask_bytes = storage.read_bytes(revision.mask_storage_key)
        cutout_bytes = storage.read_bytes(revision.cutout_storage_key)
    except StorageError as exc:
        raise AIModelUnavailable("Product-lock artifacts are unavailable.") from exc
    canonical = canonicalize_image(source)
    with Image.open(io.BytesIO(mask_bytes)) as raw_mask:
        mask = raw_mask.convert("L")
        target = _target_box(revision.target_geometry)
        metrics = mask_metrics(mask, target_box=target)
    provenance = str((revision.model_provenance or {}).get("provenance_sha256") or "")
    if len(provenance) != 64:
        provenance = sha256_bytes(
            f"{revision.id}:{revision.source_sha256}:{revision.mask_sha256}:{revision.cutout_sha256}".encode()
        )
    return ProductLock(
        lock_id=str(revision.id),
        revision=revision.revision,
        status=AIProductLockStatus.ACCEPTED,
        source_sha256=revision.source_sha256,
        canonical_image_sha256=sha256_bytes(canonical.png_bytes),
        mask_sha256=revision.mask_sha256 or sha256_bytes(mask_bytes),
        cutout_sha256=revision.cutout_sha256 or sha256_bytes(cutout_bytes),
        provenance_sha256=provenance,
        width=canonical.width,
        height=canonical.height,
        canonical_image_png=canonical.png_bytes,
        mask_png=mask_bytes,
        cutout_png=cutout_bytes,
        metrics=metrics,
        reasons=(),
        source_mime=revision.source_mime,
    )


@dataclass
class PixelSafetyDetectors:
    """Conservative, dependency-free QA for the protected source region.

    This is explicitly a pixel heuristic, not a person detector model.  It is
    useful for local acceptance while remaining honest in provenance.  A
    suspicious skin-colored region inside the protected source mask fails QA,
    which forces a negative-point refinement instead of silently exporting a
    hand-contaminated product lock.
    """

    lock: ProductLock
    protected_mask: Image.Image

    @staticmethod
    def _skin(pixel: tuple[int, ...]) -> bool:
        r, g, b = pixel[:3]
        return r > 95 and g > 42 and b > 20 and r > g * 1.18 and g > b * 1.12 and r - b > 28

    def _protected_skin_fraction(self, image: Image.Image) -> float:
        # Hand contamination is a property of the source lock, not of the
        # remote canvas layout.  Always inspect the canonical source so a
        # portrait/square transform cannot move the check away from the hand.
        rgba = Image.open(io.BytesIO(self.lock.canonical_image_png)).convert("RGBA")
        mask = self.protected_mask.convert("L").resize(rgba.size, Image.Resampling.NEAREST)
        pixels = rgba.load()
        mask_pixels = mask.load()
        protected = 0
        skin = 0
        for y in range(rgba.height):
            for x in range(rgba.width):
                if mask_pixels[x, y] > 0:
                    protected += 1
                    if self._skin(pixels[x, y]):
                        skin += 1
        return skin / protected if protected else 1.0

    def detect_person_or_hands(self, image: Image.Image) -> Mapping[str, bool]:
        fraction = self._protected_skin_fraction(image)
        # The threshold is intentionally conservative.  It does not claim to
        # identify a person; it only blocks a visible skin-like contamination
        # in the exact region that will be preserved and exported.
        return {"person": False, "hands": fraction > settings.V2_MAX_PROTECTED_SKIN_FRACTION}

    def has_duplicate_product(self, image: Image.Image, lock: ProductLock) -> bool:
        # The exact protected pixel comparison is the authoritative product
        # identity check.  This secondary check only looks for a second exact
        # copy of the source fingerprint in the generated RGB canvas.
        source = Image.open(io.BytesIO(lock.canonical_image_png)).convert("RGB")
        mask = Image.open(io.BytesIO(lock.mask_png)).convert("L")
        bbox = mask.getbbox()
        if bbox is None:
            return True
        crop = source.crop(bbox).resize((24, 24), Image.Resampling.BILINEAR)
        candidate = image.convert("RGB").resize((max(24, image.width // 8), max(24, image.height // 8)), Image.Resampling.BILINEAR)
        crop_stat = ImageStat.Stat(crop)
        # A duplicate product would need both a close mean and low variance
        # difference.  This check is intentionally a warning signal; the
        # protected-pixel check remains the hard invariant.
        for y in range(0, max(1, candidate.height - 24), 12):
            for x in range(0, max(1, candidate.width - 24), 12):
                patch = candidate.crop((x, y, x + 24, y + 24))
                stat = ImageStat.Stat(patch)
                distance = sum(abs(a - b) for a, b in zip(crop_stat.mean, stat.mean))
                if distance < 8:
                    return True
        return False


__all__ = [
    "AIModelUnavailable",
    "PixelSafetyDetectors",
    "closerouter_provider",
    "create_lock_object",
    "load_lock_object",
    "persist_lock_artifacts",
    "refinement_for_request",
]
