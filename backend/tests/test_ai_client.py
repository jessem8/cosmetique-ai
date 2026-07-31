from __future__ import annotations

import httpx
import pytest

from app.services.ai_client import (
    AIClient,
    AIProtocolError,
    AIRuntimeLost,
    AIServiceUnavailable,
)


PINNED_HEALTH_MODELS = {
    "grounding_dino": (
        "IDEA-Research/grounding-dino-tiny@"
        "a2bb814dd30d776dcf7e30523b00659f4f141c71"
    ),
    "sam": "facebook/sam-vit-base@70c1a07f894ebb5b307fd9eaaee97b9dfc16068f",
    "sdxl": (
        "stabilityai/stable-diffusion-xl-base-1.0@"
        "462165984030d82259a11f4367a4eed129e94a7b"
    ),
    "qwen": (
        "Qwen/Qwen2.5-7B-Instruct@"
        "a09a35458c702b33eeacc393d103063234e8bc28"
    ),
}
SAFE_JOB_ID = "job-abcdefghijklmnop"


def client_with(handler):
    transport = httpx.MockTransport(handler)
    return AIClient(
        base_url="https://ai-runtime.test",
        token="secret-token",
        timeout_seconds=1,
        transport=transport,
    )


def test_health_is_authenticated_and_returns_runtime_contract():
    def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(
            200,
            json={
                "ready": True,
                "runtime_id": "runtime-a",
                "gpu": {"available": True, "name": "Tesla T4", "vram_bytes": 1},
                "models": PINNED_HEALTH_MODELS,
                "pipeline_version": "0.1.0",
            },
        )

    assert client_with(handler).health().runtime_id == "runtime-a"


def test_health_accepts_gzip_encoded_json_bodies():
    import gzip
    import json

    payload = {
        "ready": True,
        "runtime_id": "runtime-gzip",
        "gpu": {"available": True, "name": "Tesla T4", "vram_bytes": 1},
        "models": PINNED_HEALTH_MODELS,
        "pipeline_version": "0.1.0",
    }
    compressed = gzip.compress(json.dumps(payload).encode("utf-8"))

    def handler(request: httpx.Request):
        assert request.headers.get("accept-encoding") == "identity"
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
            content=compressed,
        )

    assert client_with(handler).health().runtime_id == "runtime-gzip"


@pytest.mark.parametrize(
    "models",
    [
        {**PINNED_HEALTH_MODELS, "sam": "facebook/sam-vit-base@main"},
        {
            **PINNED_HEALTH_MODELS,
            "sam": "untrusted/sam-vit-base@" + "2" * 40,
        },
        {
            **PINNED_HEALTH_MODELS,
            "sam": "facebook/sam-vit-base@" + "5" * 40,
        },
    ],
)
def test_health_rejects_mutable_or_wrong_model_references(models):
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "ready": True,
                "runtime_id": "runtime-a",
                "gpu": {"available": True, "name": "Tesla T4", "vram_bytes": 1},
                "models": models,
                "pipeline_version": "0.1.0",
            },
        )
    )
    with pytest.raises(AIServiceUnavailable):
        client.health()


def test_health_fails_closed_when_runtime_is_not_ready():
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "ready": False,
                "runtime_id": "runtime-a",
                "gpu": {"available": False, "name": None, "vram_bytes": 0},
                "models": {},
                "pipeline_version": "0.1.0",
            },
        )
    )
    with pytest.raises(AIServiceUnavailable):
        client.health()


def test_poll_rejects_changed_runtime_identity():
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "id": SAFE_JOB_ID,
                "status": "processing",
                "stage": "background",
                "completed_stages": ["analysis", "extraction", "art_direction"],
                "runtime_id": "runtime-b",
                "error": None,
                "ambiguity": None,
            },
        )
    )
    with pytest.raises(AIRuntimeLost):
        client.get_job(SAFE_JOB_ID, expected_runtime_id="runtime-a")


def test_bundle_download_preserves_explicit_runtime_loss_from_bounded_json():
    client = client_with(
        lambda request: httpx.Response(
            409,
            headers={"content-type": "application/json"},
            json={
                "detail": {
                    "code": "AI_RUNTIME_LOST",
                    "message": "runtime identity changed",
                }
            },
        )
    )

    with pytest.raises(AIRuntimeLost):
        client.download_bundle(SAFE_JOB_ID, expected_runtime_id="runtime-a")


def test_bundle_runtime_error_body_is_bounded(monkeypatch):
    from app.services import ai_client as module

    monkeypatch.setattr(module, "MAX_JSON_RESPONSE_BYTES", 32)
    client = client_with(
        lambda request: httpx.Response(
            409,
            headers={"content-type": "application/json"},
            content=b"{" + (b"x" * 64) + b"}",
        )
    )

    with pytest.raises(AIProtocolError, match="volumineuse"):
        client.download_bundle(SAFE_JOB_ID, expected_runtime_id="runtime-a")


def test_protocol_rejects_unknown_remote_stage():
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "id": SAFE_JOB_ID,
                "status": "processing",
                "stage": "invented-progress",
                "completed_stages": [],
                "runtime_id": "runtime-a",
                "error": None,
                "ambiguity": None,
            },
        )
    )
    with pytest.raises(AIProtocolError):
        client.get_job(SAFE_JOB_ID, expected_runtime_id="runtime-a")


