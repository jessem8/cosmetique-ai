"""Extraction-only GPU service and real Hugging Face model adapters.

This module is the private in-container entry point. It never imports or calls
the historical V1 poster pipeline. The runtime loads Grounding DINO and native
SAM2 once, then exposes the product-lock contract over an authenticated API.
Background generation is deliberately disabled in this runtime.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, ImageOps

from ai_service.contracts import (
    CostPreflight,
    ExecutionPlan,
    GenerationSpec,
    NormalizedImageResult,
    sha256_bytes,
    new_request_id,
)
from ai_service.diffusers_adapter import DiffusersInpaintingAdapter, FakeInpaintingAdapter
from ai_service.image import ProductLockProcessor, decode_image, encode_png
from ai_service.orchestration import BackgroundGenerationOrchestrator
from ai_service.segmentation import U2NetSegmenter

from ai.gpu_models import (
    GpuProductLockEngine,
    GpuModelRuntime,
    ModelRegistry,
    ResolvedModel,
    RuntimeUnavailable,
)


class TransformersGroundingDINO:
    """Adapter for Transformers Grounding DINO zero-shot proposals."""

    def __init__(self, processor: Any, model: Any, device: str) -> None:
        self.processor = processor
        self.model = model
        self.device = device

    def propose(self, image: Image.Image, prompt: str) -> tuple[dict[str, Any], ...]:
        import torch

        inputs = _prepare_inputs(
            self.processor(
                images=image.convert("RGB"),
                text=[[prompt]],
                return_tensors="pt",
            ),
            self.model,
            self.device,
        )
        with torch.inference_mode():
            outputs = self.model(**inputs)
        results = self.processor.post_process_grounded_object_detection(
            outputs,
            inputs["input_ids"],
            threshold=float(os.getenv("GPU_RUNTIME_DINO_THRESHOLD", "0.35")),
            text_threshold=float(os.getenv("GPU_RUNTIME_DINO_TEXT_THRESHOLD", "0.25")),
            target_sizes=[(image.height, image.width)],
        )
        result = results[0]
        boxes = result.get("boxes", ())
        scores = result.get("scores", ())
        labels = result.get("text_labels")
        if labels is None:
            labels = result.get("labels", ())
        proposals: list[dict[str, Any]] = []
        for index, (box, score) in enumerate(zip(boxes, scores)):
            values = [float(item) for item in box.detach().cpu().tolist()]
            proposals.append(
                {
                    "box": {"x1": values[0], "y1": values[1], "x2": values[2], "y2": values[3]},
                    "score": float(score.detach().cpu().item()),
                    "label": str(labels[index]) if index < len(labels) else prompt,
                    "id": f"grounding-dino-{index}",
                }
            )
        return tuple(proposals)


class NativeSAM2Predictor:
    """Adapter for Meta's native static-image SAM2 predictor."""

    def __init__(self, predictor: Any, device: str) -> None:
        self.predictor = predictor
        self.device = device

    def predict(
        self,
        *,
        image: Image.Image,
        points: Sequence[tuple[float, float]],
        point_labels: Sequence[int],
        box: Mapping[str, Any] | None,
        normalized_coordinates: bool = True,
    ) -> dict[str, Any]:
        import numpy as np
        import torch

        if box is None or not normalized_coordinates:
            raise RuntimeUnavailable("SAM2 requires a normalized product box")
        pixel_box = np.asarray(
            [
                float(box["x"] * image.width),
                float(box["y"] * image.height),
                float((box["x"] + box["width"]) * image.width),
                float((box["y"] + box["height"]) * image.height),
            ],
            dtype=np.float32,
        )
        point_coords = None
        labels = None
        if points:
            point_coords = np.asarray(
                [[float(x * image.width), float(y * image.height)] for x, y in points],
                dtype=np.float32,
            )
            labels = np.asarray(point_labels, dtype=np.int32)
        with torch.inference_mode():
            with torch.autocast(self.device, dtype=torch.bfloat16, enabled=self.device == "cuda"):
                self.predictor.set_image(np.asarray(image.convert("RGB")))
                masks, scores, _ = self.predictor.predict(
                    point_coords=point_coords,
                    point_labels=labels,
                    box=pixel_box,
                    multimask_output=False,
                )
        return {"mask": masks[0], "score": float(scores[0]) if len(scores) else None}


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _prepare_inputs(inputs: Any, model: Any, device: str) -> dict[str, Any]:
    """Return plain tensors with floating inputs matched to CUDA weights."""
    import torch

    moved = inputs.to(device)
    dtypes = [
        parameter.dtype
        for parameter in model.parameters()
        if parameter.is_floating_point()
    ]
    if not dtypes:
        return dict(moved.items())
    # Some vision checkpoints retain a few FP32 parameters while convolution
    # weights are FP16. On a T4 the input must follow the CUDA convolution dtype.
    model_dtype = torch.float16 if device == "cuda" and torch.float16 in dtypes else dtypes[0]
    prepared: dict[str, Any] = {}
    for name, value in moved.items():
        if hasattr(value, "is_floating_point") and value.is_floating_point():
            prepared[name] = value.to(device=device, dtype=model_dtype)
        elif hasattr(value, "to"):
            prepared[name] = value.to(device)
        else:
            prepared[name] = value
    return prepared


