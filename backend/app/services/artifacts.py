"""Backend bindings for the canonical ``ai_core`` artifact contract."""
from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ai_core.artifacts import (
    ARTIFACT_ORDER,
    PIPELINE_VERSION,
    validate_bundle as validate_core_bundle,
)
from ai_core.errors import ArtifactContractError as CoreArtifactContractError
from ai_core.model_registry import PINNED_DEPENDENCIES, pinned_model_refs
from ai_core.schemas import CopyPayload, NormalizedBox


ARTIFACT_NAMES = frozenset(ARTIFACT_ORDER)
CHECKSUM_MEMBER_NAMES = ARTIFACT_NAMES - {"manifest.json"}
EXPECTED_DEPENDENCIES = {
    dependency.name.casefold(): dependency.version
    for dependency in PINNED_DEPENDENCIES
}
RUNTIME_DEPENDENCY_NAMES = {"torch", "cuda_runtime"}
EXPECTED_MODELS = pinned_model_refs()
_UNSET = object()


class ArtifactContractError(RuntimeError):
    """The remote bundle does not satisfy the trusted application contract."""


class ArtifactChecksumMismatch(ArtifactContractError):
    """A bundle member does not match its signed manifest metadata."""


@dataclass(frozen=True)
class ValidatedBundle:
    manifest: dict[str, Any]
    copy: dict[str, Any]
    members: dict[str, bytes]
    checksum: str


def _reject(message: str) -> ArtifactContractError:
    if "checksum" in message.casefold():
        return ArtifactChecksumMismatch("L'empreinte d'un artefact est invalide.")
    return ArtifactContractError("Le bundle ne respecte pas le contrat d'artefacts.")


def _language_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def validate_bundle(
    bundle: bytes,
    *,
    expected_runtime_id: str,
    expected_generation_id: str | None = None,
    expected_request_id: str | None = None,
    expected_input_snapshot_hash: str | None = None,
    expected_seed: int | None = None,
    expected_language: Any | None = None,
    expected_source_size: tuple[int, int] | None = None,
    expected_target_selection: Any = _UNSET,
    require_target_selection: bool = False,
) -> ValidatedBundle:
    """Validate untrusted bytes once, then bind them to the durable DB job."""
    try:
        manifest = validate_core_bundle(bundle)
    except CoreArtifactContractError as exc:
        raise _reject(str(exc)) from exc

    if manifest.pipeline_version != PIPELINE_VERSION:
        raise ArtifactContractError("Version de pipeline invalide.")
    dependency_versions = {
        dependency.name.casefold(): dependency.version
        for dependency in manifest.dependencies
    }
    if set(dependency_versions) != (
        set(EXPECTED_DEPENDENCIES) | RUNTIME_DEPENDENCY_NAMES
    ) or any(
        dependency_versions.get(name) != version
        for name, version in EXPECTED_DEPENDENCIES.items()
    ):
        raise ArtifactContractError("Versions de dépendances invalides.")
    if manifest.models != EXPECTED_MODELS:
        raise ArtifactContractError("Révisions de modèles invalides.")

    if manifest.runtime_id != expected_runtime_id:
        raise ArtifactContractError("L'identité du runtime a changé.")
    if expected_generation_id is not None and manifest.generation_id != expected_generation_id:
        raise ArtifactContractError("Identité de génération invalide.")
    if expected_request_id is not None and manifest.request_id != expected_request_id:
        raise ArtifactContractError("Identité de requête invalide.")
    if (
        expected_input_snapshot_hash is not None
        and manifest.input_snapshot_hash != expected_input_snapshot_hash
    ):
        raise ArtifactContractError("Empreinte de la demande invalide.")
    if expected_seed is not None and manifest.seed != expected_seed:
        raise ArtifactContractError("Seed du manifeste invalide.")
    if (
        expected_language is not None
        and manifest.language.value != _language_value(expected_language)
    ):
        raise ArtifactContractError("Langue du manifeste invalide.")

    if expected_target_selection is not _UNSET:
        try:
            expected_target = (
                None
                if expected_target_selection is None
                else NormalizedBox.model_validate(expected_target_selection)
            )
        except ValidationError as exc:
            raise ArtifactContractError("Sélection cible attendue invalide.") from exc
        if manifest.target_selection != expected_target:
            raise ArtifactContractError("Sélection cible du manifeste invalide.")
    if require_target_selection and manifest.target_selection is None:
        raise ArtifactContractError("Sélection cible automatique manquante.")

    records = {record.name.value: record for record in manifest.artifacts}
    if expected_source_size is not None:
        for name in ("cutout.png", "mask.png"):
            record = records[name]
            if (record.width, record.height) != expected_source_size:
                raise ArtifactContractError(
                    "Le masque et le cutout doivent correspondre à l'image source."
                )

    # The canonical validator has already rejected alternate names, ordering,
    # traversal, duplicates, symlinks, ZIP bombs, invalid media and bad checksums.
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as archive:
        members = {name: archive.read(name) for name in ARTIFACT_ORDER}

    try:
        copy = CopyPayload.model_validate_json(members["copy.json"])
    except ValidationError as exc:  # Defensive: the canonical validator also checks this.
        raise ArtifactContractError("Schéma de copy.json invalide.") from exc

    return ValidatedBundle(
        manifest=manifest.model_dump(mode="json"),
        copy=copy.model_dump(mode="json"),
        members=members,
        checksum=hashlib.sha256(bundle).hexdigest(),
    )