@pytest.mark.parametrize(
    ("status", "stage", "completed_stages", "manifest"),
    [
        ("processing", "packaging", [], None),
        (
            "processing",
            "analysis",
            ["analysis", "extraction"],
            None,
        ),
    ],
)
def test_protocol_rejects_impossible_stage_progress_combinations(
    status, stage, completed_stages, manifest
):
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "id": SAFE_JOB_ID,
                "status": status,
                "stage": stage,
                "completed_stages": completed_stages,
                "runtime_id": "runtime-a",
                "error": None,
                "ambiguity": None,
                "manifest": manifest,
            },
        )
    )

    with pytest.raises(AIProtocolError):
        client.get_job(SAFE_JOB_ID, expected_runtime_id="runtime-a")


def test_protocol_accepts_canonical_ambiguity_candidates():
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "id": SAFE_JOB_ID,
                "status": "error",
                "stage": "analysis",
                "completed_stages": [],
                "runtime_id": "runtime-a",
                "error": {
                    "code": "TARGET_AMBIGUOUS",
                    "message": "Plusieurs produits ont été détectés.",
                },
                "ambiguity": [
                    {
                        "id": "candidate-1",
                        "score": 0.9,
                        "type": "box",
                        "x": 0.1,
                        "y": 0.1,
                        "width": 0.3,
                        "height": 0.5,
                    },
                    {
                        "id": "candidate-2",
                        "score": 0.8,
                        "type": "box",
                        "x": 0.6,
                        "y": 0.1,
                        "width": 0.3,
                        "height": 0.5,
                    },
                ],
                "manifest": None,
            },
        )
    )

    job = client.get_job(SAFE_JOB_ID, expected_runtime_id="runtime-a")

    assert len(job.ambiguity or ()) == 2
    assert job.ambiguity[0].x == 0.1


def test_protocol_rejects_malformed_or_single_ambiguity_candidate():
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "id": SAFE_JOB_ID,
                "status": "error",
                "stage": "analysis",
                "completed_stages": [],
                "runtime_id": "runtime-a",
                "error": {
                    "code": "TARGET_AMBIGUOUS",
                    "message": "Sélection requise.",
                },
                "ambiguity": [
                    {
                        "id": "candidate-1",
                        "score": 0.9,
                        "type": "box",
                        "x": 0.8,
                        "y": 0.1,
                        "width": 0.5,
                        "height": 0.5,
                    }
                ],
                "manifest": None,
            },
        )
    )
    with pytest.raises(AIProtocolError):
        client.get_job(SAFE_JOB_ID, expected_runtime_id="runtime-a")


def test_protocol_rejects_inconsistent_processing_state():
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "id": SAFE_JOB_ID,
                "status": "processing",
                "stage": None,
                "completed_stages": ["analysis", "art_direction"],
                "runtime_id": "runtime-a",
                "error": None,
                "ambiguity": None,
                "manifest": None,
            },
        )
    )
    with pytest.raises(AIProtocolError):
        client.get_job(SAFE_JOB_ID, expected_runtime_id="runtime-a")


def test_json_response_stops_when_stream_exceeds_limit(monkeypatch):
    from app.services import ai_client as module

    monkeypatch.setattr(module, "MAX_JSON_RESPONSE_BYTES", 5)
    client = client_with(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ready":true}',
        )
    )
    with pytest.raises(AIProtocolError, match="volumineuse"):
        client.health()


def test_bundle_download_stops_when_stream_exceeds_limit(monkeypatch):
    from app.services import ai_client as module

    monkeypatch.setattr(module, "MAX_BUNDLE_BYTES", 5)
    client = client_with(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=b"123456",
        )
    )
    with pytest.raises(AIProtocolError, match="volumineux"):
        client.download_bundle(SAFE_JOB_ID, expected_runtime_id="runtime-a")


def test_bundle_protocol_truncation_is_a_transient_transport_failure():
    class TruncatedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"PK"
            raise httpx.RemoteProtocolError("peer closed the chunked response")

    client = client_with(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            stream=TruncatedStream(),
        )
    )

    with pytest.raises(AIServiceUnavailable):
        client.download_bundle(SAFE_JOB_ID, expected_runtime_id="runtime-a")


@pytest.mark.parametrize("method_name", ["get_job", "download_bundle"])
def test_remote_job_path_injection_is_rejected_before_any_request(method_name):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        raise AssertionError("unsafe job identifiers must never reach the transport")

    client = client_with(handler)
    method = getattr(client, method_name)

    with pytest.raises(AIProtocolError, match="Identifiant"):
        method("job-../../health?admin=true", expected_runtime_id="runtime-a")

    assert requests == []


def test_poll_rejects_a_different_response_job_identity():
    client = client_with(
        lambda request: httpx.Response(
            200,
            json={
                "id": "job-qrstuvwxyzABCDEF",
                "status": "processing",
                "stage": "analysis",
                "completed_stages": [],
                "runtime_id": "runtime-a",
                "error": None,
                "ambiguity": None,
                "manifest": None,
            },
        )
    )

    with pytest.raises(AIProtocolError, match="correspond"):
        client.get_job(SAFE_JOB_ID, expected_runtime_id="runtime-a")
