from __future__ import annotations

import io
import json
import zipfile

import pytest
from PIL import Image

from app.services.artifacts import ArtifactContractError, validate_bundle


FINAL_SIZES = {
    "instagram.jpg": (1080, 1080),
    "facebook.jpg": (1200, 630),
    "linkedin.jpg": (1200, 627),
}


def image_bytes(size: tuple[int, int], fmt: str = "JPEG", mode: str = "RGB") -> bytes:
    stream = io.BytesIO()
    Image.new(mode, size, (230, 235, 235, 255) if mode == "RGBA" else 230).save(
        stream, format=fmt
    )
    return stream.getvalue()


def valid_members() -> dict[str, bytes]:
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
    return {
        **{name: image_bytes(size) for name, size in FINAL_SIZES.items()},
        "copy.json": json.dumps(copy, ensure_ascii=False).encode(),
        "ocr.json": json.dumps(
            {"text": "Rexona Shower Fresh deodorant", "items": []},
            ensure_ascii=False,
        ).encode(),
        "manifest.json": json.dumps(
            {
                "pipeline_version": "1.1.0",
                "runtime_id": "runtime-test",
                "generation_id": "generation-test",
                "request_id": "request-test",
                "input_snapshot_hash": "a" * 64,
                "seed": 42,
                "language": "fr",
                "preserves_product_pixels": True,
            },
            ensure_ascii=False,
        ).encode(),
    }


def bundle(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_colab_zip_contract_accepts_dimensions_copy_and_manifest() -> None:
    result = validate_bundle(
        bundle(valid_members()),
        expected_runtime_id="runtime-test",
        expected_generation_id="generation-test",
        expected_request_id="request-test",
        expected_input_snapshot_hash="a" * 64,
        expected_seed=42,
        expected_language="fr",
    )

    assert result.copy["brand"] == "Rexona"
    assert result.records["instagram.jpg"]["width"] == 1080
    assert result.manifest["preserves_product_pixels"] is True


@pytest.mark.parametrize("field", ["brand", "product_name", "titre", "sous_titre", "cta"])
def test_copy_contract_rejects_placeholder_or_empty_text(field: str) -> None:
    members = valid_members()
    copy = json.loads(members["copy.json"])
    copy[field] = "Maison Exemple" if field == "brand" else ""
    members["copy.json"] = json.dumps(copy, ensure_ascii=False).encode()

    with pytest.raises(ArtifactContractError):
        validate_bundle(bundle(members), expected_runtime_id="runtime-test")


def test_bundle_rejects_wrong_platform_dimensions() -> None:
    members = valid_members()
    members["facebook.jpg"] = image_bytes((1080, 1080))

    with pytest.raises(ArtifactContractError, match="1200x630"):
        validate_bundle(bundle(members), expected_runtime_id="runtime-test")


def test_bundle_rejects_unknown_member() -> None:
    members = valid_members()
    members["debug.txt"] = b"not allowed"

    with pytest.raises(ArtifactContractError):
        validate_bundle(bundle(members), expected_runtime_id="runtime-test")
