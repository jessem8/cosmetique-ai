from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services.storage import LocalArtifactStorage, StorageError


def test_atomic_storage_round_trip_stays_inside_private_root(tmp_path: Path):
    storage = LocalArtifactStorage(tmp_path)
    key = storage.put_bytes("products/user-a/product-a/original.jpg", b"private-image")

    assert key == "products/user-a/product-a/original.jpg"
    assert storage.read_bytes(key) == b"private-image"
    assert storage.path_for(key).is_relative_to(tmp_path.resolve())
    assert not list(tmp_path.rglob("*.tmp"))


def test_storage_read_bytes_rejects_symlinks(tmp_path: Path):
    storage = LocalArtifactStorage(tmp_path)
    target = tmp_path / "outside-private-source.bin"
    target.write_bytes(b"must-not-be-followed")
    link = tmp_path / "products" / "linked.bin"
    link.parent.mkdir()
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("This environment does not permit creating symlinks.")

    with pytest.raises(StorageError, match="indisponible"):
        storage.read_bytes("products/linked.bin")


@pytest.mark.parametrize(
    "key",
    ["../secret", "/absolute/path", "C:\\secret", "nested/../../secret", "a\\..\\secret"],
)
def test_storage_rejects_path_escape(key, tmp_path: Path):
    storage = LocalArtifactStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.put_bytes(key, b"blocked")


def test_storage_open_requires_existing_regular_file(tmp_path: Path):
    storage = LocalArtifactStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.open("missing.bin")


def test_generation_install_is_idempotent_only_for_identical_bytes(tmp_path: Path):
    storage = LocalArtifactStorage(tmp_path)
    members = {"manifest.json": b"manifest", "instagram.jpg": b"image"}

    first = storage.install_generation("generation-a", b"bundle", members)
    replay = storage.install_generation("generation-a", b"bundle", members)

    assert first.created is True
    assert replay.created is False
    assert replay.bundle_key == first.bundle_key
    assert replay.artifact_keys == first.artifact_keys
    assert storage.read_bytes(first.bundle_key) == b"bundle"
    assert storage.read_bytes(first.artifact_keys["manifest.json"]) == b"manifest"
    assert not list((tmp_path / "generations").glob(".generation-a-*"))

    with pytest.raises(StorageError, match="immuables"):
        storage.install_generation("generation-a", b"different-bundle", members)
    with pytest.raises(StorageError, match="immuables"):
        storage.install_generation(
            "generation-a",
            b"bundle",
            {**members, "instagram.jpg": b"different-image"},
        )


def test_generation_cleanup_requires_the_exact_installed_bundle(tmp_path: Path):
    storage = LocalArtifactStorage(tmp_path)
    bundle = b"bundle"
    storage.install_generation("generation-a", bundle, {"manifest.json": b"manifest"})

    with pytest.raises(StorageError, match="empreinte"):
        storage.delete_generation("generation-a", expected_bundle_sha256="0" * 64)
    assert (tmp_path / "generations" / "generation-a").is_dir()

    storage.delete_generation(
        "generation-a", expected_bundle_sha256=hashlib.sha256(bundle).hexdigest()
    )
    assert not (tmp_path / "generations" / "generation-a").exists()
