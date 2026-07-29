from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from ai_core.artifacts import (
    ARTIFACT_ORDER,
    build_manifest,
    canonical_json_bytes,
    create_bundle,
)
from ai_core.model_registry import PINNED_DEPENDENCIES, pinned_model_refs
from ai_core.schemas import (
    CampaignLanguage,
    CopyPayload,
    DependencyRef,
    GenerationStage,
    ModelRef,
    NormalizedBox,
    PlatformCopy,
    StageReceipt,
)
from app.services.artifacts import (
    ArtifactChecksumMismatch,
    ArtifactContractError,
    validate_bundle,
)


FINAL_SIZES = {
    "instagram.jpg": (1080, 1080),
    "facebook.jpg": (1200, 630),
    "linkedin.jpg": (1200, 627),
}


def image_bytes(mode: str, size: tuple[int, int], fmt: str) -> bytes:
    buffer = io.BytesIO()
    color = (240, 240, 240, 255) if mode == "RGBA" else 240
    if mode == "RGB":
        color = (240, 240, 240)
    Image.new(mode, size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def artifact_payloads() -> dict[str, bytes]:
    copy = CopyPayload(
        language=CampaignLanguage.FR,
        instagram=PlatformCopy(text="Éclat quotidien."),
        facebook=PlatformCopy(text="Une campagne fondée sur les faits vérifiés."),
        linkedin=PlatformCopy(text="Création cosmétique responsable."),
    )
    return {
        **{
            name: image_bytes("RGB", size, "JPEG")
            for name, size in FINAL_SIZES.items()
        },
        "copy.json": canonical_json_bytes(copy),
        "cutout.png": image_bytes("RGBA", (8, 10), "PNG"),
        "mask.png": image_bytes("L", (8, 10), "PNG"),
        "background.jpg": image_bytes("RGB", (1024, 1024), "JPEG"),
    }


def valid_bundle(*, runtime_id: str = "runtime-a") -> bytes:
    payloads = artifact_payloads()
    completed_at = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    manifest = build_manifest(
        generation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        request_id="request-a",
        runtime_id=runtime_id,
        input_snapshot_hash="a" * 64,
        seed=42,
        language=CampaignLanguage.FR,
        target_selection=NormalizedBox(
            type="box", x=0.1, y=0.2, width=0.3, height=0.4
        ),
        dependencies=(
            *PINNED_DEPENDENCIES,
            DependencyRef(name="torch", version="2.8.0+cu126"),
            DependencyRef(name="cuda_runtime", version="12.6"),
        ),
        models=pinned_model_refs(),
        stage_receipts=tuple(
            StageReceipt(
                stage=stage,
                completed_at=completed_at + timedelta(seconds=index),
            )
            for index, stage in enumerate(GenerationStage)
        ),
        artifact_payloads=payloads,
    )
    return create_bundle(payloads, manifest)


def read_members(bundle: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def repack(
    members: list[tuple[str, bytes]],
    *,
    symlink_name: str | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (
                (0o120777 if name == symlink_name else 0o100600) << 16
            )
            info.extra = b""
            archive.writestr(info, payload, compresslevel=9)
    return buffer.getvalue()


def test_accepts_the_exact_bundle_emitted_by_shared_ai_core():
    result = validate_bundle(
        valid_bundle(),
        expected_runtime_id="runtime-a",
        expected_generation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        expected_request_id="request-a",
        expected_input_snapshot_hash="a" * 64,
        expected_seed=42,
        expected_language="fr",
        expected_source_size=(8, 10),
        expected_target_selection={
            "type": "box",
            "x": 0.1,
            "y": 0.2,
            "width": 0.3,
            "height": 0.4,
        },
    )
    assert (
        result.manifest["models"]["sdxl"]["revision"]
        == pinned_model_refs()["sdxl"].revision
    )
    assert result.copy["instagram"]["text"] == "Éclat quotidien."
    assert tuple(result.members) == ARTIFACT_ORDER


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation_id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("request_id", "request-b"),
        ("input_snapshot_hash", "c" * 64),
        ("seed", 43),
        ("language", "en"),
    ],
)
def test_bundle_identity_is_bound_to_the_database_job(field, value):
    expectations = {
        "expected_generation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "expected_request_id": "request-a",
        "expected_input_snapshot_hash": "a" * 64,
        "expected_seed": 42,
        "expected_language": "fr",
    }
    expectations[f"expected_{field}"] = value
    with pytest.raises(ArtifactContractError):
        validate_bundle(
            valid_bundle(),
            expected_runtime_id="runtime-a",
            expected_source_size=(8, 10),
            **expectations,
        )


def test_bundle_rejects_runtime_change_and_wrong_source_dimensions():
    with pytest.raises(ArtifactContractError):
        validate_bundle(
            valid_bundle(),
            expected_runtime_id="runtime-b",
            expected_source_size=(8, 10),
        )
    with pytest.raises(ArtifactContractError):
        validate_bundle(
            valid_bundle(),
            expected_runtime_id="runtime-a",
            expected_source_size=(99, 99),
        )


def test_bundle_rejects_checksum_mismatch():
    members = read_members(valid_bundle())
    members["instagram.jpg"] += b"tampered"
    with pytest.raises(ArtifactChecksumMismatch):
        validate_bundle(
            repack(list(members.items())),
            expected_runtime_id="runtime-a",
        )


def test_bundle_rejects_extra_duplicate_traversal_and_symlink_members():
    members = list(read_members(valid_bundle()).items())
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = repack([*members, ("mask.png", b"duplicate")])
    attacks = [
        repack([*members, ("debug.txt", b"secret")]),
        duplicate,
        repack(
            [
                ("../mask.png", payload) if name == "mask.png" else (name, payload)
                for name, payload in members
            ]
        ),
        repack(members, symlink_name="mask.png"),
    ]
    for attack in attacks:
        with pytest.raises(ArtifactContractError):
            validate_bundle(attack, expected_runtime_id="runtime-a")


def test_bundle_rejects_excessive_compression_ratio():
    members = read_members(valid_bundle())
    members["background.jpg"] = b"0" * (2 * 1024 * 1024)
    ordered = [(name, members[name]) for name in ARTIFACT_ORDER]
    with pytest.raises(ArtifactContractError):
        validate_bundle(repack(ordered), expected_runtime_id="runtime-a")


def test_bundle_rejects_target_selection_mismatch():
    with pytest.raises(ArtifactContractError):
        validate_bundle(
            valid_bundle(),
            expected_runtime_id="runtime-a",
            expected_target_selection={
                "type": "box",
                "x": 0.2,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
        )


def test_automatic_bundle_requires_a_detected_target_box():
    accepted = validate_bundle(
        valid_bundle(),
        expected_runtime_id="runtime-a",
        require_target_selection=True,
    )
    assert accepted.manifest["target_selection"]["type"] == "box"

    members = read_members(valid_bundle())
    manifest = json.loads(members["manifest.json"])
    manifest["target_selection"] = None
    members["manifest.json"] = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    without_target = repack(
        [(name, members[name]) for name in ARTIFACT_ORDER]
    )
    with pytest.raises(ArtifactContractError, match="Sélection cible"):
        validate_bundle(
            without_target,
            expected_runtime_id="runtime-a",
            require_target_selection=True,
        )


def rewrite_manifest(bundle: bytes, mutate) -> bytes:
    members = read_members(bundle)
    manifest = json.loads(members["manifest.json"])
    mutate(manifest)
    members["manifest.json"] = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return repack([(name, members[name]) for name in ARTIFACT_ORDER])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest.update(pipeline_version="9.9.9"),
        lambda manifest: manifest["dependencies"][0].update(version="0.0.0"),
        lambda manifest: manifest["dependencies"].append(
            {"name": "unapproved-runtime", "version": "1.0"}
        ),
        lambda manifest: manifest["models"]["sam"].update(revision="c" * 40),
        lambda manifest: manifest["models"].update(
            {
                "birefnet": {
                    "repo_id": "ZhengPeng7/BiRefNet",
                    "revision": "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4",
                    "license": "mit",
                    "custom_code": True,
                    "custom_code_reviewed": True,
                }
            }
        ),
    ],
)
def test_bundle_rejects_unbound_pipeline_dependency_and_model_provenance(mutate):
    with pytest.raises(ArtifactContractError):
        validate_bundle(
            rewrite_manifest(valid_bundle(), mutate),
            expected_runtime_id="runtime-a",
        )
