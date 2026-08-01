from __future__ import annotations

from app.services.copy_projection import project_copy_for_browser


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
    assert "• Fraîcheur longue durée" in projected["facebook"]["text"]
    assert projected["linkedin"]["hashtags"] == ["#Rexona"]
    assert projected["instagram"]["claims"] == []
