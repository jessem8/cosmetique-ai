from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from .errors import ProductPixelInvariantError, TextLayoutError
from .schemas import AssetFormat, CopyPayload, PlatformCopy


PLATFORM_DIMENSIONS: dict[AssetFormat, tuple[int, int]] = {
    AssetFormat.INSTAGRAM: (1080, 1080),
    AssetFormat.FACEBOOK: (1200, 630),
    AssetFormat.LINKEDIN: (1200, 627),
}

_PRODUCT_BOUNDS: dict[AssetFormat, tuple[float, float]] = {
    AssetFormat.INSTAGRAM: (0.42, 0.62),
    AssetFormat.FACEBOOK: (0.36, 0.78),
    AssetFormat.LINKEDIN: (0.36, 0.78),
}

_PRODUCT_CENTERS: dict[AssetFormat, tuple[float, float]] = {
    AssetFormat.INSTAGRAM: (0.73, 0.58),
    AssetFormat.FACEBOOK: (0.25, 0.51),
    AssetFormat.LINKEDIN: (0.25, 0.51),
}

_TEXT_REGIONS: dict[AssetFormat, tuple[int, int, int, int]] = {
    AssetFormat.INSTAGRAM: (56, 72, 500, 520),
    AssetFormat.FACEBOOK: (590, 58, 1140, 572),
    AssetFormat.LINKEDIN: (590, 56, 1140, 569),
}
_FONT_PATH = (
    Path(__file__).parent
    / "assets"
    / "fonts"
    / "Manrope-VariableFont_wght.ttf"
)
_FONT_SHA256 = "d0639be45d0af36e798172419d7bd173c4bd4f29e2b76cbb69db1d11bf8b0a40"
_TEXT_COLOR = (37, 35, 41)
_PANEL_COLOR = (250, 248, 246)
_ACCENT_COLOR = (155, 27, 77)
MAX_EXPORTED_ROI_MAE = 8.0
MIN_EXPORTED_ROI_PSNR_DB = 28.0


@dataclass(frozen=True, slots=True)
class CompositionAsset:
    image: Image.Image
    placed_cutout: Image.Image
    placed_mask: Image.Image
    position: tuple[int, int]
    text_region: tuple[int, int, int, int] | None = None
    contrast_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class ExportedROIQuality:
    mean_absolute_error: float
    psnr_db: float
    compared_pixels: int


def _crop_for_placement(
    cutout: Image.Image,
    mask: Image.Image,
) -> tuple[Image.Image, Image.Image]:
    binary = mask.convert("L").point(lambda value: 255 if value >= 128 else 0)
    bbox = binary.getbbox()
    if bbox is None:
        raise ValueError("accepted mask is empty")
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    padding = max(2, round(max(width, height) * 0.04))
    padded = (
        max(0, bbox[0] - padding),
        max(0, bbox[1] - padding),
        min(mask.width, bbox[2] + padding),
        min(mask.height, bbox[3] + padding),
    )
    return cutout.crop(padded), mask.crop(padded)


def _fit_product(
    cutout: Image.Image,
    mask: Image.Image,
    *,
    canvas_size: tuple[int, int],
    bounds: tuple[float, float],
) -> tuple[Image.Image, Image.Image]:
    max_width = max(1, round(canvas_size[0] * bounds[0]))
    max_height = max(1, round(canvas_size[1] * bounds[1]))
    scale = min(max_width / cutout.width, max_height / cutout.height)
    size = (
        max(1, round(cutout.width * scale)),
        max(1, round(cutout.height * scale)),
    )
    return (
        cutout.resize(size, Image.Resampling.LANCZOS),
        mask.resize(size, Image.Resampling.LANCZOS),
    )


def _validate_inputs(
    background: Image.Image, cutout: Image.Image, mask: Image.Image
) -> tuple[Image.Image, Image.Image, Image.Image]:
    if background.width <= 0 or background.height <= 0:
        raise ValueError("background dimensions must be positive")
    if cutout.width <= 0 or cutout.height <= 0:
        raise ValueError("cutout dimensions must be positive")
    if cutout.size != mask.size:
        raise ValueError("cutout and accepted mask dimensions must match")
    return background.convert("RGB"), cutout.convert("RGBA"), mask.convert("L")


