from __future__ import annotations

import io
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.datastructures import Headers, UploadFile
from PIL import Image
from sqlalchemy.exc import SQLAlchemyError

from app.main import app
from app.routers import products
from app.routers import generations
from app.routers.products import (
    create_product,
    inspect_image,
    normalize_required_text,
    read_upload_bounded,
)
from app.routers.generations import (
    effective_generation_brief,
    generation_out,
    immutable_product_snapshot,
    product_summary,
)
from app.schemas import GenerationCreate


def encoded_image(fmt: str, size: tuple[int, int] = (20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (245, 245, 245)).save(buffer, format=fmt)
    return buffer.getvalue()


def test_versioned_api_exposes_only_complete_generation_workflow():
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert ("POST", "/api/v1/products") in routes
    assert ("GET", "/api/v1/products/{product_id}/image") in routes
    assert ("POST", "/api/v1/products/{product_id}/generations") in routes
    assert ("GET", "/api/v1/generations/{generation_id}") in routes
    assert ("GET", "/api/v1/generations") in routes
    assert ("GET", "/api/v1/generations/{generation_id}/bundle") in routes
    assert (
        "GET",
        "/api/v1/generations/{generation_id}/artifacts/{artifact_name}",
    ) in routes
    assert all(not path.startswith("/uploads") for _, path in routes)
    assert all("regenerate-text" not in path for _, path in routes)
    assert all("regenerate-decor" not in path for _, path in routes)


def test_image_inspection_decodes_bytes_and_reports_canonical_metadata():
    metadata = inspect_image(encoded_image("PNG"), "image/png", max_pixels=1_000)
    assert metadata.mime == "image/png"
    assert (metadata.width, metadata.height) == (20, 30)
    assert len(metadata.sha256) == 64


def test_image_inspection_rejects_header_content_mismatch():
    with pytest.raises(HTTPException) as error:
        inspect_image(encoded_image("PNG"), "image/jpeg", max_pixels=1_000)
    assert error.value.status_code == 400


def test_image_inspection_rejects_dimensions_outside_shared_ai_contract():
    with pytest.raises(HTTPException) as error:
        inspect_image(
            encoded_image("PNG", (20_001, 1)),
            "image/png",
            max_pixels=40_000_000,
        )
    assert error.value.status_code == 413


def test_image_inspection_rejects_pixel_bomb_before_storage():
    with pytest.raises(HTTPException) as error:
        inspect_image(encoded_image("PNG", (50, 50)), "image/png", max_pixels=1_000)
    assert error.value.status_code == 413


@pytest.mark.parametrize("value", ["", " ", "\t\r\n"])
def test_required_product_text_rejects_whitespace_only(value):
    with pytest.raises(HTTPException) as error:
        normalize_required_text(value)
    assert error.value.status_code == 422


def test_upload_reader_uses_bounded_chunks():
    class TrackingUpload:
        def __init__(self):
            self.payload = bytearray(b"abcdefgh")
            self.read_sizes = []

        async def read(self, size):
            self.read_sizes.append(size)
            chunk = bytes(self.payload[:size])
            del self.payload[:size]
            return chunk

    upload = TrackingUpload()
    assert (
        asyncio.run(read_upload_bounded(upload, max_bytes=8, chunk_bytes=3))
        == b"abcdefgh"
    )
    assert upload.read_sizes == [3, 3, 3, 1]


def test_product_upload_is_removed_when_database_commit_fails(monkeypatch):
    deleted = []
    monkeypatch.setattr(products.storage, "put_bytes", lambda key, payload: key)
    monkeypatch.setattr(products.storage, "delete", lambda key: deleted.append(key))

    class FailingDB:
        def add(self, value):
            self.value = value

        def commit(self):
            raise SQLAlchemyError("database unavailable")

        def rollback(self):
            self.rolled_back = True

    upload = UploadFile(
        io.BytesIO(encoded_image("PNG")),
        filename="product.png",
        headers=Headers({"content-type": "image/png"}),
    )
    current_user = SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    )
    db = FailingDB()

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            create_product(
                db=db,
                current_user=current_user,
                image=upload,
                name="Produit",
                category="Soin",
                brand=None,
            )
        )
    assert error.value.status_code == 500
    assert db.rolled_back is True
    assert len(deleted) == 1
    assert upload.file.closed is True


