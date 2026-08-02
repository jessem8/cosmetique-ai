from __future__ import annotations

import math

import pytest
from PIL import Image, ImageChops
from PIL import ImageFont

from ai import campaign_core
from ai import colab_cosmetic_poster_pipeline as cosmetic_pipeline
from ai.campaign_core import (
    FORMATS,
    _add_contact_shadow,
    _add_surface_reflection,
    _load_font,
    build_inpaint_prompt,
    normalize_category,
    normalize_ocr_text,
    render_poster,
    validate_marketing_copy,
)


def test_ocr_and_category_normalization() -> None:
    assert normalize_ocr_text(" Rexona\nShower   Fresh ") == "Rexona Shower Fresh"
    assert normalize_category("Déodorant") == "deodorant"
    assert normalize_category("solaire") == "sunscreen"


def test_inpaint_prompt_forbids_repainting_packaging() -> None:
    prompt = build_inpaint_prompt("deodorant").casefold()
    for forbidden in ("no text", "no logo", "no fake package", "no extra product", "no vase"):
        assert forbidden in prompt


def test_deodorant_inpaint_prompt_requires_a_visible_category_scene() -> None:
    prompt = build_inpaint_prompt("deodorant").casefold()
    for token in (
        "pale-aqua",
        "shower",
        "wall and counter surface",
        "condensation",
        "water droplets",
        "folded white towel",
        "subtle depth",
        "never a blank white studio",
        "featureless backdrop",
        "flat gradient",
    ):
        assert token in prompt


def test_sdxl_negative_prompt_rejects_blank_environment(monkeypatch) -> None:
    captured = {}

    class FakeTorch:
        class Generator:
            def __init__(self, device):
                pass

            def manual_seed(self, seed):
                return self

        cuda = type("Cuda", (), {})()

    class FakePipeline:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return type("Result", (), {"images": [Image.new("RGB", (32, 32), "white")]})()

    monkeypatch.setitem(__import__("sys").modules, "torch", FakeTorch)
    monkeypatch.setattr(cosmetic_pipeline, "load_sdxl_inpaint_pipeline", lambda: FakePipeline())
    cosmetic_pipeline.generate_environment(Image.new("RGB", (32, 32)), Image.new("L", (32, 32), 255), "deodorant")
    negative = captured["negative_prompt"].casefold()
    assert "blank white background" in negative
    assert "flat gradient" in negative


def test_copy_source_spacing_is_canonicalized() -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Fraîcheur au quotidien",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "OCR + metadata"},
    }
    checked = validate_marketing_copy(copy, copy["_meta"]["ocr_text"], {"brand": "Rexona"})
    assert checked["_meta"]["source"] == "ocr+metadata"


def test_rendered_assets_have_exact_platform_dimensions() -> None:
    background = Image.new("RGB", (1024, 1024), (220, 235, 235))
    product = Image.new("RGBA", (220, 480), (20, 100, 140, 255))
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Fraîcheur au quotidien",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": ["Fraîcheur longue durée"],
        "cta": "Découvrir",
        "hashtags": ["#Rexona"],
        "_meta": {
            "ocr_text": "Rexona Shower Fresh deodorant",
            "source": "ocr+metadata",
        },
    }
    checked = validate_marketing_copy(copy, copy["_meta"]["ocr_text"], {"brand": "Rexona"})
    posters = {
        platform: render_poster(background, product, checked, platform)
        for platform in FORMATS
    }
    assert {name: image.size for name, image in posters.items()} == FORMATS


def test_render_identity_line_changes_when_product_name_changes() -> None:
    background = Image.new("RGB", (1024, 1024), (220, 235, 235))
    product = Image.new("RGBA", (220, 480), (20, 100, 140, 255))
    base_copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Fraîcheur au quotidien",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "ocr+metadata"},
    }
    alternate_copy = dict(base_copy, product_name="Invisible Dry")
    first = render_poster(background, product, base_copy, "instagram").crop((0, 0, 520, 180))
    second = render_poster(background, product, alternate_copy, "instagram").crop((0, 0, 520, 180))

    assert ImageChops.difference(first, second).getbbox() is not None


def test_copy_rejects_unsupported_clinical_claim() -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Résultat clinique",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "ocr+metadata"},
    }
    try:
        validate_marketing_copy(copy, copy["_meta"]["ocr_text"], {"brand": "Rexona"})
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported claim was accepted")


@pytest.mark.parametrize("claim", ["Protection 48H", "Protection 99%", "Protection SPF 50"])
def test_copy_rejects_unverified_numeric_duration_percentage_and_spf_claims(claim: str) -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": claim,
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "ocr+metadata"},
    }

    with pytest.raises(ValueError, match=r"(?i)claim|evidence|verified|ocr"):
        validate_marketing_copy(copy, copy["_meta"]["ocr_text"], {"brand": "Rexona"})


