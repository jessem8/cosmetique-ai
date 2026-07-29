from __future__ import annotations

from PIL import Image
import pytest

from ai_core.composition import (
    PLATFORM_DIMENSIONS,
    ProductPixelInvariantError,
    assert_product_pixels_unchanged,
    compose_campaign_assets,
    render_campaign_copy,
)
from ai_core.schemas import (
    AssetFormat,
    CampaignLanguage,
    CopyPayload,
    PlatformCopy,
)


def make_inputs() -> tuple[Image.Image, Image.Image, Image.Image]:
    background = Image.new("RGB", (1024, 1024), (238, 232, 226))
    cutout = Image.new("RGBA", (80, 120), (22, 46, 78, 0))
    mask = Image.new("L", cutout.size, 0)

    for y in range(10, 110):
        for x in range(8, 72):
            cutout.putpixel((x, y), ((x * 3) % 256, (y * 2) % 256, 91, 255))
            mask.putpixel((x, y), 255)
    return background, cutout, mask


def test_composition_produces_all_exact_platform_dimensions() -> None:
    background, cutout, mask = make_inputs()

    assets = compose_campaign_assets(background, cutout, mask)

    assert tuple(assets) == tuple(AssetFormat)
    assert {
        platform: asset.image.size for platform, asset in assets.items()
    } == PLATFORM_DIMENSIONS


def test_composition_is_byte_deterministic_for_same_inputs() -> None:
    background, cutout, mask = make_inputs()

    first = compose_campaign_assets(background, cutout, mask)
    second = compose_campaign_assets(background, cutout, mask)

    assert {
        platform: asset.image.tobytes() for platform, asset in first.items()
    } == {
        platform: asset.image.tobytes() for platform, asset in second.items()
    }


def test_composition_preserves_placed_product_pixels_inside_eroded_mask() -> None:
    background, cutout, mask = make_inputs()

    asset = compose_campaign_assets(background, cutout, mask)[AssetFormat.INSTAGRAM]

    assert_product_pixels_unchanged(
        asset.image,
        asset.placed_cutout,
        asset.placed_mask,
        asset.position,
        erosion_radius=1,
    )


def test_product_pixel_invariant_detects_post_composite_grading() -> None:
    background, cutout, mask = make_inputs()
    asset = compose_campaign_assets(background, cutout, mask)[AssetFormat.FACEBOOK]
    corrupted = asset.image.copy()
    x = asset.position[0] + asset.placed_cutout.width // 2
    y = asset.position[1] + asset.placed_cutout.height // 2
    original = corrupted.getpixel((x, y))
    corrupted.putpixel((x, y), ((original[0] + 1) % 256, original[1], original[2]))

    with pytest.raises(ProductPixelInvariantError):
        assert_product_pixels_unchanged(
            corrupted,
            asset.placed_cutout,
            asset.placed_mask,
            asset.position,
            erosion_radius=1,
        )


def test_verified_copy_renders_in_reserved_high_contrast_regions() -> None:
    background, cutout, mask = make_inputs()
    composed = compose_campaign_assets(background, cutout, mask)
    platform_copy = PlatformCopy(
        text="Maison Exemple\nSérum Éclat\nHydrate la peau\nDécouvrir",
        hashtags=("#serum",),
        claims=(),
    )
    copy = CopyPayload(
        language=CampaignLanguage.FR,
        instagram=platform_copy,
        facebook=platform_copy,
        linkedin=platform_copy,
    )

    rendered = render_campaign_copy(composed, copy)

    for platform, asset in rendered.items():
        assert asset.text_region is not None
        assert asset.contrast_ratio is not None
        assert asset.contrast_ratio >= 4.5
        assert asset.image.tobytes() != composed[platform].image.tobytes()
        assert_product_pixels_unchanged(
            asset.image,
            asset.placed_cutout,
            asset.placed_mask,
            asset.position,
            erosion_radius=1,
        )


def test_copy_region_never_overlaps_the_protected_product_core() -> None:
    background, cutout, mask = make_inputs()
    composed = compose_campaign_assets(background, cutout, mask)
    platform_copy = PlatformCopy(text="Sérum Éclat\nDécouvrir")
    copy = CopyPayload(
        language=CampaignLanguage.FR,
        instagram=platform_copy,
        facebook=platform_copy,
        linkedin=platform_copy,
    )

    rendered = render_campaign_copy(composed, copy)

    for asset in rendered.values():
        assert asset.text_region is not None
        x0, y0, x1, y1 = asset.text_region
        mask_canvas = Image.new("L", asset.image.size, 0)
        mask_canvas.paste(asset.placed_mask, asset.position)
        assert mask_canvas.crop((x0, y0, x1, y1)).getbbox() is None
