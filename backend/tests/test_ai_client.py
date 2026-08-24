from __future__ import annotations

import httpx
import io

from PIL import Image

from app.services.ai_client import AIClient, AIServiceUnavailable


def test_client_health_campaign_and_text_use_colab_contract() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "ready": True,
                    "runtime_id": "runtime-test",
                    "pipeline_version": "1.1.0",
                    "gpu": True,
                    "sdxl_model": "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
                    "ollama_model": "qwen2.5:7b-instruct-q4_K_M",
                },
            )
        if request.url.path == "/generate-campaign":
            return httpx.Response(
                200,
                content=b"PK\\x03\\x04",
                headers={"content-type": "application/zip"},
            )
        if request.url.path == "/generate-text":
            return httpx.Response(
                200,
                json={"brand": "Rexona", "product_name": "Shower Fresh"},
            )
        raise AssertionError(request.url.path)

    with AIClient(
        base_url="https://colab.example",
        token="secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.health().runtime_id == "runtime-test"
        assert client.generate_campaign(
            image_bytes=b"image",
            filename="product.jpg",
            fields={"brand": "Rexona"},
        ) == b"PK\\x03\\x04"
        assert client.generate_text({"brand": "Rexona"})["brand"] == "Rexona"

    assert calls == ["/health", "/generate-campaign", "/generate-text"]


def test_client_fails_when_colab_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with AIClient(
        base_url="https://colab.example",
        transport=httpx.MockTransport(handler),
    ) as client:
        try:
            client.health()
        except AIServiceUnavailable:
            pass
        else:
            raise AssertionError("unavailable Colab was accepted")


def test_client_accepts_gpu_runtime_when_optional_ollama_is_not_ready() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "ready": False,
                "runtime_id": "runtime-test",
                "pipeline_version": "1.2.0",
                "gpu": True,
            },
        )

    with AIClient(
        base_url="https://colab.example",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.health().runtime_id == "runtime-test"


def test_private_runtime_client_uses_authenticated_v2_contract_and_baseline() -> None:
    import base64

    buffer = io.BytesIO()
    Image.new("RGB", (12, 10), (20, 40, 60)).save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    image = base64.b64encode(image_bytes).decode("ascii")
    calls: list[str] = []
    auth_values: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        auth_values.append(request.headers.get("authorization", ""))
        if request.url.path == "/v2/health":
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "ready": True,
                    "runtime_id": "gpu-runtime-a",
                    "pipeline_version": "2.1.0",
                    "gpu": {"available": True},
                    "phases": {"container": True, "cuda": True, "models": True, "inference": True},
                },
            )
        if request.url.path == "/v2/product-locks":
            return httpx.Response(201, json={"status": "needs_review", "source_sha256": "a" * 64})
        if request.url.path.endswith("/artifacts/mask.png"):
            return httpx.Response(200, content=b"mask", headers={"content-type": "image/png"})
        if request.url.path.endswith("/artifacts/cutout.png"):
            return httpx.Response(200, content=b"cutout", headers={"content-type": "image/png"})
        if request.url.path == "/v2/generations":
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "model": "local-sdxl",
                    "model_revision": "rev-a",
                    "prompt_sha256": "b" * 64,
                    "baseline": {"image_b64": image, "mime": "image/png"},
                },
            )
        raise AssertionError(request.url.path)

    from app.services.ai_client import AIRuntimeClient

    with AIRuntimeClient(
        base_url="http://ai-runtime",
        token="runtime-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.health().runtime_id == "gpu-runtime-a"
        client.create_product_lock(
            image_bytes=b"source",
            filename="source.png",
            source_sha256="a" * 64,
            lock_id="lock-1",
            revision=1,
        )
        assert client.get_product_lock_artifact("lock-1", "mask.png") == b"mask"
        baseline = client.generate_baseline(
            generation_id="generation-1",
            lock_id="lock-1",
            category="cosmetics",
            scene_prompt="A distinct pale aqua spa counter",
            negative_prompt=None,
            seed=7,
            variant_count=1,
            target_box=None,
            prompt_sha256="b" * 64,
            request_hash="c" * 64,
        )
        assert baseline["image_bytes"] == image_bytes
        assert baseline["model_revision"] == "rev-a"
        assert baseline["width"] == 12
        assert baseline["height"] == 10

    assert calls == [
        "/v2/health",
        "/v2/product-locks",
        "/v2/product-locks/lock-1/artifacts/mask.png",
        "/v2/generations",
    ]
    assert auth_values and all(value == "Bearer runtime-token" for value in auth_values)


def test_private_runtime_client_rejects_mismatched_baseline_checksum() -> None:
    import hashlib
    import pytest
    from app.services.ai_client import AIRuntimeClient, AIProtocolError

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "navy").save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/generations":
            return httpx.Response(
                200,
                json={
                    "id": "runtime-generation-1",
                    "status": "ready",
                    "baseline": {
                        "status": "ready",
                        "artifact_url": "/v2/generations/runtime-generation-1/variants/0/image",
                        "sha256": hashlib.sha256(b"different").hexdigest(),
                        "mime": "image/png",
                        "width": 8,
                        "height": 8,
                        "bytes": len(image_bytes),
                    },
                },
            )
        if request.url.path.endswith("/variants/0/image"):
            return httpx.Response(200, content=image_bytes, headers={"content-type": "image/png"})
        raise AssertionError(request.url.path)

    with AIRuntimeClient(
        base_url="http://ai-runtime",
        token="runtime-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AIProtocolError, match="Checksum"):
            client.generate_baseline(
                generation_id="generation-1",
                lock_id="lock-1",
                category="cosmetics",
                scene_prompt="A unique product background scene",
                negative_prompt=None,
                seed=7,
                variant_count=1,
                target_box=None,
                prompt_sha256="b" * 64,
                request_hash="c" * 64,
            )


def test_private_runtime_client_rejects_blank_background_prompt() -> None:
    import pytest
    from app.services.ai_client import AIRuntimeClient, AIProtocolError

    with AIRuntimeClient(
        base_url="http://ai-runtime",
        token="runtime-token",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    ) as client:
        with pytest.raises(AIProtocolError, match="Background prompt"):
            client.generate_baseline(
                generation_id="generation-1",
                lock_id="lock-1",
                category="cosmetics",
                scene_prompt=" ",
                negative_prompt=None,
                seed=7,
                variant_count=1,
                target_box=None,
                prompt_sha256="b" * 64,
                request_hash="c" * 64,
            )
