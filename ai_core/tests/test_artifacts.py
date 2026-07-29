from __future__ import annotations

import io
import hashlib
import json
import zipfile

import pytest

from ai_core.artifacts import (
    ARTIFACT_ORDER,
    ArtifactContractError,
    build_manifest,
    create_bundle,
    validate_bundle,
)
from ai_core.schemas import (
    ArtifactName,
    CampaignLanguage,
    NormalizedBox,
)


def build_valid_bundle(
    artifact_payloads,
    pinned_models,
    pinned_dependencies,
    stage_receipts,
) -> bytes:
    manifest = build_manifest(
        generation_id="generation-123",
        request_id="request-123",
        runtime_id="runtime-123",
        input_snapshot_hash="e" * 64,
        seed=42,
        language=CampaignLanguage.FR,
        target_selection=NormalizedBox(
            type="box", x=0.12, y=0.18, width=0.54, height=0.68
        ),
        dependencies=pinned_dependencies,
        models=pinned_models,
        stage_receipts=stage_receipts,
        artifact_payloads=artifact_payloads,
    )
    return create_bundle(artifact_payloads, manifest)


def test_bundle_round_trip_validates_exact_artifact_contract(
    artifact_payloads,
    pinned_models,
    pinned_dependencies,
    stage_receipts,
) -> None:
    bundle = build_valid_bundle(
        artifact_payloads, pinned_models, pinned_dependencies, stage_receipts
    )

    validated = validate_bundle(bundle)

    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert tuple(archive.namelist()) == ARTIFACT_ORDER
    assert validated.generation_id == "generation-123"
    assert {record.name for record in validated.artifacts} == set(
        ArtifactName.non_manifest()
    )


def test_bundle_rejects_extra_member(
    artifact_payloads,
    pinned_models,
    pinned_dependencies,
    stage_receipts,
) -> None:
    bundle = build_valid_bundle(
        artifact_payloads, pinned_models, pinned_dependencies, stage_receipts
    )
    source = zipfile.ZipFile(io.BytesIO(bundle))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            archive.writestr(info.filename, source.read(info.filename))
        archive.writestr("debug.txt", b"not allowed")

    with pytest.raises(ArtifactContractError, match="exactly"):
        validate_bundle(output.getvalue())


def test_bundle_rejects_checksum_mismatch(
    artifact_payloads,
    pinned_models,
    pinned_dependencies,
    stage_receipts,
) -> None:
    bundle = build_valid_bundle(
        artifact_payloads, pinned_models, pinned_dependencies, stage_receipts
    )
    source = zipfile.ZipFile(io.BytesIO(bundle))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == ArtifactName.COPY.value:
                payload += b" "
            archive.writestr(info.filename, payload)

    with pytest.raises(ArtifactContractError, match="checksum"):
        validate_bundle(output.getvalue())


def test_manifest_builder_rejects_wrong_final_dimensions(
    artifact_payloads,
    pinned_models,
    pinned_dependencies,
    stage_receipts,
) -> None:
    artifact_payloads[ArtifactName.LINKEDIN] = artifact_payloads[
        ArtifactName.FACEBOOK
    ]

    with pytest.raises(ArtifactContractError, match="1200x627"):
        build_manifest(
            generation_id="generation-123",
            request_id="request-123",
            runtime_id="runtime-123",
            input_snapshot_hash="e" * 64,
            seed=42,
            language=CampaignLanguage.FR,
            target_selection=None,
            dependencies=pinned_dependencies,
            models=pinned_models,
            stage_receipts=stage_receipts,
            artifact_payloads=artifact_payloads,
        )


def test_bundle_rejects_copy_language_different_from_manifest(
    artifact_payloads,
    pinned_models,
    pinned_dependencies,
    stage_receipts,
) -> None:
    bundle = build_valid_bundle(
        artifact_payloads, pinned_models, pinned_dependencies, stage_receipts
    )
    source = zipfile.ZipFile(io.BytesIO(bundle))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == ArtifactName.COPY.value:
                copy = json.loads(payload)
                copy["language"] = "en"
                payload = json.dumps(
                    copy,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            archive.writestr(info.filename, payload)

    with pytest.raises(ArtifactContractError, match="language"):
        validate_bundle(output.getvalue())


def test_zip_contained_image_bomb_is_rejected_from_header_before_decode(
    artifact_payloads,
    pinned_models,
    pinned_dependencies,
    stage_receipts,
    oversized_png_bytes,
) -> None:
    bundle = build_valid_bundle(
        artifact_payloads, pinned_models, pinned_dependencies, stage_receipts
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as source:
        members = {name: source.read(name) for name in ARTIFACT_ORDER}
    members[ArtifactName.MASK.value] = oversized_png_bytes
    manifest = json.loads(members[ArtifactName.MANIFEST.value])
    mask_record = next(
        record
        for record in manifest["artifacts"]
        if record["name"] == ArtifactName.MASK.value
    )
    mask_record.update(
        {
            "bytes": len(oversized_png_bytes),
            "sha256": hashlib.sha256(oversized_png_bytes).hexdigest(),
            "width": 8_000,
            "height": 5_001,
        }
    )
    members[ArtifactName.MANIFEST.value] = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in ARTIFACT_ORDER:
            archive.writestr(name, members[name])

    with pytest.raises(ArtifactContractError, match="pixel area"):
        validate_bundle(output.getvalue())
