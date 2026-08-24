from pathlib import Path
import tempfile
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from ai.gpu_runtime import RuntimeReadiness, create_app
from ai.gpu_models import GpuProductLockEngine
from ai.gpu_service import GpuV2Service
from ai_service.contracts import NormalizedBox, RefinementContract, SegmentationResult
from ai_service.image import ProductLockProcessor, encode_png, mask_metrics


ROOT = Path(__file__).parents[2]


def test_runtime_readiness_has_explicit_gates() -> None:
    state = RuntimeReadiness()
    assert state.status == "cuda_unavailable"
    state.cuda_ready = True
    state.models_loading = True
    assert state.status == "models_loading"
    state.models_loading = False
    state.inference_ready = True
    assert state.status == "inference_ready"
    assert {
        "container_up",
        "cuda_ready",
        "models_loading",
        "inference_ready",
    } <= state.payload().keys()


def test_v2_health_requires_internal_bearer(monkeypatch) -> None:
    monkeypatch.setenv("AI_SERVICE_TOKEN", "runtime-test-token")
    client = TestClient(create_app(eager=False))
    assert client.get("/v2/health").status_code == 401
    response = client.get(
        "/v2/health",
        headers={"Authorization": "Bearer runtime-test-token"},
    )
    assert response.status_code == 503
    payload = response.json()
    assert payload["container_up"] is True
    assert payload["inference_ready"] is False


def test_extraction_runtime_does_not_enable_background_generation(monkeypatch) -> None:
    monkeypatch.setenv("AI_SERVICE_TOKEN", "runtime-test-token")
    client = TestClient(create_app(eager=False))
    response = client.post(
        "/v2/generations",
        headers={"Authorization": "Bearer runtime-test-token"},
        json={"lock_id": "lock-without-prompt"},
    )
    assert response.status_code == 503
    assert "not ready" in response.json()["detail"].lower()


def test_cpu_readiness_is_a_valid_inference_gate() -> None:
    state = RuntimeReadiness(
        cuda_ready=False,
        inference_ready=True,
        engine_mode="cpu-u2net",
    )
    assert state.status == "inference_ready"
    payload = state.payload()
    assert payload["engine_mode"] == "cpu-u2net"
    assert payload["cuda_ready"] is False
    assert payload["inference_ready"] is True


def test_compose_keeps_gpu_runtime_private_and_scopes_tokens() -> None:
    compose = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    runtime_block = compose.split("  ai-runtime:", 1)[1].split("\n  db:", 1)[0]
    assert "gpus: all" in runtime_block
    assert "driver: nvidia" in runtime_block
    assert "- ai_models:/models" in runtime_block
    assert "- ai_artifacts:/artifacts" in runtime_block
    assert "ports:" not in runtime_block
    assert "HF_TOKEN:" in runtime_block
    assert "CLOSEROUTER_API_KEY" not in runtime_block


def test_runtime_dockerfile_uses_cuda_base_and_private_entrypoint() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.ai-runtime").read_text(encoding="utf-8")
    assert "pytorch/pytorch:2.11.0-cuda13.0" in dockerfile
    assert "COPY ai/" in dockerfile
    assert 'ENTRYPOINT ["uvicorn", "ai.gpu_runtime:app"' in dockerfile


def test_cpu_runtime_dockerfile_is_separate_from_cuda_runtime() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.ai-cpu").read_text(encoding="utf-8")
    requirements = (ROOT / "ai" / "requirements-cpu-extraction.lock").read_text(encoding="utf-8")
    assert "python:3.11.9-slim-bookworm" in dockerfile
    assert "requirements-cpu-extraction.lock" in dockerfile
    assert "onnxruntime==" in requirements
    assert "diffusers" not in requirements.lower()


def test_sam2_runtime_uses_single_image_model_contract() -> None:
    service = (ROOT / "ai" / "gpu_service.py").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "Dockerfile.ai-runtime").read_text(encoding="utf-8")
    assert "from sam2.build_sam import build_sam2" in service
    assert "from sam2.sam2_image_predictor import SAM2ImagePredictor" in service
    assert '"configs/sam2.1/sam2.1_hiera_l.yaml"' in service
    assert '"sam2.1_hiera_large.pt"' in service
    assert "self.predictor.set_image" in service
    assert "self.predictor.predict" in service
    assert "SAM2_BUILD_CUDA=0" in dockerfile


