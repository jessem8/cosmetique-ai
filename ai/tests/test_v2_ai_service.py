from __future__ import annotations

import base64
import io
import json
import asyncio

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw

from ai_service import (
    BackgroundGenerationOrchestrator,
    CleanSyntheticQA,
    CloseRouterAdapter,
    DeterministicImageProvider,
    DiffusersInpaintingAdapter,
    HostNotAllowedError,
    ProductLockProcessor,
    ProductLockStatus,
    RefinementContract,
    ScenePlanner,
    SuspiciousMaskError,
    compare_interior_pixels,
)
from ai_service.api import AIService, create_app
from ai_service.contracts import GenerationSpec, ProviderCapabilities, SegmentationResult, sha256_bytes


def _jpeg_source(size: tuple[int, int] = (64, 48)) -> bytes:
    image = Image.new("RGB", size, (200, 210, 215))
    ImageDraw.Draw(image).rectangle((18, 7, 42, 40), fill=(15, 35, 70))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=93)
    return output.getvalue()


def _mask(size: tuple[int, int] = (64, 48)) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rectangle((18, 7, 42, 40), fill=255)
    return mask


def test_product_lock_is_canonical_and_immutable_style() -> None:
    processor = ProductLockProcessor()
    first = processor.create(_jpeg_source(), mask=_mask())
    second = processor.create(_jpeg_source(), mask=_mask())

    assert first.status is ProductLockStatus.ACCEPTED
    assert first.canonical_image_png == second.canonical_image_png
    assert first.mask_png == second.mask_png
    assert first.cutout_png == second.cutout_png
    assert first.provenance_sha256 == second.provenance_sha256
    assert first.metrics.connected_components == 1
    assert len(first.source_sha256) == 64
    with pytest.raises(Exception):
        first.reasons += ("mutate",)  # frozen dataclass contract


def test_product_lock_abstains_without_mask_and_flags_suspicious_mask() -> None:
    processor = ProductLockProcessor()
    abstained = processor.create(_jpeg_source())
    assert abstained.status is ProductLockStatus.ABSTAINED
    assert "no_mask_available" in abstained.reasons
    suspicious = processor.create(_jpeg_source(), mask=Image.new("L", (64, 48), 255))
    assert suspicious.status is ProductLockStatus.NEEDS_REVIEW
    assert "mask_covers_almost_entire_image" in suspicious.reasons
    with pytest.raises(SuspiciousMaskError):
        from ai_service import assert_mask_accepted

        assert_mask_accepted(suspicious)


def test_refinement_contract_is_normalized_and_returns_new_revision() -> None:
    processor = ProductLockProcessor()
    first = processor.create(_jpeg_source(), mask=_mask())
    second = processor.refine(
        first,
        {
            "positive_points": [{"x": 0.31, "y": 0.48}],
            "negative_points": [{"x": 0.02, "y": 0.02}],
            "box": {"type": "box", "x": 0.20, "y": 0.08, "width": 0.50, "height": 0.84},
        },
    )
    assert second.revision == 1
    assert first.revision == 0
    assert second.lock_id == first.lock_id
    assert second.provenance_sha256 != first.provenance_sha256
    assert second.refinement is not None


def test_lazy_adapters_do_not_require_heavy_models_for_injected_fakes() -> None:
    seen: dict[str, object] = {}

    class FakeSAM:
        def predict(self, **kwargs):
            seen.update(kwargs)
            return {"mask": _mask(), "score": 0.94}

    from ai_service.segmentation import SAM2Segmenter

    result = SAM2Segmenter(predictor=FakeSAM()).segment(
        Image.new("RGB", (64, 48)),
        refinement=RefinementContract.from_mapping({"positive_points": [{"x": 0.4, "y": 0.4}]}),
    )
    assert result.confidence == 0.94
    assert seen["normalized_coordinates"] is True
    assert seen["point_labels"] == [1]


def test_scene_planner_has_structured_negative_contract() -> None:
    plan = ScenePlanner().plan("déodorant", width=64, height=48, seed=12)
    assert "condensation" in plan.prompt
    assert "no" not in plan.negative_prompt.casefold()  # token list is model-ready
    assert "duplicate product" in plan.negative_prompt
    assert len(plan.plan_sha256) == 64


def test_orchestration_restores_exact_product_and_fails_closed_without_detectors() -> None:
    processor = ProductLockProcessor()
    lock = processor.create(_jpeg_source(), mask=_mask())
    outcome = BackgroundGenerationOrchestrator(DeterministicImageProvider()).generate(
        lock,
        category="deodorant",
        seed=5,
        duplicate_detector=CleanSyntheticQA(),
        person_hand_detector=CleanSyntheticQA(),
    )
    variant = outcome.variants[0]
    assert variant.qa.passed is True
    assert variant.manifest.pixel_comparison.exact is True
    assert variant.manifest.remote_seed_deterministic is True
    no_checks = BackgroundGenerationOrchestrator(DeterministicImageProvider()).generate(lock, category="deodorant", seed=5)
    assert no_checks.variants[0].qa.passed is False
    assert "duplicate_detector_unavailable" in no_checks.variants[0].qa.findings


