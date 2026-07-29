from __future__ import annotations

import hashlib
import io
import json
import stat
import warnings
import zipfile
from collections.abc import Mapping
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ValidationError

from .errors import ArtifactContractError
from .schemas import (
    ArtifactName,
    ArtifactRecord,
    CampaignLanguage,
    CopyPayload,
    DependencyRef,
    Manifest,
    ModelRef,
    NormalizedBox,
    StageReceipt,
)


ARTIFACT_ORDER = tuple(member.value for member in ArtifactName)
PIPELINE_VERSION = "0.1.0"
MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_SOURCE_PIXELS = 40_000_000

_FINAL_DIMENSIONS: dict[ArtifactName, tuple[int, int]] = {
    ArtifactName.INSTAGRAM: (1080, 1080),
    ArtifactName.FACEBOOK: (1200, 630),
    ArtifactName.LINKEDIN: (1200, 627),
}
_MIME: dict[ArtifactName, str] = {
    ArtifactName.INSTAGRAM: "image/jpeg",
    ArtifactName.FACEBOOK: "image/jpeg",
    ArtifactName.LINKEDIN: "image/jpeg",
    ArtifactName.COPY: "application/json",
    ArtifactName.CUTOUT: "image/png",
    ArtifactName.MASK: "image/png",
    ArtifactName.BACKGROUND: "image/jpeg",
}
_ROLE: dict[ArtifactName, str] = {
    ArtifactName.INSTAGRAM: "platform_final",
    ArtifactName.FACEBOOK: "platform_final",
    ArtifactName.LINKEDIN: "platform_final",
    ArtifactName.COPY: "campaign_copy",
    ArtifactName.CUTOUT: "product_cutout",
    ArtifactName.MASK: "accepted_mask",
    ArtifactName.BACKGROUND: "generated_background",
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(value: BaseModel | Mapping[str, Any]) -> bytes:
    if isinstance(value, BaseModel):
        serializable = value.model_dump(mode="json")
    else:
        serializable = dict(value)
    return json.dumps(
        serializable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactContractError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _parse_json(payload: bytes) -> Any:
    try:
        text = payload.decode("utf-8")
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ArtifactContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError("artifact JSON is not strict UTF-8 JSON") from exc


def _normalize_payloads(
    artifact_payloads: Mapping[ArtifactName | str, bytes],
) -> dict[ArtifactName, bytes]:
    normalized: dict[ArtifactName, bytes] = {}
    for raw_name, payload in artifact_payloads.items():
        try:
            name = raw_name if isinstance(raw_name, ArtifactName) else ArtifactName(raw_name)
        except ValueError as exc:
            raise ArtifactContractError(f"unknown artifact name {raw_name!r}") from exc
        if name is ArtifactName.MANIFEST or name in normalized:
            raise ArtifactContractError("artifact payload names must be unique and exclude manifest")
        if not isinstance(payload, bytes) or not payload:
            raise ArtifactContractError(f"{name.value} must contain non-empty bytes")
        normalized[name] = payload
    if set(normalized) != set(ArtifactName.non_manifest()):
        raise ArtifactContractError("artifact payloads must match the exact seven-member contract")
    return normalized


def _inspect_image(name: ArtifactName, payload: bytes) -> tuple[int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as probe:
                image_format = probe.format
                size = probe.size
                if name in _FINAL_DIMENSIONS:
                    expected = _FINAL_DIMENSIONS[name]
                    if size != expected:
                        raise ArtifactContractError(
                            f"{name.value} must be exactly {expected[0]}x{expected[1]}"
                        )
                elif name is ArtifactName.BACKGROUND:
                    if size != (1024, 1024):
                        raise ArtifactContractError(
                            "background.jpg must be exactly 1024x1024"
                        )
                elif size[0] * size[1] > MAX_SOURCE_PIXELS:
                    raise ArtifactContractError(
                        f"{name.value} pixel area exceeds the 40M source limit"
                    )
                probe.verify()
            with Image.open(io.BytesIO(payload)) as image:
                if image.size != size:
                    raise ArtifactContractError(
                        f"{name.value} changed dimensions while decoding"
                    )
                image.load()
                mode = image.mode
    except ArtifactContractError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ArtifactContractError(f"{name.value} is not a decodable image") from exc

    expected_format = "PNG" if name in {ArtifactName.CUTOUT, ArtifactName.MASK} else "JPEG"
    if image_format != expected_format:
        raise ArtifactContractError(f"{name.value} must be {expected_format}")
    if name in _FINAL_DIMENSIONS:
        if mode != "RGB":
            raise ArtifactContractError(f"{name.value} must decode as RGB")
    elif name is ArtifactName.BACKGROUND and mode != "RGB":
        raise ArtifactContractError("background.jpg must decode as RGB")
    elif name is ArtifactName.CUTOUT and mode != "RGBA":
        raise ArtifactContractError("cutout.png must decode as RGBA")
    elif name is ArtifactName.MASK and mode != "L":
        raise ArtifactContractError("mask.png must decode as single-channel L")
    return size


def _record_for(name: ArtifactName, payload: bytes) -> ArtifactRecord:
    if name is ArtifactName.COPY:
        try:
            CopyPayload.model_validate(_parse_json(payload))
        except ValidationError as exc:
            raise ArtifactContractError("copy.json does not match the strict copy schema") from exc
        width = height = None
    else:
        width, height = _inspect_image(name, payload)
    return ArtifactRecord(
        name=name,
        mime=_MIME[name],
        bytes=len(payload),
        sha256=sha256_bytes(payload),
        width=width,
        height=height,
        role=_ROLE[name],
    )


def _copy_payload(payload: bytes) -> CopyPayload:
    try:
        return CopyPayload.model_validate(_parse_json(payload))
    except ValidationError as exc:
        raise ArtifactContractError(
            "copy.json does not match the strict copy schema"
        ) from exc


def build_manifest(
    *,
    generation_id: str,
    request_id: str,
    runtime_id: str,
    input_snapshot_hash: str,
    seed: int,
    language: CampaignLanguage,
    target_selection: NormalizedBox | None,
    dependencies: tuple[DependencyRef, ...],
    models: dict[str, ModelRef],
    stage_receipts: tuple[StageReceipt, ...],
    artifact_payloads: Mapping[ArtifactName | str, bytes],
) -> Manifest:
    payloads = _normalize_payloads(artifact_payloads)
    if _copy_payload(payloads[ArtifactName.COPY]).language is not language:
        raise ArtifactContractError(
            "copy.json language must match manifest campaign language"
        )
    cutout_size = _inspect_image(ArtifactName.CUTOUT, payloads[ArtifactName.CUTOUT])
    mask_size = _inspect_image(ArtifactName.MASK, payloads[ArtifactName.MASK])
    if cutout_size != mask_size:
        raise ArtifactContractError(
            "cutout.png and mask.png must have matching source dimensions"
        )
    records = tuple(
        _record_for(name, payloads[name]) for name in ArtifactName.non_manifest()
    )
    try:
        return Manifest(
            pipeline_version=PIPELINE_VERSION,
            generation_id=generation_id,
            request_id=request_id,
            runtime_id=runtime_id,
            input_snapshot_hash=input_snapshot_hash,
            seed=seed,
            language=language,
            target_selection=target_selection,
            dependencies=dependencies,
            models=models,
            stage_receipts=stage_receipts,
            artifacts=records,
        )
    except ValidationError as exc:
        raise ArtifactContractError("manifest metadata violates the strict contract") from exc


def create_bundle(
    artifact_payloads: Mapping[ArtifactName | str, bytes],
    manifest: Manifest,
) -> bytes:
    payloads = _normalize_payloads(artifact_payloads)
    manifest_records = {record.name: record for record in manifest.artifacts}
    for name, payload in payloads.items():
        record = manifest_records.get(name)
        if record is None or record.sha256 != sha256_bytes(payload):
            raise ArtifactContractError(
                f"manifest checksum does not match {name.value}"
            )

    all_payloads = {
        **{name.value: payloads[name] for name in ArtifactName.non_manifest()},
        ArtifactName.MANIFEST.value: canonical_json_bytes(manifest),
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(
        stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in ARTIFACT_ORDER:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            info.extra = b""
            archive.writestr(info, all_payloads[name], compresslevel=9)
    return stream.getvalue()


def _validate_archive_members(
    archive: zipfile.ZipFile,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if tuple(names) != ARTIFACT_ORDER or len(set(names)) != len(names):
        raise ArtifactContractError(
            "bundle must contain exactly the eight canonical artifacts in order"
        )
    total_uncompressed = 0
    for info in infos:
        name = info.filename
        if (
            name.startswith(("/", "\\"))
            or "\\" in name
            or "/" in name
            or ".." in name
        ):
            raise ArtifactContractError("bundle members must be flat safe filenames")
        if info.flag_bits & 0x1:
            raise ArtifactContractError("encrypted bundle members are forbidden")
        if info.extra:
            raise ArtifactContractError("ZIP entry extra fields are forbidden")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ArtifactContractError("ZIP symlinks are forbidden")
        if info.file_size <= 0:
            raise ArtifactContractError("empty bundle members are forbidden")
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ArtifactContractError("bundle uncompressed size exceeds limit")
        if info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise ArtifactContractError("bundle member compression ratio exceeds limit")
    return {info.filename: info for info in infos}


def validate_bundle(bundle: bytes) -> Manifest:
    if not isinstance(bundle, bytes) or not bundle:
        raise ArtifactContractError("bundle must be non-empty bytes")
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise ArtifactContractError("bundle compressed size exceeds limit")
    stream = io.BytesIO(bundle)
    if not zipfile.is_zipfile(stream):
        raise ArtifactContractError("bundle is not a valid ZIP archive")
    stream.seek(0)
    try:
        with zipfile.ZipFile(stream) as archive:
            _validate_archive_members(archive)
            payloads = {name: archive.read(name) for name in ARTIFACT_ORDER}
    except ArtifactContractError:
        raise
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ArtifactContractError("bundle ZIP cannot be safely read") from exc

    try:
        manifest = Manifest.model_validate(
            _parse_json(payloads[ArtifactName.MANIFEST.value])
        )
    except ValidationError as exc:
        raise ArtifactContractError("manifest.json violates the strict schema") from exc

    records = {record.name: record for record in manifest.artifacts}
    if (
        _copy_payload(payloads[ArtifactName.COPY.value]).language
        is not manifest.language
    ):
        raise ArtifactContractError(
            "copy.json language must match manifest campaign language"
        )
    cutout_size: tuple[int, int] | None = None
    mask_size: tuple[int, int] | None = None
    for name in ArtifactName.non_manifest():
        payload = payloads[name.value]
        record = records.get(name)
        if record is None:
            raise ArtifactContractError(f"manifest omits {name.value}")
        if record.bytes != len(payload) or record.sha256 != sha256_bytes(payload):
            raise ArtifactContractError(f"artifact checksum mismatch for {name.value}")
        inspected = _record_for(name, payload)
        if inspected != record:
            raise ArtifactContractError(f"manifest metadata mismatch for {name.value}")
        if name is ArtifactName.CUTOUT:
            cutout_size = (record.width, record.height)  # type: ignore[arg-type]
        elif name is ArtifactName.MASK:
            mask_size = (record.width, record.height)  # type: ignore[arg-type]
    if cutout_size != mask_size:
        raise ArtifactContractError(
            "cutout.png and mask.png source dimensions do not match"
        )
    return manifest