@pytest.mark.parametrize(
    ("claim", "ocr_text", "category"),
    [
        ("Protection 48H", "Rexona Shower Fresh 48H", "deodorant"),
        ("Protection 99%", "Rexona Shower Fresh 99%", "deodorant"),
        ("Protection SPF 50", "Rexona Solaire SPF 50", "sunscreen"),
    ],
)
def test_copy_permits_numeric_duration_percentage_and_spf_claims_grounded_in_ocr(
    claim: str,
    ocr_text: str,
    category: str,
) -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": category,
        "titre": claim,
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": ocr_text, "source": "ocr+metadata"},
    }

    checked = validate_marketing_copy(copy, ocr_text, {"brand": "Rexona"})

    assert checked["titre"] == claim


@pytest.mark.parametrize("claim", ["Protection 48H", "Protection 99%", "Protection SPF 50"])
def test_copy_permits_numeric_claims_from_user_verified_metadata(claim: str) -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": claim,
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "ocr+metadata"},
    }

    checked = validate_marketing_copy(
        copy,
        copy["_meta"]["ocr_text"],
        {"brand": "Rexona", "verified_claims": [claim]},
    )

    assert checked["titre"] == claim


def test_copy_permits_spf_claim_verified_by_metadata_product_name() -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Solaire SPF 50",
        "category": "sunscreen",
        "titre": "Protection SPF 50",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Solaire", "source": "ocr+metadata"},
    }

    checked = validate_marketing_copy(
        copy,
        copy["_meta"]["ocr_text"],
        {"brand": "Rexona", "product_name": "Solaire SPF 50"},
    )

    assert checked["titre"] == "Protection SPF 50"


def test_missing_bundled_font_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing bundled font must not silently fall back to Pillow's bitmap font."""

    missing = "C:/definitely-missing-cosmetique-font.ttf"
    monkeypatch.setattr(campaign_core, "_FONT_CANDIDATES", {"regular": [missing]})

    with pytest.raises(Exception, match=r"(?i)font"):
        # Use a size not touched by the poster-fit loops so a future font
        # cache cannot mask a missing-file regression.
        _load_font(137, "regular")


def test_french_glyphs_use_a_freetype_font() -> None:
    font = _load_font(30, "regular")

    assert isinstance(font, ImageFont.FreeTypeFont)
    assert font.getbbox("Été crème") is not None


def test_source_scene_product_stays_inside_five_percent_safe_margins() -> None:
    background = Image.new("RGB", (1024, 1024), (220, 235, 235))
    product = Image.new("RGBA", (180, 360), (235, 24, 28, 255))
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Fraîcheur au quotidien",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "ocr+metadata"},
    }
    checked = validate_marketing_copy(copy, copy["_meta"]["ocr_text"], {"brand": "Rexona"})

    # This position is valid in the 1024px source scene, but the landscape
    # crop and square scaling would clip it without a platform-safe clamp.
    source_position = (820, 520)
    violations = []
    for platform, (width, height) in FORMATS.items():
        poster = render_poster(
            background,
            product,
            checked,
            platform,
            source_product_position=source_position,
        )
        colored = [
            (x, y)
            for y in range(height)
            for x in range(width)
            if poster.getpixel((x, y))[0] > 180
            and poster.getpixel((x, y))[1] < 80
            and poster.getpixel((x, y))[2] < 80
        ]
        assert colored, f"{platform} poster lost the product entirely"
        xs = [point[0] for point in colored]
        ys = [point[1] for point in colored]
        bbox = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
        margin_x = round(width * 0.05)
        margin_y = round(height * 0.05)
        if not (
            bbox[0] >= margin_x
            and bbox[1] >= margin_y
            and bbox[2] <= width - margin_x
            and bbox[3] <= height - margin_y
        ):
            violations.append((platform, bbox, (margin_x, margin_y)))

    assert not violations, f"product escaped platform safe margins: {violations}"


def test_contact_shadow_is_a_bottom_footprint_not_the_alpha_silhouette() -> None:
    canvas = Image.new("RGBA", (320, 360), (255, 255, 255, 255))
    product = Image.new("RGBA", (100, 180), (230, 20, 20, 255))
    position = (110, 80)

    _add_contact_shadow(canvas, product, position)

    upper_values = [
        canvas.getpixel((x, y))[0]
        for y in range(position[1], position[1] + int(product.height * 0.55))
        for x in range(position[0], position[0] + product.width)
    ]
    lower_values = [
        canvas.getpixel((x, y))[0]
        for y in range(position[1] + product.height - 12, position[1] + product.height + 36)
        for x in range(position[0], position[0] + product.width)
    ]

    assert min(upper_values) >= 250
    assert min(lower_values) < 245