def compose_campaign_assets(
    background: Image.Image,
    cutout: Image.Image,
    mask: Image.Image,
) -> dict[AssetFormat, CompositionAsset]:
    """Compose three platform assets without filtering the placed product."""

    background, cutout, mask = _validate_inputs(background, cutout, mask)
    accepted_alpha = ImageChops.multiply(cutout.getchannel("A"), mask)
    accepted_cutout = cutout.copy()
    accepted_cutout.putalpha(accepted_alpha)
    placement_cutout, placement_mask = _crop_for_placement(
        accepted_cutout,
        mask,
    )

    assets: dict[AssetFormat, CompositionAsset] = {}
    for platform in AssetFormat:
        size = PLATFORM_DIMENSIONS[platform]
        canvas = ImageOps.fit(
            background,
            size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).convert("RGBA")
        placed_cutout, placed_mask = _fit_product(
            placement_cutout,
            placement_mask,
            canvas_size=size,
            bounds=_PRODUCT_BOUNDS[platform],
        )
        placed_alpha = ImageChops.multiply(
            placed_cutout.getchannel("A"), placed_mask
        )
        placed_cutout.putalpha(placed_alpha)

        center_x, center_y = _PRODUCT_CENTERS[platform]
        x = round(size[0] * center_x - placed_cutout.width / 2)
        y = round(size[1] * center_y - placed_cutout.height / 2)
        if x < 0 or y < 0 or x + placed_cutout.width > size[0] or y + placed_cutout.height > size[1]:
            raise ValueError("deterministic product layout exceeds canvas")

        canvas.alpha_composite(placed_cutout, (x, y))
        final = canvas.convert("RGB")
        asset = CompositionAsset(
            image=final,
            placed_cutout=placed_cutout,
            placed_mask=placed_mask,
            position=(x, y),
        )
        assert_product_pixels_unchanged(
            final,
            placed_cutout,
            placed_mask,
            (x, y),
            erosion_radius=1,
        )
        assets[platform] = asset
    return assets


