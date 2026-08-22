from __future__ import annotations

import asyncio
import io
import os
from types import SimpleNamespace

import httpx
from PIL import Image, ImageDraw

from ai.colab_v2_runtime import ColabProductLockEngine
from ai.colab_v2_service import ColabDiffusersProvider, ColabV2Service, create_app
from ai_service.contracts import NormalizedBox, Proposal
from ai_service.diffusers_adapter import FakeInpaintingAdapter
from ai_service.segmentation import GroundingDINOProposalAdapter, SAM2Segmenter


def _source() -> bytes:
    image = Image.new("RGB", (80, 60), "white")
    ImageDraw.Draw(image).rectangle((22, 8, 56, 51), fill="navy")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _Detector:
    def propose(self, image, prompt: str):
        if prompt == "cosmetic product packaging":
            return [Proposal(NormalizedBox(0.25, 0.12, 0.45, 0.74), 0.95, "product")]
        return []


class _SAM:
    def predict(self, **kwargs):
        mask = Image.new("L", (80, 60), 0)
        ImageDraw.Draw(mask).rectangle((22, 8, 56, 51), fill=255)
        return {"mask": mask, "score": 0.96}


def _service() -> ColabV2Service:
    engine = ColabProductLockEngine(
        proposal=GroundingDINOProposalAdapter(detector=_Detector()),
        sam2=SAM2Segmenter(predictor=_SAM()),
    )
    runtime = SimpleNamespace(
        status="ready",
        engine=engine,
        health=lambda: {
            "primary_engine": "colab-v2",
            "status": "ready",
            "runtime_id": "test-runtime",
        },
    )
    provider = ColabDiffusersProvider(FakeInpaintingAdapter(), "synthetic-sdxl")
    return ColabV2Service(runtime=runtime, provider=provider)


def test_v2_service_creates_lock_generates_and_preserves_product_pixels() -> None:
    service = _service()
    lock = service.create_lock(_source())
    assert lock.accepted is True

    generation_id, outcome = service.generate(
        lock.lock_id,
        category="deodorant",
        seed=42,
        creative_direction="Une salle de bain premium bleu pâle, lumière du matin.",
    )
    assert generation_id in service.generations
    assert outcome.variants[0].qa.passed is True
    assert outcome.variants[0].manifest.pixel_comparison.exact is True
    assert outcome.variants[0].manifest.provider == "colab"


def test_v2_service_batch_gives_every_input_a_terminal_status() -> None:
    results = _service().batch(
        [("one.png", _source()), ("two.png", _source())],
        category="cosmetics",
    )
    assert len(results) == 2
    assert all(item["status"] in {"ready", "needs_review", "failed"} for item in results)


def test_v2_api_requires_bearer_and_exposes_v2_health() -> None:
    os.environ["COLAB_AI_TOKEN"] = "test-token-with-at-least-32-characters"
    app = create_app(_service())

    async def request() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            denied = await client.get("/v2/health")
            allowed = await client.get(
                "/v2/health",
                headers={"Authorization": "Bearer test-token-with-at-least-32-characters"},
            )
            return denied, allowed

    denied, allowed = asyncio.run(request())
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["primary_engine"] == "colab-v2"