def _load_grounding_dino(model: ResolvedModel, device: str) -> TransformersGroundingDINO:
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    processor = AutoProcessor.from_pretrained(model.path)
    load_options: dict[str, Any] = {
        # Keep detector weights and processor pixels in FP32. These models are
        # released before SDXL loads, so correctness is worth the small memory cost.
        "dtype": torch.float32,
        "low_cpu_mem_usage": True,
    }
    loaded = AutoModelForZeroShotObjectDetection.from_pretrained(model.path, **load_options)
    loaded.to(device).float().eval()
    return TransformersGroundingDINO(processor, loaded, device)


def _load_sam2(model: ResolvedModel, device: str) -> NativeSAM2Predictor:
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    checkpoint = Path(model.path) / "sam2.1_hiera_large.pt"
    loaded = build_sam2(
        "configs/sam2.1/sam2.1_hiera_l.yaml",
        ckpt_path=str(checkpoint),
        device=device,
    )
    return NativeSAM2Predictor(SAM2ImagePredictor(loaded), device)


def _load_inpainting(model: ResolvedModel, device: str) -> DiffusersInpaintingAdapter:
    import logging
    import torch
    from diffusers import StableDiffusionXLInpaintPipeline

    options: dict[str, Any] = {
        # Diffusers 0.35.x expects torch_dtype here. Passing the newer
        # dtype keyword is ignored by this pinned runtime and materializes
        # SDXL in FP32, which exceeds the 8 GB Docker memory budget.
        "torch_dtype": torch.float16 if device == "cuda" else torch.float32,
        "use_safetensors": True,
        "low_cpu_mem_usage": True,
    }
    if device == "cuda":
        options["variant"] = "fp16"
    # This checkpoint carries legacy EMA fields in its UNet config. Diffusers
    # deliberately ignores them; keep that known metadata warning from
    # obscuring real load failures, while restoring the logger afterwards.
    diffusers_logger = logging.getLogger("diffusers")
    previous_level = diffusers_logger.level
    diffusers_logger.setLevel(logging.ERROR)
    try:
        pipeline = StableDiffusionXLInpaintPipeline.from_pretrained(model.path, **options)
    finally:
        diffusers_logger.setLevel(previous_level)
    if device == "cuda" and hasattr(pipeline, "enable_sequential_cpu_offload"):
        # Sequential offload keeps the detector and SDXL families from being
        # resident together on an 8 GB RTX 5050.
        pipeline.enable_sequential_cpu_offload()
    elif device == "cuda" and hasattr(pipeline, "enable_model_cpu_offload"):
        pipeline.enable_model_cpu_offload()
    else:
        pipeline.to(device)
    # These switches are no-ops on older Diffusers releases but materially
    # reduce peak activation memory when supported by the pipeline.
    if hasattr(pipeline, "enable_vae_tiling"):
        pipeline.enable_vae_tiling()
    if hasattr(pipeline, "enable_attention_slicing"):
        pipeline.enable_attention_slicing("max")
    return DiffusersInpaintingAdapter(pipeline=pipeline, model_id=model.repo_id)