def _relative_luminance(color: tuple[int, int, int]) -> float:
    channels = []
    for value in color:
        channel = value / 255
        channels.append(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(
    foreground: tuple[int, int, int], background: tuple[int, int, int]
) -> float:
    lighter = max(_relative_luminance(foreground), _relative_luminance(background))
    darker = min(_relative_luminance(foreground), _relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


@lru_cache(maxsize=1)
def validate_font_asset() -> Path:
    if not _FONT_PATH.is_file():
        raise TextLayoutError(f"bundled Manrope font is missing: {_FONT_PATH.name}")
    digest = hashlib.sha256(_FONT_PATH.read_bytes()).hexdigest()
    if digest != _FONT_SHA256:
        raise TextLayoutError("bundled Manrope font checksum does not match policy")
    return _FONT_PATH


@lru_cache(maxsize=16)
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(validate_font_asset()), size=size)
    except OSError as exc:
        raise TextLayoutError("bundled Manrope font cannot be loaded") from exc


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines():
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        if draw.textlength(current, font=font) > max_width:
            raise TextLayoutError("copy contains a word too wide for the safe region")
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
                if draw.textlength(current, font=font) > max_width:
                    raise TextLayoutError(
                        "copy contains a word too wide for the safe region"
                    )
        lines.append(current)
    return lines


def _fit_copy(
    draw: ImageDraw.ImageDraw,
    platform_copy: PlatformCopy,
    region: tuple[int, int, int, int],
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    padding = 30
    available_width = region[2] - region[0] - 2 * padding
    available_height = region[3] - region[1] - 2 * padding
    for font_size in range(42, 21, -2):
        font = _load_font(font_size)
        lines = _wrap_text(draw, platform_copy.text, font, available_width)
        line_height = max(
            font_size + 8,
            draw.textbbox((0, 0), "Ag", font=font)[3] + 8,
        )
        if len(lines) * line_height <= available_height:
            return font, lines, line_height
    raise TextLayoutError("verified copy cannot fit the reserved platform safe region")


def render_campaign_copy(
    assets: dict[AssetFormat, CompositionAsset],
    copy: CopyPayload,
) -> dict[AssetFormat, CompositionAsset]:
    """Render only validated copy in protected, high-contrast safe regions."""

    platform_copy = {
        AssetFormat.INSTAGRAM: copy.instagram,
        AssetFormat.FACEBOOK: copy.facebook,
        AssetFormat.LINKEDIN: copy.linkedin,
    }
    contrast = _contrast_ratio(_TEXT_COLOR, _PANEL_COLOR)
    if contrast < 4.5:
        raise TextLayoutError("copy palette does not meet WCAG AA contrast")

    rendered: dict[AssetFormat, CompositionAsset] = {}
    for platform in AssetFormat:
        source = assets[platform]
        region = _TEXT_REGIONS[platform]
        product_mask = Image.new("L", source.image.size, 0)
        product_mask.paste(source.placed_mask, source.position)
        if product_mask.crop(region).getbbox() is not None:
            raise TextLayoutError(
                f"{platform.value} copy region overlaps the protected product mask"
            )

        image = source.image.copy()
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(region, radius=22, fill=_PANEL_COLOR)
        draw.rounded_rectangle(
            (region[0], region[1], region[0] + 9, region[3]),
            radius=4,
            fill=_ACCENT_COLOR,
        )
        font, lines, line_height = _fit_copy(
            draw, platform_copy[platform], region
        )
        x = region[0] + 30
        y = region[1] + 30
        for line in lines:
            draw.text((x, y), line, font=font, fill=_TEXT_COLOR)
            y += line_height

        assert_product_pixels_unchanged(
            image,
            source.placed_cutout,
            source.placed_mask,
            source.position,
            erosion_radius=1,
        )
        rendered[platform] = CompositionAsset(
            image=image,
            placed_cutout=source.placed_cutout,
            placed_mask=source.placed_mask,
            position=source.position,
            text_region=region,
            contrast_ratio=contrast,
        )
    return rendered


def assert_product_pixels_unchanged(
    final: Image.Image,
    placed_cutout: Image.Image,
    placed_mask: Image.Image,
    position: tuple[int, int],
    *,
    erosion_radius: int = 1,
) -> None:
    if erosion_radius < 0:
        raise ValueError("erosion radius must be non-negative")
    if placed_cutout.size != placed_mask.size:
        raise ValueError("placed cutout and mask dimensions must match")
    x, y = position
    if x < 0 or y < 0 or x + placed_cutout.width > final.width or y + placed_cutout.height > final.height:
        raise ValueError("placed product lies outside final image")

    core_mask = placed_mask.convert("L")
    if erosion_radius:
        core_mask = core_mask.filter(ImageFilter.MinFilter(erosion_radius * 2 + 1))
    opaque_alpha = placed_cutout.getchannel("A").point(
        lambda value: 255 if value == 255 else 0
    )
    core_mask = ImageChops.multiply(core_mask.point(lambda value: 255 if value == 255 else 0), opaque_alpha)
    if core_mask.getbbox() is None:
        raise ProductPixelInvariantError("eroded accepted mask has no opaque pixels")

    actual = final.convert("RGB").crop(
        (x, y, x + placed_cutout.width, y + placed_cutout.height)
    )
    expected = placed_cutout.convert("RGB")
    difference = ImageChops.difference(actual, expected)
    masked_difference = ImageChops.multiply(difference, core_mask.convert("RGB"))
    if masked_difference.getbbox() is not None:
        raise ProductPixelInvariantError(
            "final composition altered product pixels inside the eroded mask"
        )


def measure_exported_product_roi_quality(
    encoded_jpeg: bytes,
    placed_cutout: Image.Image,
    placed_mask: Image.Image,
    position: tuple[int, int],
    *,
    erosion_radius: int = 2,
) -> ExportedROIQuality:
    """
    Measure the unavoidable JPEG loss inside the protected product ROI.

    Exact identity is enforced before encoding. Delivered JPEGs instead receive
    a bounded decoded-domain quality gate, reported as MAE and PSNR.
    """

    try:
        with Image.open(io.BytesIO(encoded_jpeg)) as decoded:
            if decoded.format != "JPEG":
                raise ProductPixelInvariantError(
                    "platform export is not a JPEG"
                )
            final = decoded.convert("RGB")
            final.load()
    except ProductPixelInvariantError:
        raise
    except OSError as exc:
        raise ProductPixelInvariantError(
            "platform JPEG cannot be decoded for ROI quality validation"
        ) from exc

    if placed_cutout.size != placed_mask.size:
        raise ValueError("placed cutout and mask dimensions must match")
    x, y = position
    if (
        x < 0
        or y < 0
        or x + placed_cutout.width > final.width
        or y + placed_cutout.height > final.height
    ):
        raise ValueError("placed product lies outside decoded final image")

    core_mask = placed_mask.convert("L")
    if erosion_radius:
        core_mask = core_mask.filter(
            ImageFilter.MinFilter(erosion_radius * 2 + 1)
        )
    opaque_alpha = placed_cutout.getchannel("A").point(
        lambda value: 255 if value == 255 else 0
    )
    core_mask = ImageChops.multiply(
        core_mask.point(lambda value: 255 if value == 255 else 0),
        opaque_alpha,
    )
    compared_pixels = core_mask.histogram()[255]
    if compared_pixels <= 0:
        raise ProductPixelInvariantError(
            "decoded-export ROI has no protected product pixels"
        )

    actual = final.crop(
        (x, y, x + placed_cutout.width, y + placed_cutout.height)
    )
    expected = placed_cutout.convert("RGB")
    difference = ImageChops.difference(actual, expected)
    absolute_sum = 0
    squared_sum = 0
    for channel in difference.split():
        histogram = channel.histogram(mask=core_mask)
        absolute_sum += sum(value * count for value, count in enumerate(histogram))
        squared_sum += sum(
            value * value * count
            for value, count in enumerate(histogram)
        )
    denominator = compared_pixels * 3
    mae = absolute_sum / denominator
    mse = squared_sum / denominator
    psnr = (
        math.inf
        if mse == 0
        else 20 * math.log10(255 / math.sqrt(mse))
    )
    return ExportedROIQuality(
        mean_absolute_error=mae,
        psnr_db=psnr,
        compared_pixels=compared_pixels,
    )


def assert_exported_product_roi_quality(
    encoded_jpeg: bytes,
    placed_cutout: Image.Image,
    placed_mask: Image.Image,
    position: tuple[int, int],
) -> ExportedROIQuality:
    quality = measure_exported_product_roi_quality(
        encoded_jpeg,
        placed_cutout,
        placed_mask,
        position,
    )
    if (
        quality.mean_absolute_error > MAX_EXPORTED_ROI_MAE
        or quality.psnr_db < MIN_EXPORTED_ROI_PSNR_DB
    ):
        raise ProductPixelInvariantError(
            "decoded platform JPEG exceeds the protected product ROI quality tolerance"
        )
    return quality
