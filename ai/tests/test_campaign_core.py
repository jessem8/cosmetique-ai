from __future__ import annotations

from PIL import Image

from ai.campaign_core import (
    FORMATS,
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
