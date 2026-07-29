"""Private, path-safe and atomic local artifact storage."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping

from app.core.config import settings


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class GenerationInstall:
    bundle_key: str
    artifact_keys: dict[str, str]
    created: bool


class LocalArtifactStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_key(key: str) -> PurePosixPath:
        if not key or "\\" in key or "\x00" in key:
            raise StorageError("Clé de stockage invalide.")
        path = PurePosixPath(key)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise StorageError("Clé de stockage invalide.")
        if ":" in path.parts[0]:
            raise StorageError("Clé de stockage invalide.")
        return path

    def path_for(self, key: str) -> Path:
        relative = self._validate_key(key)
        candidate = self.root.joinpath(*relative.parts)
        cursor = self.root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise StorageError("La clé traverse un lien symbolique.")
        resolved_candidate = candidate.resolve()
        if not resolved_candidate.is_relative_to(self.root):
            raise StorageError("La clé sort de la racine privée.")
        return candidate

    def put_bytes(self, key: str, payload: bytes) -> str:
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=".upload-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, destination)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise StorageError("Échec de l'écriture atomique.") from exc
        return key

    def read_bytes(self, key: str) -> bytes:
        try:
            path = self.path_for(key)
            if not path.is_file() or path.is_symlink():
                raise StorageError("Objet de stockage indisponible.")
            return path.read_bytes()
        except (OSError, StorageError) as exc:
            raise StorageError("Objet de stockage indisponible.") from exc

    def open(self, key: str) -> BinaryIO:
        path = self.path_for(key)
        if not path.is_file() or path.is_symlink():
            raise StorageError("Objet de stockage indisponible.")
        try:
            return path.open("rb")
        except OSError as exc:
            raise StorageError("Objet de stockage indisponible.") from exc

    def delete(self, key: str) -> None:
        path = self.path_for(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Échec du nettoyage de l'objet.") from exc

    def delete_generation(
        self, generation_id: str, *, expected_bundle_sha256: str
    ) -> None:
        """Remove only the exact generation installed by a failed DB finalize."""
        if not generation_id or PurePosixPath(generation_id).name != generation_id:
            raise StorageError("Identifiant de génération invalide.")
        raw_directory = self.root / "generations" / generation_id
        if raw_directory.is_symlink():
            raise StorageError("Répertoire de génération invalide.")
        directory = raw_directory.resolve()
        expected_parent = (self.root / "generations").resolve()
        if directory.parent != expected_parent:
            raise StorageError("Répertoire de génération invalide.")
        if not directory.exists():
            return
        bundle_path = directory / "bundle.zip"
        try:
            if not bundle_path.is_file() or bundle_path.is_symlink():
                raise StorageError("Bundle de génération invalide.")
            actual_checksum = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            if actual_checksum != expected_bundle_sha256:
                raise StorageError("L'empreinte du bundle installé ne correspond pas.")
            shutil.rmtree(directory)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Échec du nettoyage de la génération.") from exc

    @staticmethod
    def _directory_matches(
        directory: Path, bundle: bytes, members: Mapping[str, bytes]
    ) -> bool:
        expected = {"bundle.zip": bundle, **dict(members)}
        try:
            children = list(directory.iterdir())
            if {child.name for child in children} != set(expected):
                return False
            return all(
                child.is_file()
                and not child.is_symlink()
                and child.read_bytes() == expected[child.name]
                for child in children
            )
        except OSError:
            return False

    def install_generation(
        self,
        generation_id: str,
        bundle: bytes,
        members: Mapping[str, bytes],
    ) -> GenerationInstall:
        """Atomically install, or safely replay, a validated generation."""
        if not generation_id or PurePosixPath(generation_id).name != generation_id:
            raise StorageError("Identifiant de génération invalide.")
        if any(
            not name
            or name == "bundle.zip"
            or PurePosixPath(name).name != name
            for name in members
        ):
            raise StorageError("Nom d'artefact invalide.")

        base_key = f"generations/{generation_id}"
        bundle_key = f"{base_key}/bundle.zip"
        artifact_keys = {name: f"{base_key}/{name}" for name in members}
        final_directory = self.path_for(base_key)
        final_directory.parent.mkdir(parents=True, exist_ok=True)

        if final_directory.exists():
            if self._directory_matches(final_directory, bundle, members):
                return GenerationInstall(bundle_key, artifact_keys, created=False)
            raise StorageError("Les artefacts terminaux sont immuables.")

        staging = Path(
            tempfile.mkdtemp(prefix=f".{generation_id}-", dir=final_directory.parent)
        )
        try:
            (staging / "bundle.zip").write_bytes(bundle)
            for name, payload in members.items():
                (staging / name).write_bytes(payload)
            try:
                os.replace(staging, final_directory)
            except OSError as exc:
                # A lease handoff may race after the first worker installed the
                # exact same validated bundle but before its DB commit.
                if self._directory_matches(final_directory, bundle, members):
                    return GenerationInstall(bundle_key, artifact_keys, created=False)
                raise StorageError("Échec de l'installation atomique.") from exc
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Échec de l'installation atomique.") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

        return GenerationInstall(bundle_key, artifact_keys, created=True)


storage = LocalArtifactStorage(settings.ARTIFACT_ROOT)
