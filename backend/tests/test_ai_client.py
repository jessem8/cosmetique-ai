from __future__ import annotations

import httpx

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
