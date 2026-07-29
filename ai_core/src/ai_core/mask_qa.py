from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter

from .errors import MaskQualityError
from .geometry import normalized_box_to_pixels
from .schemas import NormalizedBox


@dataclass(frozen=True, slots=True)
class MaskQualityMetrics:
    area_fraction: float
    inside_fraction: float
    bbox_iou: float
    edge_contact_fraction: float


def _intersection_area(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> int:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    return max(0, x1 - x0) * max(0, y1 - y0)


def validate_mask(
    mask: Image.Image,
    target: NormalizedBox,
    *,
    min_area_fraction: float = 0.005,
    max_area_fraction: float = 0.85,
    min_inside_fraction: float = 0.80,
    min_bbox_iou: float = 0.25,
    max_edge_contact_fraction: float = 0.08,
) -> MaskQualityMetrics:
    """Validate a binary product mask against the selected detector box."""

    if mask.width <= 0 or mask.height <= 0:
        raise MaskQualityError("mask dimensions must be positive")
    binary = mask.convert("L").point(lambda value: 255 if value >= 128 else 0)
    bbox = binary.getbbox()
    if bbox is None:
        raise MaskQualityError("accepted mask is empty")

    histogram = binary.histogram()
    opaque_pixels = histogram[255]
    total_pixels = binary.width * binary.height
    area_fraction = opaque_pixels / total_pixels
    if not min_area_fraction <= area_fraction <= max_area_fraction:
        raise MaskQualityError(
            f"mask area fraction {area_fraction:.4f} is outside quality bounds"
        )

    target_pixels = normalized_box_to_pixels(
        target, image_width=binary.width, image_height=binary.height
    )
    target_bbox = (
        target_pixels.x_min,
        target_pixels.y_min,
        target_pixels.x_max,
        target_pixels.y_max,
    )
    intersection = _intersection_area(bbox, target_bbox)
    target_crop = binary.crop(target_bbox)
    inside_pixels = target_crop.histogram()[255]
    inside_fraction = inside_pixels / opaque_pixels
    if inside_fraction < min_inside_fraction:
        raise MaskQualityError(
            f"mask lies mostly outside the selected target ({inside_fraction:.3f} inside)"
        )

    bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    target_area = (target_bbox[2] - target_bbox[0]) * (
        target_bbox[3] - target_bbox[1]
    )
    union = bbox_area + target_area - intersection
    bbox_iou = intersection / union if union else 0.0
    if bbox_iou < min_bbox_iou:
        raise MaskQualityError(
            f"mask bounding box IoU {bbox_iou:.3f} is below the quality threshold"
        )

    edge_coordinates = {
        *((x, 0) for x in range(binary.width)),
        *((x, binary.height - 1) for x in range(binary.width)),
        *((0, y) for y in range(binary.height)),
        *((binary.width - 1, y) for y in range(binary.height)),
    }
    edge_pixels = sum(binary.getpixel(coordinate) == 255 for coordinate in edge_coordinates)
    eroded = binary.filter(ImageFilter.MinFilter(3))
    internal_boundary_pixels = ImageChops.difference(
        binary,
        eroded,
    ).histogram()[255]
    boundary_pixels = internal_boundary_pixels + edge_pixels
    edge_contact_fraction = (
        edge_pixels / boundary_pixels if boundary_pixels else 0.0
    )
    if edge_contact_fraction > max_edge_contact_fraction:
        raise MaskQualityError(
            f"mask edge contact {edge_contact_fraction:.3f} exceeds the quality threshold"
        )

    return MaskQualityMetrics(
        area_fraction=area_fraction,
        inside_fraction=inside_fraction,
        bbox_iou=bbox_iou,
        edge_contact_fraction=edge_contact_fraction,
    )
