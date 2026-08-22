"""CPU-only image canonicalization and product-lock processing.

This module intentionally uses Pillow only.  Heavy model adapters return a
mask to :class:`ProductLockProcessor`; all provenance and acceptance decisions
remain deterministic and testable without those adapters.
"""
from __future__ import annotations

import io
import math
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from PIL import Image, ImageChops, ImageOps

from .contracts import (
    MaskMetrics,
    NormalizedBox,
    NormalizedPoint,
    ProductLock,
    ProductLockStatus,
    RefinementContract,
    SegmentationResult,
    canonical_json,
    sha256_bytes,
)


class ImageDecodeError(ValueError):
    """Raised when the source cannot be decoded as a bounded raster image."""


class SuspiciousMaskError(ValueError):
    """Raised only when a caller explicitly asks for strict mask acceptance."""


class Segmenter(Protocol):
    def segment(self, image: Image.Image, *, target_box: NormalizedBox | None = None) -> SegmentationResult | Image.Image:
        ...


@dataclass(frozen=True)
class CanonicalImage:
    image: Image.Image
    png_bytes: bytes
    source_sha256: str
    canonical_sha256: str
    width: int
    height: int


def encode_png(image: Image.Image, *, mode: str | None = None) -> bytes:
    """Encode a metadata-free deterministic PNG.

    ``optimize=False`` avoids version-dependent optimizer heuristics while the
    fixed compression level keeps the output stable for a pinned Pillow build.
    """

    normalized = image.convert(mode) if mode else image
    output = io.BytesIO()
    normalized.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def decode_image(source: bytes | bytearray | memoryview | Image.Image, *, max_pixels: int = 100_000_000) -> Image.Image:
    if isinstance(source, Image.Image):
        image = source.copy()
    else:
        raw = bytes(source)
        try:
            image = Image.open(io.BytesIO(raw))
            image.load()
        except Exception as exc:  # Pillow exposes several decoder exceptions.
            raise ImageDecodeError("source image could not be decoded") from exc
    if image.width <= 0 or image.height <= 0 or image.width * image.height > max_pixels:
        raise ImageDecodeError("source image exceeds the pixel cap")
    try:
        # EXIF orientation is applied once and then metadata is discarded.  All
        # coordinates and masks in V2 refer to this canonical orientation.
        return ImageOps.exif_transpose(image).convert("RGBA")
    except Exception as exc:
        raise ImageDecodeError("source image could not be normalized") from exc


def canonicalize_image(source: bytes | bytearray | memoryview | Image.Image, *, max_pixels: int = 100_000_000) -> CanonicalImage:
    raw = bytes(source) if not isinstance(source, Image.Image) else encode_png(source)
    image = decode_image(source, max_pixels=max_pixels)
    png_bytes = encode_png(image, mode="RGBA")
    return CanonicalImage(
        image=image,
        png_bytes=png_bytes,
        source_sha256=sha256_bytes(raw),
        canonical_sha256=sha256_bytes(png_bytes),
        width=image.width,
        height=image.height,
    )


def _coerce_mask(value: Any, size: tuple[int, int]) -> Image.Image:
    if isinstance(value, Image.Image):
        mask = value.copy()
    elif isinstance(value, (bytes, bytearray, memoryview)):
        try:
            mask = Image.open(io.BytesIO(bytes(value)))
            mask.load()
        except Exception as exc:
            raise ValueError("segmentation mask bytes could not be decoded") from exc
    else:
        # Optional numpy support is local to this conversion; importing numpy
        # here must not make the package dependency-heavy.
        try:
            import numpy as np  # type: ignore

            array = np.asarray(value)
            if array.ndim == 3:
                array = array[..., 0]
            if array.ndim != 2:
                raise ValueError("mask array must be two-dimensional")
            if array.dtype.kind == "f" and float(array.max(initial=0)) <= 1.0:
                array = array * 255
            mask = Image.fromarray(array.astype("uint8"), mode="L")
        except ImportError as exc:
            raise ValueError("mask must be a Pillow image or encoded bytes") from exc
        except Exception as exc:
            raise ValueError("mask array could not be converted") from exc
    if mask.mode in {"RGBA", "LA"}:
        mask = mask.getchannel("A")
    else:
        mask = mask.convert("L")
    if mask.size != size:
        mask = mask.resize(size, Image.Resampling.NEAREST)
    # Canonical masks are binary.  Thresholding before metrics prevents a
    # half-transparent, hand-painted alpha fringe from being treated as a
    # trustworthy product region.
    return mask.point(lambda value: 255 if value >= 128 else 0, mode="L")


def _foreground(mask: Image.Image) -> set[tuple[int, int]]:
    pixels = mask.load()
    return {(x, y) for y in range(mask.height) for x in range(mask.width) if pixels[x, y] > 0}