def _real_factories(device: str) -> dict[str, Callable[[ResolvedModel], Any]]:
    return {
        "grounding_dino": lambda model: _load_grounding_dino(model, device),
        "sam2": lambda model: _load_sam2(model, device),
    }


class GpuDiffusersProvider:
    """Provider contract with lazy SDXL loading for constrained T4 RAM."""

    provider_name = "gpu"

    def __init__(
        self,
        adapter: DiffusersInpaintingAdapter | None,
        model: str,
        *,
        runtime: GpuModelRuntime | None = None,
        adapter_factory: Callable[[], DiffusersInpaintingAdapter] | None = None,
    ) -> None:
        self.adapter = adapter
        self.model = model
        self.runtime = runtime
        self.adapter_factory = adapter_factory

    def _ensure_adapter(self) -> DiffusersInpaintingAdapter:
        if self.adapter is None:
            if self.runtime is None or self.adapter_factory is None:
                raise RuntimeUnavailable("GPU Diffusers adapter is not configured")
            self.runtime.suspend_detectors()
            self.adapter = self.adapter_factory()
        return self.adapter

    def _release_adapter(self) -> None:
        if self.runtime is None:
            return
        self.adapter = None
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        self.runtime.resume_detectors()

    def discover(self) -> tuple[Any, ...]:
        from ai_service.contracts import ProviderCapabilities

        return (
            ProviderCapabilities(
                provider="gpu",
                model=self.model,
                endpoint="local-diffusers-inpainting",
                supports_generation=True,
                supports_edit=True,
                supports_inpainting=True,
                deterministic_seed=False,
                estimated_cost_usd=0.0,
            ),
        )

    def preflight(self, spec: GenerationSpec, *, operation: str = "generation", max_cost_usd: float | None = None) -> CostPreflight:
        if operation.casefold() in {"edit", "inpaint", "inpainting"} and not spec.image_bytes:
            return CostPreflight(False, 0.0, reason="source image bytes are required")
        return CostPreflight(True, 0.0)

    def plan(self, spec: GenerationSpec, *, operation: str = "generation", max_cost_usd: float | None = None) -> ExecutionPlan:
        preflight = self.preflight(spec, operation=operation, max_cost_usd=max_cost_usd)
        if not preflight.allowed:
            raise RuntimeUnavailable(preflight.reason or "GPU provider preflight failed")
        return ExecutionPlan(
            request_id=new_request_id(),
            provider="gpu",
            operation=operation,
            capabilities=self.discover()[0],
            spec=spec,
            estimated_cost_usd=0.0,
        )

    def execute(self, plan: ExecutionPlan) -> NormalizedImageResult:
        if not plan.spec.image_bytes or not plan.spec.mask_bytes:
            raise RuntimeUnavailable("source and product mask are required")
        try:
            adapter = self._ensure_adapter()
            source = decode_image(plan.spec.image_bytes).convert("RGB")
            raw_mask = Image.open(io.BytesIO(plan.spec.mask_bytes))
            mask = raw_mask.getchannel("A") if raw_mask.mode in {"RGBA", "LA"} else raw_mask.convert("L")
            # Shared V2 passes an opaque product hint; Diffusers white means edit.
            edit_mask = ImageOps.invert(mask)
            output = adapter.inpaint(
                source,
                edit_mask,
                plan.spec.prompt,
                negative_prompt=plan.spec.negative_prompt,
                seed=plan.spec.seed,
                width=plan.spec.width,
                height=plan.spec.height,
                num_inference_steps=int(os.getenv("GPU_RUNTIME_INPAINT_STEPS", "30")),
                guidance_scale=float(os.getenv("GPU_RUNTIME_INPAINT_GUIDANCE", "7.0")),
            ).convert("RGB")
            payload = encode_png(output, mode="RGB")
            return NormalizedImageResult(
                image_bytes=payload,
                mime_type="image/png",
                width=output.width,
                height=output.height,
                sha256=sha256_bytes(payload),
                request_id=plan.request_id,
                provider="gpu",
                model=self.model,
                usage={"runtime": "gpu", "billable": False},
                cost_usd=0.0,
                deterministic_seed_claim=False,
            )
        finally:
            # Keep only one heavy model family resident at a time on a T4.
            self._release_adapter()