def test_surface_reflection_is_low_and_confined_below_product_footprint() -> None:
    canvas = Image.new("RGBA", (320, 360), (255, 255, 255, 255))
    product = Image.new("RGBA", (100, 180), (230, 20, 20, 255))
    position = (110, 80)

    _add_surface_reflection(canvas, product, position)

    # Reflection must never tint the package itself or the scene above it.
    for y in range(0, position[1] + product.height):
        assert all(canvas.getpixel((x, y))[:3] == (255, 255, 255) for x in range(320))

    changed_rows = [
        y
        for y in range(position[1] + product.height, canvas.height)
        if any(canvas.getpixel((x, y))[:3] != (255, 255, 255) for x in range(canvas.width))
    ]
    assert changed_rows
    assert max(changed_rows) - (position[1] + product.height) + 1 <= round(product.height * 0.10)


def test_requested_french_copy_rejects_clearly_english_creative() -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Freshness every day",
        "sous_titre": "Feel clean and light.",
        "bullets": [],
        "cta": "Discover",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "ocr+metadata"},
    }

    with pytest.raises(ValueError, match=r"(?i)french|language|français"):
        validate_marketing_copy(
            copy,
            copy["_meta"]["ocr_text"],
            {"brand": "Rexona", "language": "fr"},
        )


def test_requested_french_copy_accepts_evidence_grounded_french() -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Fraîcheur au quotidien",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": ["Fraîcheur longue durée"],
        "cta": "Découvrir",
        "hashtags": ["#Rexona"],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "ocr+metadata"},
    }

    checked = validate_marketing_copy(
        copy,
        copy["_meta"]["ocr_text"],
        {"brand": "Rexona", "language": "fr"},
    )

    assert checked["titre"] == "Fraîcheur au quotidien"
    assert checked["sous_titre"] == "Une sensation propre et légère."


@pytest.mark.parametrize(
    ("ocr_text", "expected"),
    [
        ("Rexona SPF 50", "sunscreen"),
        ("Nivea shower gel", "bodycare"),
        ("Nivea hand cream", "bodycare"),
        ("Veet wax", "bodycare"),
        ("Labello lip balm", "bodycare"),
        ("Dove wipes", "bodycare"),
        ("Dove body mist", "bodycare"),
        ("L'Oréal hair oil", "haircare"),
    ],
)
def test_category_inference_covers_common_cosmetic_product_terms(ocr_text: str, expected: str) -> None:
    assert cosmetic_pipeline.infer_category_from_text(ocr_text) == expected


def _normalize_target_box(value):
    validator = getattr(cosmetic_pipeline, "normalize_target_box", None)
    assert callable(validator), "normalize_target_box contract is missing"
    return validator(value)


def test_normalized_target_box_accepts_valid_geometry() -> None:
    value = {"type": "box", "x": 0.12, "y": 0.18, "width": 0.42, "height": 0.63}

    assert _normalize_target_box(value) == value


@pytest.mark.parametrize(
    "value",
    [
        {"type": "box", "x": 0.8, "y": 0.1, "width": 0.3, "height": 0.2},
        {"type": "box", "x": 0.1, "y": 0.8, "width": 0.2, "height": 0.3},
        {"type": "box", "x": 0.1, "y": 0.1, "width": 0, "height": 0.4},
        {"type": "box", "x": 0.1, "y": 0.1, "width": 0.4, "height": 0},
        {"type": "box", "x": math.nan, "y": 0.1, "width": 0.4, "height": 0.4},
        {"type": "box", "x": 0.1, "y": math.inf, "width": 0.4, "height": 0.4},
        {"type": "box", "x": 0.1, "y": 0.1, "width": -math.inf, "height": 0.4},
    ],
)
def test_normalized_target_box_rejects_outside_zero_and_non_finite_geometry(value) -> None:
    validator = getattr(cosmetic_pipeline, "normalize_target_box", None)
    assert callable(validator), "normalize_target_box contract is missing"

    with pytest.raises(ValueError):
        validator(value)


@pytest.mark.parametrize("platform", tuple(FORMATS))
def test_poster_composition_preserves_opaque_product_pixels_before_jpeg(platform: str) -> None:
    width, height = FORMATS[platform]
    background = Image.new("RGB", (width, height), (220, 235, 235))
    product_color = (9, 220, 145)
    product = Image.new("RGBA", (180, 260), (*product_color, 255))
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Fraîcheur au quotidien",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": ["Fraîcheur longue durée"],
        "cta": "Découvrir",
        "hashtags": [],
        "_meta": {"ocr_text": "Rexona Shower Fresh", "source": "ocr+metadata"},
    }
    checked = validate_marketing_copy(copy, copy["_meta"]["ocr_text"], {"brand": "Rexona"})
    source_position = (round(width * 0.06), round(height * 0.06))
    poster = render_poster(
        background,
        product,
        checked,
        platform,
        source_product_position=source_position,
    )

    for y in range(source_position[1] + 2, source_position[1] + product.height - 2):
        for x in range(source_position[0] + 2, source_position[0] + product.width - 2):
            assert poster.getpixel((x, y)) == product_color
