"""Validation for the six-member Colab campaign contract."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError


REQUIRED_ARCHIVE_FILES = frozenset(
    {
        "instagram.jpg",
        "facebook.jpg",
        "linkedin.jpg",
        "copy.json",
        "ocr.json",
        "manifest.json",
    }
)
OPTIONAL_ARCHIVE_FILES = frozenset({"cutout.png", "mask.png", "background.jpg"})
ARTIFACT_NAMES = REQUIRED_ARCHIVE_FILES | OPTIONAL_ARCHIVE_FILES
CHECKSUM_MEMBER_NAMES = ARTIFACT_NAMES
PIPELINE_VERSION = "1.1.0"
MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 150 * 1024 * 1024


class ArtifactContractError(RuntimeError):
    """The remote ZIP does not satisfy the trusted Colab contract."""


class ArtifactChecksumMismatch(ArtifactContractError):
    """A member checksum or manifest checksum is invalid."""


@dataclass(frozen=True)
class ValidatedBundle:
    manifest: dict[str, Any]
    copy: dict[str, Any]
    ocr: dict[str, Any]
    members: dict[str, bytes]
    records: dict[str, dict[str, Any]]
    checksum: str


def _strict_json(payload: bytes, name: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ArtifactContractError(f"{name} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError(f"{name} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactContractError(f"{name} must contain an object")
    return value


def _copy_contract(copy: dict[str, Any]) -> None:
    required = {
        "brand",
        "product_name",
        "category",
        "titre",
        "sous_titre",
        "bullets",
        "cta",
        "hashtags",
        "_meta",
    }
    if set(copy) != required:
        raise ArtifactContractError("copy.json fields do not match the strict contract")
    for field in ("brand", "product_name", "category", "titre", "sous_titre", "cta"):
        if not isinstance(copy[field], str) or not copy[field].strip():
            raise ArtifactContractError(f"copy.json {field} must be non-empty text")
    if not isinstance(copy["bullets"], list) or len(copy["bullets"]) > 3:
        raise ArtifactContractError("copy.json bullets are invalid")
    if not all(isinstance(item, str) and item.strip() for item in copy["bullets"]):
        raise ArtifactContractError("copy.json bullets must be non-empty strings")
    if not isinstance(copy["hashtags"], list) or len(copy["hashtags"]) > 5:
        raise ArtifactContractError("copy.json hashtags are invalid")
    if not all(isinstance(item, str) and item.strip() for item in copy["hashtags"]):
        raise ArtifactContractError("copy.json hashtags must be non-empty strings")
    meta = copy["_meta"]
    if not isinstance(meta, dict):
        raise ArtifactContractError("copy.json _meta must be an object")
    if not isinstance(meta.get("ocr_text"), str) or not meta["ocr_text"].strip():
        raise ArtifactContractError("copy.json _meta.ocr_text is required")
    if meta.get("source") != "ocr+metadata":
        raise ArtifactContractError("copy.json _meta.source must be ocr+metadata")
    lowered = json.dumps(copy, ensure_ascii=False).casefold()
    for placeholder in ("maison exemple", "example brand", "hydra glow serum"):
        if placeholder in lowered:
            raise ArtifactContractError("copy.json contains placeholder copy")
    for claim in ("clinique", "dermatologique", "guérit", "traite"):
        if claim in lowered and claim not in meta["ocr_text"].casefold():
            raise ArtifactContractError("copy.json contains an unsupported claim")


def _image_record(name: str, payload: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            image_format = image.format
            size = image.size
            mode = image.mode
    except (OSError, UnidentifiedImageError) as exc:
        raise ArtifactContractError(f"{name} is not a readable image") from exc

    expected = {
        "instagram.jpg": ((1080, 1080), "JPEG", "RGB"),
        "facebook.jpg": ((1200, 630), "JPEG", "RGB"),
        "linkedin.jpg": ((1200, 627), "JPEG", "RGB"),
        "background.jpg": ((1024, 1024), "JPEG", "RGB"),
        "cutout.png": (None, "PNG", "RGBA"),
        "mask.png": (None, "PNG", "L"),
    }.get(name)
    if expected is None:
        raise ArtifactContractError(f"unknown image artifact {name}")
    expected_size, expected_format, expected_mode = expected
    if expected_size is not None and size != expected_size:
        raise ArtifactContractError(
            f"{name} must be exactly {expected_size[0]}x{expected_size[1]}"
        )
    if image_format != expected_format or mode != expected_mode:
        raise ArtifactContractError(f"{name} has the wrong image format or mode")
    return {
        "name": name,
        "mime": "image/png" if expected_format == "PNG" else "image/jpeg",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "width": size[0],
        "height": size[1],
    }


def validate_bundle(
    bundle: bytes,
    *,
    expected_runtime_id: str | None = None,
    expected_generation_id: str | None = None,
    expected_request_id: str | None = None,
    expected_input_snapshot_hash: str | None = None,
    expected_seed: int | None = None,
    expected_language: Any | None = None,
    expected_source_size: tuple[int, int] | None = None,
    expected_target_selection: Any = None,
    require_target_selection: bool = False,
) -> ValidatedBundle:
    if not isinstance(bundle, bytes) or not bundle or len(bundle) > MAX_BUNDLE_BYTES:
        raise ArtifactContractError("ZIP size is invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            allowed = REQUIRED_ARCHIVE_FILES | OPTIONAL_ARCHIVE_FILES
            if len(names) != len(set(names)) or any(name not in allowed for name in names):
                raise ArtifactContractError("ZIP contains unknown or duplicate members")
            if not REQUIRED_ARCHIVE_FILES.issubset(names):
                missing = sorted(REQUIRED_ARCHIVE_FILES.difference(names))
                raise ArtifactContractError("ZIP is missing: " + ", ".join(missing))
            total = sum(info.file_size for info in infos)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ArtifactContractError("ZIP uncompressed size is too large")
            for info in infos:
                if info.filename != info.filename.replace("\\", "/") or "/" in info.filename or ".." in info.filename:
                    raise ArtifactContractError("ZIP member path is unsafe")
                if info.flag_bits & 0x1:
                    raise ArtifactContractError("encrypted ZIP members are forbidden")
            members = {name: archive.read(name) for name in names}
    except ArtifactContractError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArtifactContractError("response is not a readable ZIP") from exc

    copy = _strict_json(members["copy.json"], "copy.json")
    _copy_contract(copy)
    ocr = _strict_json(members["ocr.json"], "ocr.json")
    manifest = _strict_json(members["manifest.json"], "manifest.json")
    if manifest.get("pipeline_version") != PIPELINE_VERSION:
        raise ArtifactContractError("unsupported Colab pipeline version")
    if manifest.get("preserves_product_pixels") is not True:
        raise ArtifactContractError("manifest does not prove product-pixel preservation")
    for key, expected in (
        ("runtime_id", expected_runtime_id),
        ("generation_id", expected_generation_id),
        ("request_id", expected_request_id),
        ("input_snapshot_hash", expected_input_snapshot_hash),
        ("seed", expected_seed),
    ):
        if expected is not None and manifest.get(key) != expected:
            raise ArtifactContractError(f"manifest {key} does not match the database request")
    if expected_language is not None and manifest.get("language") not in {expected_language, getattr(expected_language, "value", None)}:
        raise ArtifactContractError("manifest language does not match the request")

    records: dict[str, dict[str, Any]] = {}
    for name in sorted(REQUIRED_ARCHIVE_FILES | (OPTIONAL_ARCHIVE_FILES & set(members))):
        if name.endswith((".jpg", ".png")):
            records[name] = _image_record(name, members[name])
        else:
            records[name] = {
                "name": name,
                "mime": "application/json",
                "sha256": hashlib.sha256(members[name]).hexdigest(),
                "bytes": len(members[name]),
                "width": None,
                "height": None,
            }
    manifest = {**manifest, "artifacts": [records[name] for name in sorted(records)]}
    return ValidatedBundle(
        manifest=manifest,
        copy=copy,
        ocr=ocr,
        members=members,
        records=records,
        checksum=hashlib.sha256(bundle).hexdigest(),
    )