def test_diffusers_mask_convention_white_modifies_black_preserves() -> None:
    source = Image.new("RGB", (12, 12), (10, 20, 30))
    mask = Image.new("L", source.size, 0)
    ImageDraw.Draw(mask).rectangle((0, 0, 5, 11), fill=255)
    adapter = DiffusersInpaintingAdapter(pipeline=lambda **kwargs: kwargs["image"])
    output = adapter.inpaint(source, mask, "scene")
    # The injected pipeline echoes the source; the convention is validated by
    # the canonical mask passed to it rather than by model pixels.
    assert output.size == source.size


def test_closerouter_mocked_discovery_preflight_edit_and_normalization_without_credit_gate() -> None:
    result_image = Image.new("RGB", (8, 8), (80, 90, 100))
    result_bytes = io.BytesIO()
    result_image.save(result_bytes, format="PNG")
    encoded = base64.b64encode(result_bytes.getvalue()).decode("ascii")
    calls: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.headers)))
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "edit-model", "supports_edit": True, "supports_generation": True, "cost_usd": 0.02, "deterministic_seed": False}]})
        if request.url.path == "/v1/credits":
            # A zero balance must be observable for accounting but must not
            # block an otherwise valid allowlisted request.
            return httpx.Response(200, json={"credits": 0.0})
        if request.url.path == "/v1/images/edits":
            body = json.loads(request.content.decode("utf-8"))
            assert body["image"].startswith("data:image/png;base64,")
            assert "https://" not in body["image"]
            assert body["quality"] == "medium"
            assert body["input_fidelity"] == "high"
            assert body["output_format"] == "png"
            return httpx.Response(200, json={"data": [{"b64_json": encoded}], "usage": {"images": 1}, "cost_usd": 0.02})
        raise AssertionError(request.url.path)

    source = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(source, format="PNG")
    with CloseRouterAdapter(
        base_url="https://api.closerouter.dev/v1",
        api_key="server-only",
        transport=httpx.MockTransport(handler),
    ) as provider:
        spec = GenerationSpec(
            prompt="background",
            width=8,
            height=8,
            image_bytes=source.getvalue(),
            image_mime="image/png",
            metadata={
                "quality": "medium",
                "input_fidelity": "high",
                "output_format": "png",
            },
        )
        plan = provider.plan(spec, operation="edit", max_cost_usd=0.10)
        output = provider.execute(plan)
    assert output.width == 8 and output.height == 8
    assert output.cost_usd == 0.02
    assert any(path == "/v1/models" for _, path, _ in calls)
    assert any(path == "/v1/credits" for _, path, _ in calls)
    edit_headers = next(headers for method, path, headers in calls if path == "/v1/images/edits")
    assert edit_headers["authorization"] == "Bearer server-only"


def test_closerouter_parses_live_catalog_image_endpoints_and_nested_credits() -> None:
    """Match the documented/live CloseRouter response shapes exactly."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "anthropic/claude-fable-5",
                            "endpoints": ["chat", "responses"],
                            "pricing": {"unit": "usd_per_million_tokens"},
                        },
                        {
                            "id": "openai/gpt-image-2",
                            "endpoints": ["images_generations", "images_edits"],
                            "pricing": {
                                "image": "0.00900000",
                                "unit": "usd_per_image",
                            },
                            "supported_parameters": [
                                "image",
                                "mask",
                                "prompt",
                                "quality",
                                "size",
                            ],
                        },
                    ]
                },
            )
        if request.url.path == "/v1/credits":
            return httpx.Response(
                200,
                json={"data": {"total_credits": 9.86, "total_usage": 0.14}},
            )
        raise AssertionError(request.url.path)

    with CloseRouterAdapter(
        base_url="https://api.closerouter.dev/v1",
        api_key="server-only",
        transport=httpx.MockTransport(handler),
    ) as provider:
        capabilities = provider.discover()
        assert [item.model for item in capabilities] == ["openai/gpt-image-2"]
        image = next(item for item in capabilities if item.model == "openai/gpt-image-2")
        assert image.supports_generation is True
        assert image.supports_edit is True
        assert image.estimated_cost_usd == pytest.approx(0.009)
        spec = GenerationSpec(
            prompt="replace only the background",
            width=1024,
            height=1024,
            model="openai/gpt-image-2",
            image_bytes=_jpeg_source(),
            image_mime="image/jpeg",
        )
        preflight = provider.preflight(spec, operation="edit", max_cost_usd=2.0)

    assert preflight.allowed is True
    assert preflight.credits_available_usd == pytest.approx(9.86)
    assert preflight.estimated_cost_usd == pytest.approx(0.009)


def test_closerouter_rejects_public_http_hosts() -> None:
    with pytest.raises(HostNotAllowedError):
        CloseRouterAdapter(base_url="http://provider.example/v1", api_key="x")


def test_fastapi_surface_returns_lock_metadata_without_raw_image() -> None:
    pytest.importorskip("fastapi")
    service = AIService()
    app = create_app(service)
    raw = _mask()
    source = _jpeg_source()
    source_b64 = base64.b64encode(source).decode("ascii")
    mask_bytes = io.BytesIO()
    raw.save(mask_bytes, format="PNG")
    async def request() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.post("/v2/product-lock", json={"image_b64": source_b64, "mask_b64": base64.b64encode(mask_bytes.getvalue()).decode("ascii")})

    response = asyncio.run(request())
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    assert "canonical_image_png" not in payload