@dataclass
class GpuSafetyDetectors:
    runtime: GpuModelRuntime

    def _engine(self) -> GpuProductLockEngine:
        if self.runtime.engine is None:
            self.runtime.resume_detectors()
        if self.runtime.engine is None:
            raise RuntimeUnavailable("detector engine is unavailable for QA")
        return self.runtime.engine

    def has_duplicate_product(self, image: Image.Image, lock: Any) -> bool | None:
        try:
            engine = self._engine()
            proposals = engine._proposals(image, "cosmetic product packaging")
            return engine._distinct_product_count(proposals) > 1
        except Exception:
            return None


@dataclass
class CpuProductLockRuntime:
    """Low-memory extraction runtime for Pascal and CPU-only machines."""

    engine: Any
    runtime_id: str = field(default_factory=lambda: f"cpu-u2net-{uuid.uuid4().hex[:12]}")
    status: str = "ready"
    reason: str | None = None

    def health(self) -> dict[str, Any]:
        return {
            "primary_engine": "cpu-u2net",
            "status": self.status,
            "runtime_id": self.runtime_id,
            "models": {"u2net": {"repo_id": "danielgatis/rembg/u2net.onnx", "execution_provider": "CPUExecutionProvider"}},
            "reason": self.reason,
        }


class CpuProductLockEngine:
    """Product-lock adapter backed by the lazy CPU ONNX U2Net segmenter."""

    def __init__(self, *, model_dir: str | Path) -> None:
        self.processor = ProductLockProcessor(
            segmenter=U2NetSegmenter(model_dir=model_dir, model="u2net"),
            min_score=0.65,
            accept_model_confidence=0.0,
            strict=False,
        )

    def create_lock(self, source: bytes, *, refinement: Any = None, target_box: Any = None) -> Any:
        return self.processor.create(
            source,
            refinement=refinement,
            target_box=target_box,
        )

    def refine(self, lock: Any, source: bytes, refinement: Mapping[str, Any]) -> Any:
        return self.processor.refine(lock, refinement)

    def detect_person_or_hands(self, image: Image.Image) -> Mapping[str, bool] | None:
        try:
            engine = self._engine()
            people = engine._proposals(image, "person")
            hands = engine._proposals(image, "human hand")
            return {
                "person": any(item.score >= 0.45 for item in people),
                "hands": any(item.score >= 0.45 for item in hands),
            }
        except Exception:
            return None


