from __future__ import annotations

import io
import hashlib
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
        "cutout.png": image_bytes((64, 64), fmt="PNG", mode="RGBA"),
        "mask.png": image_bytes((64, 64), fmt="PNG", mode="L"),
        "background.jpg": image_bytes((1024, 1024)),
        "copy.json": json.dumps(copy, ensure_ascii=False).encode(),
        "ocr.json": json.dumps(
            {"text": "Rexona Shower Fresh deodorant", "items": []},
            ensure_ascii=False,
        ).encode(),
        "manifest.json": json.dumps(
            {
                "pipeline_version": "1.2.0",
                "runtime_id": "runtime-test",
                "generation_id": "generation-test",
                "request_id": "request-test",
                "input_snapshot_hash": "a" * 64,
                "seed": 42,
                "language": "fr",
                "copy_language": "fr",
                "text_rendering": "Pillow",
                "target_box_source": "automatic",
                "target_box": None,
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


def test_bundle_repairs_observed_rexona_ocr_creative_before_storage() -> None:
    members = valid_members()
    copy = json.loads(members["copy.json"])
    copy.update(
        {
            "titre": "Rexona Shower Fresh Shower Fresh",
            "sous_titre": "48H MOTION ACIVATED PROTECTION",
            "bullets": ["OZ Alco"],
        }
    )
    members["copy.json"] = json.dumps(copy, ensure_ascii=False).encode()

    result = validate_bundle(
        bundle(members),
        expected_runtime_id="runtime-test",
        expected_product={
            "brand": "Rexona",
            "name": "Shower Fresh",
            "category": "deodorant",
        },
        expected_language="fr",
    )

    assert result.copy["titre"] == "Fraîcheur au quotidien"
    assert result.copy["bullets"] == []
    assert result.copy["_meta"]["finalized_by"] == "backend-deterministic-v1"
    assert result.manifest["backend_finalization"]["typography"] == "Pillow"
    assert "ACIVATED" not in json.loads(result.members["copy.json"])["sous_titre"]


def test_poster_finalization_replaces_left_copy_veil_and_keeps_right_region() -> None:
    members = valid_members()
    copy = json.loads(members["copy.json"])
    copy.update({"titre": "Rexona Shower Fresh Shower Fresh", "sous_titre": "48H MOTION ACIVATED PROTECTION", "bullets": ["OZ Alco"]})
    members["copy.json"] = json.dumps(copy, ensure_ascii=False).encode()
    # Use a textured poster so JPEG re-encoding drift is measured rather than
    # hidden by a flat-color fixture.
    textured = Image.new("RGB", (1080, 1080))
    pixels = textured.load()
    for y in range(1080):
        for x in range(1080):
            pixels[x, y] = (x % 251, y % 251, (x + y) % 251)
    encoded = io.BytesIO()
    textured.save(encoded, format="JPEG", quality=95, optimize=True)
    members["instagram.jpg"] = encoded.getvalue()
    original = Image.open(io.BytesIO(members["instagram.jpg"])).convert("RGB")
    result = validate_bundle(bundle(members), expected_language="fr", expected_product={"brand": "Rexona", "name": "Shower Fresh", "category": "deodorant"})
    finalized = Image.open(io.BytesIO(result.members["instagram.jpg"])).convert("RGB")

    assert result.members["instagram.jpg"] != members["instagram.jpg"]
    assert finalized.getpixel((100, 100)) != original.getpixel((100, 100))
    protected_x = int(original.width * 0.60)
    errors = []
    for x in range(protected_x, original.width, 37):
        for y in range(0, original.height, 53):
            before = original.getpixel((x, y))
            after = finalized.getpixel((x, y))
            errors.append(sum(abs(a - b) for a, b in zip(before, after)) / 3)
    assert sum(errors) / len(errors) < 8


def test_bundle_requires_all_pipeline_1_2_evidence_members() -> None:
    members = valid_members()
    del members["mask.png"]
    with pytest.raises(ArtifactContractError, match="mask.png"):
        validate_bundle(bundle(members), expected_runtime_id="runtime-test")


def test_finalized_zip_contains_the_same_repaired_members_used_for_storage() -> None:
    members = valid_members()
    copy = json.loads(members["copy.json"])
    copy.update({"titre": "Rexona Shower Fresh Shower Fresh", "sous_titre": "48H MOTION ACIVATED PROTECTION", "bullets": ["OZ Alco"]})
    members["copy.json"] = json.dumps(copy, ensure_ascii=False).encode()
    result = validate_bundle(
        bundle(members),
        expected_language="fr",
        expected_product={"brand": "Rexona", "name": "Shower Fresh", "category": "deodorant"},
    )
    with zipfile.ZipFile(io.BytesIO(result.finalized_bundle)) as archive:
        assert set(archive.namelist()) == set(result.members)
        assert archive.read("copy.json") == result.members["copy.json"]
        assert archive.read("instagram.jpg") == result.members["instagram.jpg"]
        stored_manifest = json.loads(archive.read("manifest.json"))
    manifest_record = next(item for item in stored_manifest["artifacts"] if item["name"] == "manifest.json")
    assert "sha256" not in manifest_record
    assert result.checksum != hashlib.sha256(bundle(members)).hexdigest()


def test_finalized_zip_is_byte_deterministic() -> None:
    members = valid_members()
    first = validate_bundle(bundle(members), expected_language="fr")
    second = validate_bundle(bundle(members), expected_language="fr")
    assert first.finalized_bundle == second.finalized_bundle
    assert first.checksum == second.checksum
