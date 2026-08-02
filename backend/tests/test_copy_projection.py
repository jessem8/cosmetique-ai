from __future__ import annotations

from app.services.copy_projection import (
    caption_from_response,
    fallback_caption,
    project_copy_for_browser,
)


def test_strict_copy_is_projected_to_existing_platform_shape() -> None:
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

    projected = project_copy_for_browser(copy)

    assert set(projected) == {"instagram", "facebook", "linkedin"}
    assert projected["instagram"]["text"].startswith("Fraîcheur au quotidien")
    assert "Fraîcheur longue durée" in projected["facebook"]["text"]
    assert projected["linkedin"]["hashtags"] == ["#Rexona"]
    assert projected["instagram"]["claims"] == []


def test_fallback_library_adapts_copy_to_each_platform() -> None:
    copy = {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "titre": "Fraîcheur au quotidien",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": ["Fraîcheur longue durée"],
        "cta": "Découvrir",
        "hashtags": ["#Rexona", "#Fraîcheur"],
    }

    captions = {platform: fallback_caption(copy, platform) for platform in ("instagram", "facebook", "linkedin")}

    assert len({caption["text"] for caption in captions.values()}) == 3
    assert captions["instagram"]["source"] == "library"
    assert captions["linkedin"]["hashtags"] == ["#Rexona", "#Fraîcheur"]


def test_qwen_caption_contract_rejects_unsafe_or_malformed_values() -> None:
    assert caption_from_response({"text": "Découvrir la routine.", "hashtags": ["Rexona"]}, platform="instagram") == {
        "text": "Découvrir la routine.",
        "hashtags": ["#Rexona"],
        "source": "qwen",
    }
    assert caption_from_response({"text": "Cliniquement prouvé", "hashtags": []}, platform="instagram") is None
