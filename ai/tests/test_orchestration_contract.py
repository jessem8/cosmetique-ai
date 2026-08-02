from __future__ import annotations

import io
import json
from zipfile import ZipFile

from PIL import Image

from ai import colab_cosmetic_poster_pipeline as pipeline


def _source_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 230, 230)).save(output, format="PNG")
    return output.getvalue()


def test_generate_campaign_zip_orchestrates_square_and_wide_scenes(monkeypatch) -> None:
    scene_calls: list[tuple[tuple[int, int], int]] = []
    prepare_calls: list[tuple[int, int]] = []
    render_calls: list[tuple[str, tuple[int, int]]] = []
    copy_calls: list[str] = []

    cutout = Image.new("RGBA", (64, 64), (20, 100, 140, 255))
    product_mask = Image.new("L", (64, 64), 255)

    def fake_read_label(image: Image.Image, *, roi_images=()):
        assert len(roi_images) == 1
        return {"text": "Rexona Shower Fresh", "items": [{"text": "Rexona", "confidence": 0.99}]}

    def fake_metadata(ocr, overrides=None):
        return {
            "brand": "Rexona",
            "product_name": "Shower Fresh",
            "category": "deodorant",
            "tone": "premium",
        }

    def fake_copy(ocr, metadata, tone="premium", language="fr"):
        copy_calls.append(language)
        return {
            "brand": metadata["brand"],
            "product_name": metadata["product_name"],
            "category": metadata["category"],
            "titre": "Fraîcheur au quotidien",
            "sous_titre": "Une sensation propre et légère.",
            "bullets": [],
            "cta": "Découvrir",
            "hashtags": [],
            "_meta": {"ocr_text": ocr["text"], "source": "ocr+metadata"},
        }

    def fake_prepare(cutout_image: Image.Image, canvas_size: tuple[int, int]):
        prepare_calls.append(canvas_size)
        base = Image.new("RGB", canvas_size, (220, 230, 230))
        mask = Image.new("L", canvas_size, 255)
        product = Image.new("RGBA", (20, 40), (20, 100, 140, 255))
        return base, mask, product, (100, 100)

    def fake_environment(base: Image.Image, mask: Image.Image, category: str, seed: int = 42):
        scene_calls.append((base.size, seed))
        return base.copy()

    def fake_render(scene, product, copy, platform, *, source_product_position=None):
        render_calls.append((platform, scene.size))
        return Image.new("RGB", pipeline.FORMATS[platform], (20, 100, 140))

    monkeypatch.setattr(pipeline, "read_product_label", fake_read_label)
    monkeypatch.setattr(pipeline, "remove_product_background", lambda image: (cutout, product_mask))
    monkeypatch.setattr(pipeline, "evaluate_mask_quality", lambda mask, target_source: {"target_source": target_source})
    monkeypatch.setattr(pipeline, "infer_product_metadata", fake_metadata)
    monkeypatch.setattr(pipeline, "generate_marketing_copy", fake_copy)
    monkeypatch.setattr(pipeline, "prepare_inpainting_inputs", fake_prepare)
    monkeypatch.setattr(pipeline, "generate_environment", fake_environment)
    monkeypatch.setattr(pipeline, "render_poster", fake_render)

    archive = pipeline.generate_campaign_zip(
        _source_png(),
        seed=42,
        language="fr",
        request_context={"generation_id": "g-1", "request_id": "r-1"},
    )

    assert scene_calls == [((1024, 1024), 42), ((1216, 640), 43)]
    assert prepare_calls == [(1024, 1024), (1216, 640)]
    assert render_calls == [
        ("instagram", (1024, 1024)),
        ("facebook", (1216, 640)),
        ("linkedin", (1216, 640)),
    ]
    assert copy_calls == ["fr"]

    with ZipFile(io.BytesIO(archive)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))

    assert manifest["pipeline_version"] == "1.2.0"
    assert manifest["language"] == "fr"
    assert manifest["copy_language"] == "fr"
    assert manifest["scenes"] == {
        "square": {"dimensions": [1024, 1024], "seed": 42},
        "wide": {"dimensions": [1216, 640], "seed": 43},
    }
