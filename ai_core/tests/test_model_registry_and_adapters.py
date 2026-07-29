from __future__ import annotations

import subprocess
import sys

import pytest
from PIL import Image

from ai_core.adapters import GroundingDinoDetector, _release_cuda, _require_cuda
from ai_core.errors import ModelPolicyError
from ai_core.model_registry import pinned_model_refs
from ai_core.schemas import CandidateBox


def test_required_models_are_bound_to_reviewed_hub_commits() -> None:
    models = pinned_model_refs()

    assert {
        name: (model.repo_id, model.revision, model.license)
        for name, model in models.items()
    } == {
        "grounding_dino": (
            "IDEA-Research/grounding-dino-tiny",
            "a2bb814dd30d776dcf7e30523b00659f4f141c71",
            "apache-2.0",
        ),
        "sam": (
            "facebook/sam-vit-base",
            "70c1a07f894ebb5b307fd9eaaee97b9dfc16068f",
            "apache-2.0",
        ),
        "sdxl": (
            "stabilityai/stable-diffusion-xl-base-1.0",
            "462165984030d82259a11f4367a4eed129e94a7b",
            "openrail++",
        ),
        "qwen": (
            "Qwen/Qwen2.5-7B-Instruct",
            "a09a35458c702b33eeacc393d103063234e8bc28",
            "apache-2.0",
        ),
    }


def test_birefnet_custom_code_is_disabled_without_explicit_review_gate() -> None:
    with pytest.raises(ModelPolicyError, match="review"):
        pinned_model_refs(enable_birefnet=True, custom_code_reviewed=False)

    assert "birefnet" not in pinned_model_refs()


def test_birefnet_requires_durable_review_evidence_before_manifest_enablement() -> None:
    with pytest.raises(ModelPolicyError, match="evidence"):
        pinned_model_refs(enable_birefnet=True, custom_code_reviewed=True)

    models = pinned_model_refs(
        enable_birefnet=True,
        custom_code_reviewed=True,
        custom_code_review_evidence=f"sha256:{'e' * 64}",
    )
    assert models["birefnet"].custom_code is True
    assert models["birefnet"].custom_code_reviewed is True


def test_importing_adapter_module_does_not_import_gpu_frameworks() -> None:
    code = (
        "import sys; import ai_core.adapters; "
        "print(','.join(name for name in ('torch','transformers','diffusers','bitsandbytes') "
        "if name in sys.modules))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == ""


def test_detector_backend_is_loaded_only_on_first_inference_and_can_unload() -> None:
    events: list[str] = []

    class Backend:
        def detect(self, image: Image.Image, prompt: str) -> tuple[CandidateBox, ...]:
            events.append(f"detect:{prompt}:{image.size}")
            return (
                CandidateBox(
                    id="candidate-1",
                    score=0.91,
                    type="box",
                    x=0.1,
                    y=0.2,
                    width=0.3,
                    height=0.6,
                ),
            )

        def close(self) -> None:
            events.append("close")

    def factory() -> Backend:
        events.append("load")
        return Backend()

    detector = GroundingDinoDetector(backend_factory=factory)
    assert detector.loaded is False
    assert events == []

    first = detector.detect(Image.new("RGB", (100, 80)), "cosmetic product")
    second = detector.detect(Image.new("RGB", (100, 80)), "cosmetic product")
    detector.unload()

    assert first == second
    assert events == [
        "load",
        "detect:cosmetic product:(100, 80)",
        "detect:cosmetic product:(100, 80)",
        "close",
    ]
    assert detector.loaded is False


def test_cuda_preflight_fails_before_any_model_loader_is_needed() -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class Torch:
        cuda = Cuda()

    with pytest.raises(ModelPolicyError, match="CUDA"):
        _require_cuda(Torch())


def test_cuda_release_collects_python_cache_and_ipc(monkeypatch) -> None:
    events: list[str] = []

    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def empty_cache() -> None:
            events.append("empty_cache")

        @staticmethod
        def ipc_collect() -> None:
            events.append("ipc_collect")

    class Torch:
        cuda = Cuda()

    monkeypatch.setattr(
        "ai_core.adapters.gc.collect",
        lambda: events.append("gc.collect"),
    )
    _release_cuda(Torch())

    assert events == ["gc.collect", "empty_cache", "ipc_collect"]