def _connected_components(mask: Image.Image, foreground: set[tuple[int, int]]) -> tuple[int, list[set[tuple[int, int]]]]:
    remaining = set(foreground)
    components: list[set[tuple[int, int]]] = []
    while remaining:
        start = remaining.pop()
        component = {start}
        stack = [start]
        while stack:
            x, y = stack.pop()
            for neighbour in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.add(neighbour)
                    stack.append(neighbour)
        components.append(component)
    return len(components), components


def _hole_fraction(mask: Image.Image, foreground: set[tuple[int, int]]) -> float:
    if not foreground:
        return 0.0
    width, height = mask.size
    background = {(x, y) for y in range(height) for x in range(width) if (x, y) not in foreground}
    outside: set[tuple[int, int]] = set()
    stack = [(x, y) for x, y in background if x in (0, width - 1) or y in (0, height - 1)]
    while stack:
        point = stack.pop()
        if point in outside or point not in background:
            continue
        outside.add(point)
        x, y = point
        stack.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
    holes = len(background - outside)
    return holes / max(1, len(foreground) + holes)


def mask_metrics(mask: Image.Image, *, target_box: NormalizedBox | None = None, points: RefinementContract | None = None) -> MaskMetrics:
    mask = mask.convert("L").point(lambda value: 255 if value >= 128 else 0, mode="L")
    foreground = _foreground(mask)
    total = mask.width * mask.height
    count = len(foreground)
    coverage = count / max(1, total)
    bbox = None
    if foreground:
        xs = [point[0] for point in foreground]
        ys = [point[1] for point in foreground]
        bbox = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
    components, component_sets = _connected_components(mask, foreground)
    edge = sum(1 for x, y in foreground if x in (0, mask.width - 1) or y in (0, mask.height - 1))
    edge_fraction = edge / max(1, count)
    perimeter = sum(
        1
        for x, y in foreground
        if any(neighbour not in foreground for neighbour in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
    )
    perimeter_fraction = perimeter / max(1, count)
    holes = _hole_fraction(mask, foreground)
    reasons: list[str] = []
    if count == 0:
        reasons.append("empty_mask")
    elif coverage < 0.0005:
        reasons.append("mask_too_small")
    elif coverage > 0.98:
        reasons.append("mask_covers_almost_entire_image")
    # A product lock is a protected, contiguous region.  Even one detached
    # island can be a hand, a second product, or hand-painted contamination;
    # fail closed and let a human or a SAM2 refinement decide.
    if components > 1:
        reasons.append("multiple_components")
    if components > 8:
        reasons.append("too_many_components")
    if edge_fraction > 0.65:
        reasons.append("mask_touches_image_edge")
    if count >= 24 and perimeter_fraction > 0.45:
        reasons.append("irregular_mask_boundary")
    if holes > 0.40:
        reasons.append("excessive_holes")
    if target_box is not None and bbox is not None:
        left = round(target_box.x * mask.width)
        top = round(target_box.y * mask.height)
        right = round((target_box.x + target_box.width) * mask.width)
        bottom = round((target_box.y + target_box.height) * mask.height)
        tolerance = max(2, round(min(mask.width, mask.height) * 0.03))
        if bbox[0] < left - tolerance or bbox[1] < top - tolerance or bbox[2] > right + tolerance or bbox[3] > bottom + tolerance:
            reasons.append("mask_outside_target_box")
    if points is not None:
        pixels = mask.load()
        for point in points.positive_points:
            x = min(mask.width - 1, round(point.x * (mask.width - 1)))
            y = min(mask.height - 1, round(point.y * (mask.height - 1)))
            if pixels[x, y] == 0:
                reasons.append("positive_point_outside_mask")
        for point in points.negative_points:
            x = min(mask.width - 1, round(point.x * (mask.width - 1)))
            y = min(mask.height - 1, round(point.y * (mask.height - 1)))
            if pixels[x, y] > 0:
                reasons.append("negative_point_inside_mask")
    # Keep score conservative: no suspicious condition is the only route to
    # accepted; confidence from a model is handled by ProductLockProcessor.
    penalty = 0.0
    penalty += 0.5 if "empty_mask" in reasons else 0.0
    penalty += 0.3 if "mask_too_small" in reasons or "mask_covers_almost_entire_image" in reasons else 0.0
    penalty += 0.1 * min(3, max(0, components - 1))
    penalty += 0.2 if "mask_touches_image_edge" in reasons else 0.0
    penalty += 0.2 if "irregular_mask_boundary" in reasons else 0.0
    penalty += 0.2 if "excessive_holes" in reasons else 0.0
    penalty += 0.35 if any(reason in reasons for reason in ("mask_outside_target_box", "positive_point_outside_mask", "negative_point_inside_mask")) else 0.0
    score = max(0.0, min(1.0, 1.0 - penalty))
    return MaskMetrics(
        coverage_fraction=coverage,
        bbox=bbox,
        connected_components=components,
        edge_touch_fraction=edge_fraction,
        hole_fraction=holes,
        foreground_pixels=count,
        total_pixels=total,
        score=score,
        suspicious_reasons=tuple(dict.fromkeys(reasons)),
        perimeter_fraction=perimeter_fraction,
    )


def _draw_disc(mask: Image.Image, point: NormalizedPoint, value: int, radius: int) -> None:
    from PIL import ImageDraw

    x = min(mask.width - 1, max(0, round(point.x * (mask.width - 1))))
    y = min(mask.height - 1, max(0, round(point.y * (mask.height - 1))))
    ImageDraw.Draw(mask).ellipse((x - radius, y - radius, x + radius, y + radius), fill=value)


def apply_refinement(mask: Image.Image, refinement: RefinementContract | Mapping[str, Any] | None) -> Image.Image:
    """Apply deterministic point/box refinement to a binary mask.

    This is a CPU-safe fallback and a contract test double, not a replacement
    for SAM2.  Production SAM2 adapters receive the same normalized points and
    box through their predictor interface.
    """

    contract = refinement if isinstance(refinement, RefinementContract) else RefinementContract.from_mapping(refinement)
    output = mask.convert("L").copy()
    radius = max(2, round(min(output.size) * 0.025))
    if contract.box is not None:
        left = round(contract.box.x * output.width)
        top = round(contract.box.y * output.height)
        right = round((contract.box.x + contract.box.width) * output.width)
        bottom = round((contract.box.y + contract.box.height) * output.height)
        box_mask = Image.new("L", output.size, 0)
        from PIL import ImageDraw

        ImageDraw.Draw(box_mask).rectangle((left, top, max(left, right - 1), max(top, bottom - 1)), fill=255)
        output = ImageChops.multiply(output, box_mask)
    for point in contract.positive_points:
        _draw_disc(output, point, 255, radius)
    for point in contract.negative_points:
        _draw_disc(output, point, 0, radius)
    return output.point(lambda value: 255 if value >= 128 else 0, mode="L")


def _mask_with_source_alpha(image: Image.Image, mask: Image.Image) -> Image.Image:
    source_alpha = image.getchannel("A")
    return ImageChops.multiply(mask.convert("L"), source_alpha)


def _provenance_digest(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


class ProductLockProcessor:
    """Create and refine immutable product-lock artifacts."""

    def __init__(
        self,
        segmenter: Segmenter | None = None,
        *,
        min_score: float = 0.65,
        accept_model_confidence: float = 0.55,
        strict: bool = False,
    ) -> None:
        if not 0 <= min_score <= 1 or not 0 <= accept_model_confidence <= 1:
            raise ValueError("mask acceptance thresholds must be in [0, 1]")
        self.segmenter = segmenter
        self.min_score = min_score
        self.accept_model_confidence = accept_model_confidence
        self.strict = strict

    def _resolve_mask(
        self,
        image: Image.Image,
        mask: Any | None,
        *,
        target_box: NormalizedBox | None,
    ) -> tuple[Image.Image | None, float | None, str, str, dict[str, Any]]:
        if mask is not None:
            if isinstance(mask, SegmentationResult):
                return _coerce_mask(mask.mask, image.size), mask.confidence, mask.model, mask.provider, dict(mask.metadata)
            return _coerce_mask(mask, image.size), None, "provided-mask", "caller", {}
        if self.segmenter is None:
            return None, None, "no-segmenter", "none", {}
        result = self.segmenter.segment(image, target_box=target_box)
        if isinstance(result, SegmentationResult):
            return _coerce_mask(result.mask, image.size), result.confidence, result.model, result.provider, dict(result.metadata)
        return _coerce_mask(result, image.size), None, self.segmenter.__class__.__name__, "local", {}

    def create(
        self,
        source: bytes | bytearray | memoryview | Image.Image,
        *,
        mask: Any | None = None,
        refinement: RefinementContract | Mapping[str, Any] | None = None,
        target_box: NormalizedBox | Mapping[str, Any] | None = None,
        source_mime: str = "image/png",
        lock_id: str | None = None,
        revision: int = 0,
        source_sha256: str | None = None,
    ) -> ProductLock:
        if source_mime not in {"image/png", "image/jpeg", "image/webp", "image/avif"}:
            raise ValueError("unsupported source image MIME type")
        canonical = canonicalize_image(source)
        original_source_sha256 = source_sha256 or canonical.source_sha256
        if len(original_source_sha256) != 64 or any(char not in "0123456789abcdef" for char in original_source_sha256):
            raise ValueError("source_sha256 override must be a lowercase SHA-256 digest")
        box = target_box if isinstance(target_box, NormalizedBox) or target_box is None else NormalizedBox.from_mapping(target_box)
        contract = refinement if isinstance(refinement, RefinementContract) else RefinementContract.from_mapping(refinement)
        resolved, confidence, model, provider, metadata = self._resolve_mask(canonical.image, mask, target_box=box)
        reasons: list[str] = []
        if resolved is None:
            reasons.append("no_mask_available")
            empty = Image.new("L", canonical.image.size, 0)
            metrics = mask_metrics(empty, target_box=box, points=contract)
            resolved = empty
            status = ProductLockStatus.ABSTAINED
        else:
            resolved = apply_refinement(resolved, contract)
            metrics = mask_metrics(resolved, target_box=box, points=contract)
            reasons.extend(metrics.suspicious_reasons)
            if confidence is not None and confidence < self.accept_model_confidence:
                reasons.append("low_segmenter_confidence")
            status = ProductLockStatus.NEEDS_REVIEW if reasons or metrics.score < self.min_score else ProductLockStatus.ACCEPTED
            if metrics.foreground_pixels == 0:
                status = ProductLockStatus.ABSTAINED
        mask_png = encode_png(resolved, mode="L")
        cutout = canonical.image.copy()
        cutout.putalpha(_mask_with_source_alpha(canonical.image, resolved))
        cutout_png = encode_png(cutout, mode="RGBA")
        provenance_payload = {
            "schema": "product-lock-v2",
            "revision": revision,
            "source_sha256": original_source_sha256,
            "canonical_image_sha256": canonical.canonical_sha256,
            "mask_sha256": sha256_bytes(mask_png),
            "cutout_sha256": sha256_bytes(cutout_png),
            "width": canonical.width,
            "height": canonical.height,
            "algorithm": "canonical-binary-mask-v1",
            "segmenter": {"provider": provider, "model": model, "confidence": confidence, "metadata": metadata},
            "target_box": box.to_dict() if box else None,
            "refinement": contract.to_dict(),
        }
        provenance = _provenance_digest(provenance_payload)
        output_id = lock_id or provenance[:32]
        lock = ProductLock(
            lock_id=output_id,
            revision=revision,
            status=status,
            source_sha256=original_source_sha256,
            canonical_image_sha256=canonical.canonical_sha256,
            mask_sha256=sha256_bytes(mask_png),
            cutout_sha256=sha256_bytes(cutout_png),
            provenance_sha256=provenance,
            width=canonical.width,
            height=canonical.height,
            canonical_image_png=canonical.png_bytes,
            mask_png=mask_png,
            cutout_png=cutout_png,
            metrics=metrics,
            reasons=tuple(dict.fromkeys(reasons)),
            source_mime=source_mime,
            refinement=contract if refinement is not None else None,
        )
        if self.strict and lock.status is not ProductLockStatus.ACCEPTED:
            raise SuspiciousMaskError(
                f"product lock rejected in strict mode: {', '.join(lock.reasons) or lock.status.value}"
            )
        return lock

    def refine(self, lock: ProductLock, refinement: RefinementContract | Mapping[str, Any]) -> ProductLock:
        """Return a new lock revision; the input lock remains unchanged."""

        image = decode_image(lock.canonical_image_png)
        contract = refinement if isinstance(refinement, RefinementContract) else RefinementContract.from_mapping(refinement)
        refined_mask = apply_refinement(Image.open(io.BytesIO(lock.mask_png)), contract)
        return self.create(
            image,
            mask=refined_mask,
            refinement=contract,
            lock_id=lock.lock_id,
            revision=lock.revision + 1,
            source_mime=lock.source_mime,
            source_sha256=lock.source_sha256,
        )


ProductLockService = ProductLockProcessor


def create_product_lock(*args: Any, **kwargs: Any) -> ProductLock:
    """Functional convenience API retaining the processor's strict gate."""

    return ProductLockProcessor().create(*args, **kwargs)


def refine_product_lock(lock: ProductLock, refinement: RefinementContract | Mapping[str, Any], *, processor: ProductLockProcessor | None = None) -> ProductLock:
    return (processor or ProductLockProcessor()).refine(lock, refinement)


def assert_mask_accepted(lock: ProductLock) -> ProductLock:
    """Fail closed for callers that require a trustworthy lock."""

    if lock.status is not ProductLockStatus.ACCEPTED:
        raise SuspiciousMaskError(f"product lock is {lock.status.value}: {', '.join(lock.reasons) or 'no accepted mask'}")
    return lock


__all__ = [
    "CanonicalImage",
    "ImageDecodeError",
    "ProductLockProcessor",
    "ProductLockService",
    "Segmenter",
    "SuspiciousMaskError",
    "apply_refinement",
    "assert_mask_accepted",
    "canonicalize_image",
    "create_product_lock",
    "decode_image",
    "encode_png",
    "mask_metrics",
    "refine_product_lock",
]