@dataclass
class GpuV2Service:
    runtime: GpuModelRuntime
    provider: Any
    locks: dict[str, Any] = field(default_factory=dict)
    generations: dict[str, Any] = field(default_factory=dict)
    generation_artifacts: dict[str, tuple[bytes, ...]] = field(default_factory=dict)
    artifact_root: Path = field(
        default_factory=lambda: Path(os.getenv("ARTIFACT_ROOT", "/artifacts"))
    )
    persistence_errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock_root().mkdir(parents=True, exist_ok=True)
        # Do not scan and decode every historical source/mask at startup.
        # A lock is restored on demand by its explicit ID, keeping startup
        # independent of the size of the artifact volume.

    def _lock_root(self) -> Path:
        return self.artifact_root / "product-locks"

    def _lock_path(self, lock_id: str) -> Path:
        safe_id = str(lock_id)
        if not safe_id or Path(safe_id).name != safe_id or safe_id in {".", ".."}:
            raise ValueError("product lock id is invalid")
        return self._lock_root() / safe_id

    def _generation_artifact_path(self, generation_id: str, index: int = 0) -> Path:
        safe_id = str(generation_id)
        if not safe_id or Path(safe_id).name != safe_id or safe_id in {".", ".."}:
            raise ValueError("generation id is invalid")
        return self.artifact_root / "generations" / safe_id / f"baseline-{index + 1}.png"

    def _persist_lock(self, lock: Any) -> None:
        directory = self._lock_path(lock.lock_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "source.png").write_bytes(lock.canonical_image_png)
            (directory / "mask.png").write_bytes(lock.mask_png)
            (directory / "lock.json").write_text(
                json.dumps(
                    {
                        "lock_id": lock.lock_id,
                        "revision": lock.revision,
                        "source_sha256": lock.source_sha256,
                        "source_mime": lock.source_mime,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeUnavailable("product lock artifacts could not be persisted") from exc

    def _restore_lock(self, lock_id: str) -> Any | None:
        directory = self._lock_path(lock_id)
        metadata_path = directory / "lock.json"
        if not metadata_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            stored_id = str(metadata.get("lock_id") or lock_id)
            if stored_id != lock_id or Path(stored_id).name != stored_id:
                raise ValueError("persisted product lock id is invalid")
            source = (directory / "source.png").read_bytes()
            mask = (directory / "mask.png").read_bytes()
            lock = ProductLockProcessor().create(
                source,
                mask=mask,
                source_mime=str(metadata.get("source_mime") or "image/png"),
                lock_id=stored_id,
                revision=int(metadata.get("revision") or 0),
                source_sha256=str(metadata["source_sha256"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.persistence_errors.append(str(lock_id))
            return None
        self.locks[lock.lock_id] = lock
        return lock

    def _load_persisted_locks(self) -> None:
        root = self._lock_root()
        if not root.is_dir():
            return
        for directory in root.iterdir():
            if directory.is_dir():
                self._restore_lock(directory.name)

    def health(self) -> dict[str, Any]:
        payload = self.runtime.health()
        payload["primary_engine"] = "gpu-v2"
        payload.update(
            {
                "service": "campaign-studio-product-extraction",
                "capability": "product-extraction",
                "background_generation": False,
                "generation_provider": "disabled",
                "generation_model": None,
                "generation_loaded": False,
            }
        )
        return payload

    def create_lock(self, source: bytes, *, refinement: Any = None, target_box: Any = None) -> Any:
        if self.runtime.engine is None:
            raise RuntimeUnavailable("V2 runtime is not ready")
        lock = self.runtime.engine.create_lock(source, refinement=refinement, target_box=target_box)
        self.locks[lock.lock_id] = lock
        self._persist_lock(lock)
        return lock

    def refine_lock(self, lock_id: str, source: bytes, refinement: Mapping[str, Any]) -> Any:
        if lock_id not in self.locks:
            self._restore_lock(lock_id)
        if lock_id not in self.locks or self.runtime.engine is None:
            raise KeyError("product lock not found")
        lock = self.runtime.engine.refine(self.locks[lock_id], source, refinement)
        self.locks[lock.lock_id] = lock
        self._persist_lock(lock)
        return lock

    def generate(self, lock_id: str, *, category: str, variants: int = 1, seed: int | None = None, creative_direction: str | None = None) -> tuple[str, Any]:
        raise RuntimeUnavailable("background generation is disabled; this runtime only extracts products")

        # Historical generation implementation retained below for fixture
        # compatibility. It is unreachable in the production extraction service.
        lock = self.locks.get(lock_id) or self._restore_lock(lock_id)
        if lock is None or self.runtime.engine is None:
            raise KeyError("product lock not found")
        detectors = GpuSafetyDetectors(self.runtime)
        outcome = BackgroundGenerationOrchestrator(self.provider).generate(
            lock,
            category=category,
            variants=variants,
            seed=seed,
            creative_direction=creative_direction,
            duplicate_detector=detectors,
            person_hand_detector=detectors,
            require_detectors=True,
        )
        generation_id = str(uuid.uuid4())
        self.generations[generation_id] = outcome
        self.generation_artifacts[generation_id] = tuple(
            encode_png(item.image, mode="RGBA") for item in outcome.variants
        )
        for index, image_bytes in enumerate(self.generation_artifacts[generation_id]):
            path = self._generation_artifact_path(generation_id, index)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes)
        return generation_id, outcome

    def batch(self, items: Sequence[tuple[str, bytes]], *, category: str, seed: int = 42, creative_direction: str | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for index, (name, source) in enumerate(items):
            row: dict[str, Any] = {"index": index, "source_name": name}
            try:
                lock = self.create_lock(source)
                row["lock"] = lock.to_dict()
                if not lock.accepted:
                    row["status"] = "needs_review"
                    row["reason"] = list(lock.reasons)
                    results.append(row)
                    continue
                generation_id, outcome = self.generate(
                    lock.lock_id,
                    category=category,
                    seed=seed + index,
                    creative_direction=creative_direction,
                )
                row["generation_id"] = generation_id
                row["status"] = "ready" if all(item.qa.passed for item in outcome.variants) else "needs_review"
                row["variants"] = [item.manifest.to_dict() for item in outcome.variants]
            except Exception as exc:
                row["status"] = "failed"
                row["reason"] = f"{type(exc).__name__}: {exc}"[:500]
            results.append(row)
        return results

    def generation_payload(self, generation_id: str, outcome: Any) -> dict[str, Any]:
        manifests = [item.manifest.to_dict() for item in outcome.variants]
        first = manifests[0] if manifests else {}
        usage = first.get("usage") if isinstance(first.get("usage"), dict) else {}
        overall_status = "ready" if all(item.qa.passed for item in outcome.variants) else "needs_review"
        artifact_key = f"generations/{generation_id}/baseline-1.png"
        image_bytes = self.generation_artifacts.get(generation_id, (b"",))[0]
        baseline = {
            "stage": "baseline",
            "status": "ready",
            "artifact_key": artifact_key,
            "artifact_url": f"/v2/generations/{generation_id}/variants/0/image",
            "sha256": sha256_bytes(image_bytes),
            "mime": "image/png",
            "width": outcome.variants[0].image.width if outcome.variants else None,
            "height": outcome.variants[0].image.height if outcome.variants else None,
            "bytes": len(image_bytes),
            "model": first.get("model"),
            "prompt_sha256": first.get("prompt_sha256"),
            "seed": first.get("seed"),
            "provider_request_id": usage.get("provider_request_id"),
            "qa": first.get("qa"),
        }
        return {
            "id": generation_id,
            "status": overall_status,
            "model": first.get("model"),
            "prompt_sha256": first.get("prompt_sha256"),
            "seed": first.get("seed"),
            "provider_request_id": usage.get("provider_request_id"),
            "baseline": baseline,
            "scene_plan": {
                "category": outcome.scene_plan.category,
                "prompt": outcome.scene_plan.prompt,
                "negative_prompt": outcome.scene_plan.negative_prompt,
                "plan_sha256": outcome.scene_plan.plan_sha256,
            },
            "variants": manifests,
        }


class ExtractionOnlyProvider:
    """Explicit disabled provider that prevents accidental scene generation."""

    provider_name = "product-extraction"
    model = "native-sam2"
    adapter = None

    def discover(self) -> tuple[Any, ...]:
        return ()

    def plan(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeUnavailable("background generation is disabled; this runtime only extracts products")

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeUnavailable("background generation is disabled; this runtime only extracts products")


def build_service(*, hf_token: str | None = None, cache_dir: str = "/content/campaign-studio-models") -> GpuV2Service:
    device = _device()
    if device != "cuda":
        raise RuntimeUnavailable("a CUDA GPU runtime is required for Campaign Studio V2")
    runtime = GpuModelRuntime(ModelRegistry(cache_dir=cache_dir))
    runtime.start(_real_factories(device), token=hf_token)
    return GpuV2Service(runtime=runtime, provider=ExtractionOnlyProvider())


def build_cpu_service(*, cache_dir: str = "/models") -> GpuV2Service:
    runtime = CpuProductLockRuntime(
        engine=CpuProductLockEngine(model_dir=Path(cache_dir) / "segmentation"),
        reason="GPU unavailable or below the native SAM2 capability threshold; using CPU ONNX extraction.",
    )
    return GpuV2Service(runtime=runtime, provider=ExtractionOnlyProvider())


def create_app(service: GpuV2Service) -> Any:
    """Create the authenticated V2-only FastAPI application."""
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.responses import Response

    globals().update({"Request": Request, "Response": Response, "UploadFile": UploadFile})
    app = FastAPI(title="Campaign Studio V2 GPU", version="2.0.0")

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        expected = os.getenv("AI_SERVICE_TOKEN", "").strip()
        if expected and request.url.path.startswith("/v2/"):
            supplied = request.headers.get("authorization", "")
            if not secrets.compare_digest(supplied, f"Bearer {expected}"):
                return Response(
                    json.dumps({"detail": "GPU V2 bearer authentication failed"}),
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    @app.get("/v2/health")
    def health() -> dict[str, Any]:
        return service.health()

    @app.post("/v2/product-locks")
    async def create_lock(image: UploadFile = File(...), target_box: str | None = Form(default=None), refinement: str | None = Form(default=None)) -> dict[str, Any]:
        try:
            source = await image.read()
            lock = service.create_lock(
                source,
                target_box=json.loads(target_box) if target_box else None,
                refinement=json.loads(refinement) if refinement else None,
            )
            return lock.to_dict()
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/v2/product-locks/{lock_id}")
    def get_lock(lock_id: str) -> dict[str, Any]:
        lock = service.locks.get(lock_id)
        if lock is None:
            raise HTTPException(status_code=404, detail="product lock not found")
        return lock.to_dict()

    @app.post("/v2/product-locks/{lock_id}/refinements")
    async def refine_lock(lock_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            source = base64.b64decode(str(payload.get("source_b64", "")), validate=True)
            return service.refine_lock(lock_id, source, payload.get("refinement", {})).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v2/product-locks/{lock_id}/artifacts/{artifact}")
    def lock_artifact(lock_id: str, artifact: str) -> Response:
        lock = service.locks.get(lock_id)
        if lock is None:
            raise HTTPException(status_code=404, detail="product lock not found")
        values = {"canonical.png": (lock.canonical_image_png, "image/png"), "mask.png": (lock.mask_png, "image/png"), "cutout.png": (lock.cutout_png, "image/png")}
        if artifact not in values:
            raise HTTPException(status_code=404, detail="artifact not found")
        body, media_type = values[artifact]
        return Response(body, media_type=media_type, headers={"Cache-Control": "private, no-store"})

    @app.post("/v2/generations")
    async def generate(payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            generation_id, outcome = service.generate(
                str(payload.get("lock_id", "")),
                category=str(payload.get("category", "cosmetics")),
                variants=int(payload.get("variants", payload.get("variant_count", 1))),
                seed=None if payload.get("seed") is None else int(payload["seed"]),
                creative_direction=payload.get("creative_direction"),
            )
            return service.generation_payload(generation_id, outcome)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RuntimeUnavailable, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v2/generations/{generation_id}")
    def get_generation(generation_id: str) -> dict[str, Any]:
        outcome = service.generations.get(generation_id)
        if outcome is None:
            raise HTTPException(status_code=404, detail="generation not found")
        return service.generation_payload(generation_id, outcome)

    @app.get("/v2/generations/{generation_id}/variants/{variant_index}/image")
    def generation_image(generation_id: str, variant_index: int) -> Response:
        outcome = service.generations.get(generation_id)
        artifacts = service.generation_artifacts.get(generation_id, ())
        if outcome is None or variant_index < 0:
            raise HTTPException(status_code=404, detail="generation variant not found")
        if variant_index >= len(artifacts):
            path = service._generation_artifact_path(generation_id, variant_index)
            if not path.is_file():
                raise HTTPException(status_code=404, detail="generation variant not found")
            artifacts = (path.read_bytes(),)
        return Response(artifacts[variant_index], media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @app.post("/v2/batches")
    async def batch(images: list[UploadFile] = File(...), category: str = Form(default="cosmetics"), creative_direction: str | None = Form(default=None)) -> dict[str, Any]:
        items = [(image.filename or f"image-{index + 1}", await image.read()) for index, image in enumerate(images)]
        results = service.batch(items, category=category, creative_direction=creative_direction)
        return {"status": "completed", "total": len(results), "items": results}

    return app


__all__ = [
    "ExtractionOnlyProvider",
    "CpuProductLockEngine",
    "CpuProductLockRuntime",
    "build_cpu_service",
    "GpuDiffusersProvider",
    "GpuSafetyDetectors",
    "GpuV2Service",
    "FakeInpaintingAdapter",
    "TransformersGroundingDINO",
    "NativeSAM2Predictor",
    "build_service",
    "create_app",
]
