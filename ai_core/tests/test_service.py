from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ai_core.artifacts import build_manifest, create_bundle
from ai_core.errors import TargetAmbiguousError
from ai_core.schemas import (
    CampaignLanguage,
    CandidateBox,
    NormalizedBox,
)
from ai_core.service import GPUHealth, RuntimeSettings, create_app


TOKEN = "test-token-with-at-least-thirty-two-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def image_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (80, 100), (220, 210, 200)).save(stream, format="PNG")
    return stream.getvalue()


def remote_request(*, snapshot_hash: str | None = None) -> dict:
    source = image_bytes()
    value = {
        "generation_id": "generation-123",
        "request_id": "request-123",
        "input_snapshot_hash": "",
        "input": {
            "product": {
                "name": "Sérum Éclat",
                "brand": "Maison Exemple",
                "category": "Sérum",
                "original_mime": "image/png",
                "original_sha256": hashlib.sha256(source).hexdigest(),
                "original_width": 80,
                "original_height": 100,
            },
            "generation": {
                "language": "fr",
                "seed": 42,
                "audience": None,
                "benefits": ["Hydrate la peau"],
                "ingredients": [],
                "verified_claims": [],
                "cta": "Découvrir",
                "creative_direction": None,
                "target_hint": {
                    "type": "box",
                    "x": 0.2,
                    "y": 0.2,
                    "width": 0.6,
                    "height": 0.6,
                },
                "source_generation_id": None,
            },
        },
    }
    canonical = json.dumps(
        value["input"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    value["input_snapshot_hash"] = snapshot_hash or hashlib.sha256(
        canonical
    ).hexdigest()
    return value


def post_job(client: TestClient, payload: dict | None = None):
    return client.post(
        "/v1/jobs",
        headers=AUTH,
        files={"image": ("product.png", image_bytes(), "image/png")},
        data={
            "request": json.dumps(
                payload or remote_request(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        },
    )


class NeverCalled:
    def run(self, *args, **kwargs):
        raise AssertionError("orchestrator must not run")


def settings() -> RuntimeSettings:
    return RuntimeSettings(token=TOKEN, runtime_id="runtime-test")


def gpu_ready() -> GPUHealth:
    return GPUHealth(available=True, name="Tesla T4", vram_bytes=16_000_000_000)


def test_every_runtime_endpoint_requires_constant_time_bearer_auth() -> None:
    app = create_app(
        settings(),
        orchestrator=NeverCalled(),
        gpu_probe=gpu_ready,
        readiness_probe=lambda: True,
    )
    with TestClient(app) as client:
        for method, path in (
            ("get", "/v1/health"),
            ("post", "/v1/jobs"),
            ("get", "/v1/jobs/not-found"),
            ("get", "/v1/jobs/not-found/bundle"),
        ):
            response = getattr(client, method)(path)
            assert response.status_code == 401


def test_health_reports_exact_runtime_gpu_and_pinned_model_commits() -> None:
    app = create_app(
        settings(),
        orchestrator=NeverCalled(),
        gpu_probe=gpu_ready,
        readiness_probe=lambda: True,
    )
    with TestClient(app) as client:
        response = client.get("/v1/health", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "runtime_id": "runtime-test",
        "gpu": {
            "available": True,
            "name": "Tesla T4",
            "vram_bytes": 16_000_000_000,
        },
        "models": {
            "grounding_dino": (
                "IDEA-Research/grounding-dino-tiny@"
                "a2bb814dd30d776dcf7e30523b00659f4f141c71"
            ),
            "sam": (
                "facebook/sam-vit-base@"
                "70c1a07f894ebb5b307fd9eaaee97b9dfc16068f"
            ),
            "sdxl": (
                "stabilityai/stable-diffusion-xl-base-1.0@"
                "462165984030d82259a11f4367a4eed129e94a7b"
            ),
            "qwen": (
                "Qwen/Qwen2.5-7B-Instruct@"
                "a09a35458c702b33eeacc393d103063234e8bc28"
            ),
        },
        "pipeline_version": "0.1.0",
    }


def test_health_and_job_creation_fail_closed_when_prerequisites_are_missing() -> None:
    app = create_app(
        settings(),
        orchestrator=NeverCalled(),
        gpu_probe=gpu_ready,
        readiness_probe=lambda: False,
    )
    with TestClient(app) as client:
        health = client.get("/v1/health", headers=AUTH)
        creation = post_job(client)

    assert health.status_code == 200
    assert health.json()["gpu"]["available"] is True
    assert health.json()["ready"] is False
    assert creation.status_code == 503
    assert creation.json()["detail"]["code"] == "AI_SERVICE_UNAVAILABLE"


def test_post_rejects_mismatched_canonical_snapshot_before_scheduling() -> None:
    app = create_app(
        settings(),
        orchestrator=NeverCalled(),
        gpu_probe=gpu_ready,
        readiness_probe=lambda: True,
    )
    with TestClient(app) as client:
        response = post_job(client, remote_request(snapshot_hash="f" * 64))

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_GENERATION_REQUEST"


def test_post_rejects_uploaded_image_bytes_not_bound_to_snapshot() -> None:
    payload = remote_request()
    payload["input"]["product"]["original_sha256"] = "a" * 64
    canonical = json.dumps(
        payload["input"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["input_snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    app = create_app(
        settings(),
        orchestrator=NeverCalled(),
        gpu_probe=gpu_ready,
        readiness_probe=lambda: True,
    )

    with TestClient(app) as client:
        response = post_job(client, payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_GENERATION_REQUEST"


def test_compressed_image_over_pixel_limit_fails_before_decode(
    oversized_png_bytes,
) -> None:
    payload = remote_request()
    payload["input"]["product"].update(
        {
            "original_sha256": hashlib.sha256(oversized_png_bytes).hexdigest(),
            "original_width": 8_000,
            "original_height": 5_001,
        }
    )
    canonical = json.dumps(
        payload["input"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["input_snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    app = create_app(
        settings(),
        orchestrator=NeverCalled(),
        gpu_probe=gpu_ready,
        readiness_probe=lambda: True,
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/jobs",
            headers=AUTH,
            files={"image": ("bomb.png", oversized_png_bytes, "image/png")},
            data={
                "request": json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_GENERATION_REQUEST"
    assert "pixel-area" in response.json()["detail"]["message"]


class BlockingSuccess:
    def __init__(self, output) -> None:
        self.output = output
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, job, source, *, on_stage_complete):
        self.started.set()
        self.release.wait(timeout=5)
        return self.output


def test_runtime_accepts_only_one_active_job_and_serves_done_bundle(
    artifact_payloads,
    pinned_models,
    pinned_dependencies,
    stage_receipts,
) -> None:
    manifest = build_manifest(
        generation_id="generation-123",
        request_id="request-123",
        runtime_id="runtime-test",
        input_snapshot_hash=remote_request()["input_snapshot_hash"],
        seed=42,
        language=CampaignLanguage.FR,
        target_selection=NormalizedBox(
            type="box", x=0.2, y=0.2, width=0.6, height=0.6
        ),
        dependencies=pinned_dependencies,
        models=pinned_models,
        stage_receipts=stage_receipts,
        artifact_payloads=artifact_payloads,
    )
    bundle = create_bundle(artifact_payloads, manifest)
    runner = BlockingSuccess(SimpleNamespace(bundle=bundle, manifest=manifest))
    app = create_app(
        settings(),
        orchestrator=runner,
        gpu_probe=gpu_ready,
        readiness_probe=lambda: True,
    )

    with TestClient(app) as client:
        created = post_job(client)
        assert created.status_code == 202
        assert runner.started.wait(timeout=2)
        # A distinct request_id is required; identical IDs are idempotent replays.
        busy_payload = remote_request()
        busy_payload["request_id"] = "request-busy-456"
        busy_payload["generation_id"] = "generation-busy-456"
        busy = post_job(client, busy_payload)
        assert busy.status_code == 429

        job_id = created.json()["id"]
        processing = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        assert processing.json()["status"] == "processing"
        runner.release.set()

        for _ in range(100):
            result = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
            if result.json()["status"] == "done":
                break
            time.sleep(0.01)
        assert result.json()["manifest"]["runtime_id"] == "runtime-test"

        wrong_runtime = client.get(
            f"/v1/jobs/{job_id}/bundle",
            headers={**AUTH, "X-Expected-Runtime-ID": "runtime-old"},
        )
        assert wrong_runtime.status_code == 409
        assert wrong_runtime.json()["detail"]["code"] == "AI_RUNTIME_LOST"

        downloaded = client.get(
            f"/v1/jobs/{job_id}/bundle",
            headers={**AUTH, "X-Expected-Runtime-ID": "runtime-test"},
        )
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"] == "application/zip"
        assert downloaded.content == bundle


class Ambiguous:
    def run(self, job, source, *, on_stage_complete):
        raise TargetAmbiguousError(
            (
                CandidateBox(
                    id="candidate-1",
                    score=0.91,
                    type="box",
                    x=0.1,
                    y=0.1,
                    width=0.3,
                    height=0.7,
                ),
                CandidateBox(
                    id="candidate-2",
                    score=0.88,
                    type="box",
                    x=0.6,
                    y=0.1,
                    width=0.3,
                    height=0.7,
                ),
            )
        )


def test_ambiguous_detection_is_an_explicit_error_with_candidates() -> None:
    app = create_app(
        settings(),
        orchestrator=Ambiguous(),
        gpu_probe=gpu_ready,
        readiness_probe=lambda: True,
    )
    with TestClient(app) as client:
        created = post_job(client)
        for _ in range(100):
            result = client.get(
                f"/v1/jobs/{created.json()['id']}", headers=AUTH
            )
            if result.json()["status"] == "error":
                break
            time.sleep(0.01)

    assert result.json()["error"]["code"] == "TARGET_AMBIGUOUS"
    assert len(result.json()["ambiguity"]) == 2
