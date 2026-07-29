from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_current_user
from app.main import app


EXPECTED_INVALID_GENERATION_DETAIL = {
    "code": "INVALID_GENERATION_REQUEST",
    "message": "La demande de génération est invalide.",
}


def _override_api_dependencies() -> None:
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        is_active=True,
    )


def test_generation_body_validation_uses_the_stable_error_envelope():
    _override_api_dependencies()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/products/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/generations",
                headers={"Idempotency-Key": "client-request-key-0001"},
                json={"language": "fr", "unsupported_claim": "miracle"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json() == {"detail": EXPECTED_INVALID_GENERATION_DETAIL}


def test_missing_idempotency_key_uses_the_stable_error_envelope():
    _override_api_dependencies()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/products/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/generations",
                json={"language": "fr"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json() == {"detail": EXPECTED_INVALID_GENERATION_DETAIL}


def test_non_generation_validation_keeps_fastapis_default_envelope():
    _override_api_dependencies()
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/login", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