def test_product_upload_is_closed_when_size_validation_fails(monkeypatch):
    monkeypatch.setattr(products.settings, "MAX_UPLOAD_SIZE_MB", 0)
    upload = UploadFile(
        io.BytesIO(b"x"),
        filename="product.png",
        headers=Headers({"content-type": "image/png"}),
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            create_product(
                db=SimpleNamespace(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
                image=upload,
                name="Produit",
                category="Soin",
                brand=None,
            )
        )

    assert error.value.status_code == 413
    assert upload.file.closed is True


def test_product_upload_is_closed_after_success(monkeypatch):
    monkeypatch.setattr(products.storage, "put_bytes", lambda key, payload: key)

    class SuccessfulDB:
        def add(self, value):
            self.value = value

        def commit(self):
            return None

        def refresh(self, value):
            value.created_at = datetime.now(timezone.utc)

    upload = UploadFile(
        io.BytesIO(encoded_image("PNG")),
        filename="product.png",
        headers=Headers({"content-type": "image/png"}),
    )

    result = asyncio.run(
        create_product(
            db=SuccessfulDB(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            image=upload,
            name="Produit",
            category="Soin",
            brand=None,
        )
    )

    assert result.name == "Produit"
    assert upload.file.closed is True


def test_generation_response_exposes_a_bounded_product_summary():
    product = SimpleNamespace(name="Sérum", brand="Maison", category="Soin")
    assert product_summary(product).model_dump() == {
        "name": "Sérum",
        "brand": "Maison",
        "category": "Soin",
    }


def test_error_generation_never_exposes_untrusted_legacy_copy():
    now = datetime.now(timezone.utc)
    generation = SimpleNamespace(
        id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        product=SimpleNamespace(
            name="Sérum", brand="Maison", category="Soin"
        ),
        source_generation_id=None,
        status=generations.GenerationStatus.ERROR,
        stage=None,
        completed_stages=[],
        language="fr",
        seed=42,
        attempt_count=2,
        created_at=now,
        updated_at=now,
        error_code="ARTIFACT_CONTRACT_FAILED",
        error_message="Les fichiers générés sont incomplets ou invalides.",
        candidate_boxes=None,
        copy_json={"instagram": {"text": "Unsupported legacy claim"}},
        assets=[],
    )

    result = generation_out(generation)
    assert result.copy_data is None
    assert result.model_dump(mode="json", by_alias=True)["copy"] is None


def test_immutable_product_snapshot_binds_source_bytes_and_dimensions():
    product = SimpleNamespace(
        name="Sérum",
        brand="Maison",
        category="Soin",
        original_mime="image/png",
        original_sha256="a" * 64,
        original_width=640,
        original_height=800,
    )
    assert immutable_product_snapshot(product) == {
        "name": "Sérum",
        "brand": "Maison",
        "category": "Soin",
        "original_mime": "image/png",
        "original_sha256": "a" * 64,
        "original_width": 640,
        "original_height": 800,
    }


def test_source_brief_is_revalidated_before_inheritance():
    correction = GenerationCreate.model_validate(
        {
            "language": "fr",
            "seed": 42,
            "source_generation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        }
    )
    with pytest.raises(ValidationError):
        effective_generation_brief(
            correction,
            {
                "language": "fr",
                "seed": 42,
                "benefits": ["Hydrate"],
                "invented_legacy_field": "must not leak",
            },
        )


def test_ambiguity_correction_preserves_the_complete_verified_brief():
    source = {
        "language": "fr",
        "seed": 42,
        "audience": "peau sensible",
        "benefits": ["Hydrate"],
        "ingredients": ["Aloe vera"],
        "verified_claims": ["Testé sous contrôle dermatologique"],
        "cta": "Découvrir",
        "creative_direction": "Studio minéral",
        "target_hint": None,
        "source_generation_id": None,
    }
    correction = GenerationCreate.model_validate(
        {
            "language": "fr",
            "seed": 42,
            "source_generation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "target_hint": {
                "type": "box",
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
        }
    )

    merged = effective_generation_brief(correction, source)
    assert merged["audience"] == "peau sensible"
    assert merged["benefits"] == ["Hydrate"]
    assert merged["ingredients"] == ["Aloe vera"]
    assert merged["verified_claims"] == ["Testé sous contrôle dermatologique"]
    assert merged["cta"] == "Découvrir"
    assert merged["creative_direction"] == "Studio minéral"
    assert merged["target_hint"]["x"] == 0.1


def test_ambiguity_correction_inherits_a_custom_seed_when_omitted():
    source = {
        "language": "fr",
        "seed": 987_654_321,
        "audience": "peau sensible",
        "benefits": ["Hydrate"],
        "ingredients": [],
        "verified_claims": [],
        "cta": None,
        "creative_direction": None,
        "target_hint": None,
        "source_generation_id": None,
    }
    correction = GenerationCreate.model_validate(
        {
            "language": "fr",
            "source_generation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "target_hint": {
                "type": "box",
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
        }
    )

    merged = effective_generation_brief(correction, source)

    assert merged["seed"] == 987_654_321


def test_generation_persists_the_effective_inherited_seed(monkeypatch):
    user_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    product_id = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    source_id = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    product = generations.Product(
        id=product_id,
        user_id=user_id,
        name="Sérum",
        brand="Maison",
        category="Soin",
        original_mime="image/png",
        original_sha256="a" * 64,
        original_width=640,
        original_height=800,
    )
    source = SimpleNamespace(
        id=source_id,
        product_id=product_id,
        input_snapshot={
            "generation": {
                "language": "fr",
                "seed": 987_654_321,
                "audience": "peau sensible",
                "benefits": ["Hydrate"],
                "ingredients": [],
                "verified_claims": [],
                "cta": None,
                "creative_direction": None,
                "target_hint": None,
                "source_generation_id": None,
            }
        },
    )

    class FakeDB:
        def __init__(self):
            self.results = [product, source, None]
            self.added = None

        def scalar(self, statement):
            return self.results.pop(0)

        def add(self, value):
            self.added = value

        def commit(self):
            return None

        def refresh(self, value):
            return None

    class HealthyClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def health(self):
            return SimpleNamespace(runtime_id="runtime-a")

    db = FakeDB()
    monkeypatch.setattr(generations, "AIClient", HealthyClient)
    monkeypatch.setattr(generations, "generation_out", lambda value: value)
    body = GenerationCreate.model_validate(
        {
            "language": "fr",
            "source_generation_id": str(source_id),
            "target_hint": {
                "type": "box",
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
        }
    )

    created = generations.create_generation(
        product_id=product_id,
        body=body,
        db=db,
        current_user=SimpleNamespace(id=user_id),
        idempotency_key="client-request-key-0001",
    )

    assert created is db.added
    assert created.seed == 987_654_321
    assert created.language.value == "fr"