def test_blackwell_capability_probe_is_explicit() -> None:
    runtime = (ROOT / "ai" / "gpu_runtime.py").read_text(encoding="utf-8")
    assert "GPU_RUNTIME_REQUIRED_COMPUTE" in runtime
    assert "torch.cuda.get_device_capability()" in runtime
    assert 'torch.ones((1,), device="cuda"' in runtime


def test_product_lock_survives_runtime_recreation() -> None:
    source_image = Image.new("RGBA", (32, 32), (236, 240, 242, 255))
    mask_image = Image.new("L", source_image.size, 0)
    ImageDraw.Draw(mask_image).rectangle((8, 8, 24, 24), fill=255)
    source = encode_png(source_image, mode="RGBA")
    mask = encode_png(mask_image, mode="L")
    lock = ProductLockProcessor().create(
        source,
        mask=mask,
        lock_id="persisted-lock",
        revision=3,
    )

    engine = SimpleNamespace(create_lock=lambda *args, **kwargs: lock)
    with tempfile.TemporaryDirectory(dir=ROOT / "test-artifacts") as artifact_dir:
        artifact_root = Path(artifact_dir)
        first = GpuV2Service(
            runtime=SimpleNamespace(engine=engine),
            provider=SimpleNamespace(model="test"),
            artifact_root=artifact_root,
        )
        first.locks[lock.lock_id] = lock
        first._persist_lock(lock)

        second = GpuV2Service(
            runtime=SimpleNamespace(engine=engine),
            provider=SimpleNamespace(model="test"),
            artifact_root=artifact_root,
        )
        assert second.locks == {}
        restored = second._restore_lock("persisted-lock")
        assert restored is not None
        assert restored.source_sha256 == lock.source_sha256
        assert restored.revision == 3


def test_large_masks_use_bounded_topology_analysis() -> None:
    mask = Image.new("L", (2268, 4032), 0)
    ImageDraw.Draw(mask).rectangle((900, 900, 1368, 3000), fill=255)
    metrics = mask_metrics(mask)
    assert metrics.total_pixels == 2268 * 4032
    assert metrics.foreground_pixels == (1368 - 900 + 1) * (3000 - 900 + 1)
    assert metrics.bbox == (900, 900, 1369, 3001)
    assert "mask_too_small" not in metrics.suspicious_reasons


def test_gpu_lock_normalizes_json_target_box_before_sam2() -> None:
    source_image = Image.new("RGBA", (32, 32), (236, 240, 242, 255))
    source = encode_png(source_image, mode="RGBA")

    class ProposalAdapter:
        def propose(self, image, *, prompt):
            return ()

    class SAM2Adapter:
        def segment(self, image, *, refinement, target_box):
            assert isinstance(target_box, NormalizedBox)
            mask = Image.new("L", image.size, 0)
            ImageDraw.Draw(mask).rectangle((8, 8, 24, 24), fill=255)
            return SegmentationResult(mask=mask, confidence=0.95, model="test-sam2")

    engine = GpuProductLockEngine(proposal=ProposalAdapter(), sam2=SAM2Adapter())
    lock = engine.create_lock(
        source,
        target_box={"type": "box", "x": 0.2, "y": 0.2, "width": 0.5, "height": 0.5},
    )
    assert lock.metrics.foreground_pixels > 0


def test_guided_product_lock_does_not_add_point_to_box_prompt() -> None:
    from ai.gpu_models import GpuProductLockEngine

    contract = GpuProductLockEngine._guided_refinement(
        base=RefinementContract(),
        selected=NormalizedBox(x=0.2, y=0.2, width=0.5, height=0.5),
        contaminants=(),
    )
    assert contract.positive_points == ()
    assert contract.box == NormalizedBox(x=0.2, y=0.2, width=0.5, height=0.5)


def test_guided_product_lock_ignores_contaminant_points_outside_box() -> None:
    from ai.gpu_models import GpuProductLockEngine
    from ai_service.contracts import Proposal

    contract = GpuProductLockEngine._guided_refinement(
        base=RefinementContract(),
        selected=NormalizedBox(x=0.2, y=0.2, width=0.5, height=0.5),
        contaminants=(
            Proposal(
                box=NormalizedBox(x=0.0, y=0.0, width=0.1, height=0.1),
                score=0.9,
                label="human hand",
            ),
        ),
    )
    assert contract.negative_points == ()
