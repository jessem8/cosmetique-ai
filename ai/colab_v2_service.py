"""V2-only Colab service and real Hugging Face model adapters.

This module is the notebook-facing entry point. It never imports or calls the
historical V1 poster pipeline. The runtime loads Grounding DINO, SAM2, and the
Diffusers inpainting checkpoint once, then exposes product-lock, generation,
and batch contracts over an authenticated API.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import os
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
from ai_service.image import decode_image, encode_png
from ai_service.orchestration import BackgroundGenerationOrchestrator

from ai.colab_v2_runtime import (
    ColabProductLockEngine,
    ColabRuntime,
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
            inputs.input_ids,
            threshold=float(os.getenv("COLAB_DINO_THRESHOLD", "0.35")),
            text_threshold=float(os.getenv("COLAB_DINO_TEXT_THRESHOLD", "0.25")),
            target_sizes=[(image.height, image.width)],
        )
        result = results[0]
        boxes = result.get("boxes", ())
        scores = result.get("scores", ())
        labels = result.get("text_labels", result.get("labels", ()))
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


class TransformersSAM2Predictor:
    """Adapter for SAM2 box and positive/negative point refinement."""

    def __init__(self, processor: Any, model: Any, device: str) -> None:
        self.processor = processor
        self.model = model
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
        import torch

        if box is None or not normalized_coordinates:
            raise RuntimeUnavailable("SAM2 requires a normalized product box")
        pixel_box = [
            float(box["x"] * image.width),
            float(box["y"] * image.height),
            float((box["x"] + box["width"]) * image.width),
            float((box["y"] + box["height"]) * image.height),
        ]
        kwargs: dict[str, Any] = {"input_boxes": [[pixel_box]]}
        if points:
            kwargs["input_points"] = [[
                [[float(x * image.width), float(y * image.height)] for x, y in points]
            ]]
            kwargs["input_labels"] = [[list(point_labels)]]
        inputs = _prepare_inputs(
            self.processor(image.convert("RGB"), return_tensors="pt", **kwargs),
            self.model,
            self.device,
        )
        with torch.inference_mode():
            outputs = self.model(**inputs, multimask_output=True)
        masks = self.processor.image_processor.post_process_masks(
            outputs.pred_masks.cpu(),
            inputs["original_sizes"].cpu(),
        )[0][0]
        scores = outputs.iou_scores[0][0].detach().cpu()
        index = int(scores.argmax().item()) if scores.numel() else 0
        mask = masks[index].numpy() if hasattr(masks[index], "numpy") else masks[index]
        return {"mask": mask, "score": float(scores[index].item()) if scores.numel() else None}


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _prepare_inputs(inputs: Any, model: Any, device: str) -> Any:
    """Move processor tensors and match floating inputs to model dtype."""
    inputs = inputs.to(device)
    try:
        model_dtype = next(parameter for parameter in model.parameters() if parameter.is_floating_point()).dtype
    except StopIteration:
        return inputs
    for name, value in inputs.items():
        if hasattr(value, "is_floating_point") and value.is_floating_point():
            inputs[name] = value.to(dtype=model_dtype)
    return inputs


def _load_grounding_dino(model: ResolvedModel, device: str) -> TransformersGroundingDINO:
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    processor = AutoProcessor.from_pretrained(model.path)
    load_options: dict[str, Any] = {
        "dtype": torch.float16 if device == "cuda" else torch.float32,
        "low_cpu_mem_usage": True,
    }
    loaded = AutoModelForZeroShotObjectDetection.from_pretrained(model.path, **load_options)
    loaded.to(device).eval()
    return TransformersGroundingDINO(processor, loaded, device)


def _load_sam2(model: ResolvedModel, device: str) -> TransformersSAM2Predictor:
    import torch
    from transformers import AutoModel, AutoProcessor

    processor = AutoProcessor.from_pretrained(model.path)
    load_options: dict[str, Any] = {
        "dtype": torch.float16 if device == "cuda" else torch.float32,
        "low_cpu_mem_usage": True,
    }
    loaded = AutoModel.from_pretrained(model.path, **load_options)
    loaded.to(device).eval()
    return TransformersSAM2Predictor(processor, loaded, device)


def _load_inpainting(model: ResolvedModel, device: str) -> DiffusersInpaintingAdapter:
    import logging
    import torch
    from diffusers import StableDiffusionXLInpaintPipeline

    options: dict[str, Any] = {
        "dtype": torch.float16 if device == "cuda" else torch.float32,
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
        pipeline.enable_sequential_cpu_offload()
    elif device == "cuda" and hasattr(pipeline, "enable_model_cpu_offload"):
        pipeline.enable_model_cpu_offload()
    else:
        pipeline.to(device)
    return DiffusersInpaintingAdapter(pipeline=pipeline, model_id=model.repo_id)


def _real_factories(device: str) -> dict[str, Callable[[ResolvedModel], Any]]:
    return {
        "grounding_dino": lambda model: _load_grounding_dino(model, device),
        "sam2": lambda model: _load_sam2(model, device),
    }


class ColabDiffusersProvider:
    """Provider contract with lazy SDXL loading for constrained T4 RAM."""

    provider_name = "colab"

    def __init__(
        self,
        adapter: DiffusersInpaintingAdapter | None,
        model: str,
        *,
        runtime: ColabRuntime | None = None,
        adapter_factory: Callable[[], DiffusersInpaintingAdapter] | None = None,
    ) -> None:
        self.adapter = adapter
        self.model = model
        self.runtime = runtime
        self.adapter_factory = adapter_factory

    def _ensure_adapter(self) -> DiffusersInpaintingAdapter:
        if self.adapter is None:
            if self.runtime is None or self.adapter_factory is None:
                raise RuntimeUnavailable("Colab Diffusers adapter is not configured")
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
                provider="colab",
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
            raise RuntimeUnavailable(preflight.reason or "Colab provider preflight failed")
        return ExecutionPlan(
            request_id=new_request_id(),
            provider="colab",
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
                num_inference_steps=int(os.getenv("COLAB_INPAINT_STEPS", "30")),
                guidance_scale=float(os.getenv("COLAB_INPAINT_GUIDANCE", "7.0")),
            ).convert("RGB")
            payload = encode_png(output, mode="RGB")
            return NormalizedImageResult(
                image_bytes=payload,
                mime_type="image/png",
                width=output.width,
                height=output.height,
                sha256=sha256_bytes(payload),
                request_id=plan.request_id,
                provider="colab",
                model=self.model,
                usage={"runtime": "colab", "billable": False},
                cost_usd=0.0,
                deterministic_seed_claim=False,
            )
        finally:
            # Keep only one heavy model family resident at a time on a T4.
            self._release_adapter()


@dataclass
class ColabSafetyDetectors:
    runtime: ColabRuntime

    def _engine(self) -> ColabProductLockEngine:
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
class ColabV2Service:
    runtime: ColabRuntime
    provider: ColabDiffusersProvider
    locks: dict[str, Any] = field(default_factory=dict)
    generations: dict[str, Any] = field(default_factory=dict)

    def health(self) -> dict[str, Any]:
        payload = self.runtime.health()
        payload["primary_engine"] = "colab-v2"
        payload.update(
            {
                "service": "campaign-studio-v2-colab",
                "generation_provider": "colab-diffusers",
                "generation_model": self.provider.model,
                "generation_loaded": self.provider.adapter is not None,
            }
        )
        return payload

    def create_lock(self, source: bytes, *, refinement: Any = None, target_box: Any = None) -> Any:
        if self.runtime.engine is None:
            raise RuntimeUnavailable("V2 runtime is not ready")
        lock = self.runtime.engine.create_lock(source, refinement=refinement, target_box=target_box)
        self.locks[lock.lock_id] = lock
        return lock

    def refine_lock(self, lock_id: str, source: bytes, refinement: Mapping[str, Any]) -> Any:
        if lock_id not in self.locks or self.runtime.engine is None:
            raise KeyError("product lock not found")
        lock = self.runtime.engine.refine(self.locks[lock_id], source, refinement)
        self.locks[lock.lock_id] = lock
        return lock

    def generate(self, lock_id: str, *, category: str, variants: int = 1, seed: int | None = None, creative_direction: str | None = None) -> tuple[str, Any]:
        lock = self.locks.get(lock_id)
        if lock is None or self.runtime.engine is None:
            raise KeyError("product lock not found")
        detectors = ColabSafetyDetectors(self.runtime)
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

    @staticmethod
    def generation_payload(generation_id: str, outcome: Any) -> dict[str, Any]:
        return {
            "id": generation_id,
            "status": "ready" if all(item.qa.passed for item in outcome.variants) else "needs_review",
            "scene_plan": {
                "category": outcome.scene_plan.category,
                "prompt": outcome.scene_plan.prompt,
                "negative_prompt": outcome.scene_plan.negative_prompt,
                "plan_sha256": outcome.scene_plan.plan_sha256,
            },
            "variants": [item.manifest.to_dict() for item in outcome.variants],
        }


def build_service(*, hf_token: str | None = None, cache_dir: str = "/content/campaign-studio-models") -> ColabV2Service:
    device = _device()
    if device != "cuda":
        raise RuntimeUnavailable("a CUDA Colab runtime is required for Campaign Studio V2")
    runtime = ColabRuntime(ModelRegistry(cache_dir=cache_dir))
    runtime.start(_real_factories(device), token=hf_token)
    inpaint_model = runtime.registry.resolved.get("inpaint")
    if inpaint_model is None:
        raise RuntimeUnavailable("the V2 inpainting snapshot was not resolved")
    provider = ColabDiffusersProvider(
        None,
        inpaint_model.repo_id,
        runtime=runtime,
        adapter_factory=lambda: _load_inpainting(inpaint_model, device),
    )
    return ColabV2Service(runtime=runtime, provider=provider)


def create_app(service: ColabV2Service) -> Any:
    """Create the authenticated V2-only FastAPI application."""
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.responses import Response

    globals().update({"Request": Request, "Response": Response, "UploadFile": UploadFile})
    app = FastAPI(title="Campaign Studio V2 Colab", version="2.0.0")

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        expected = os.getenv("COLAB_AI_TOKEN", "").strip()
        if expected and request.url.path.startswith("/v2/"):
            supplied = request.headers.get("authorization", "")
            if not secrets.compare_digest(supplied, f"Bearer {expected}"):
                return Response(
                    json.dumps({"detail": "Colab V2 bearer authentication failed"}),
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
                variants=int(payload.get("variants", 1)),
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
        if outcome is None or variant_index < 0 or variant_index >= len(outcome.variants):
            raise HTTPException(status_code=404, detail="generation variant not found")
        return Response(encode_png(outcome.variants[variant_index].image, mode="RGBA"), media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @app.post("/v2/batches")
    async def batch(images: list[UploadFile] = File(...), category: str = Form(default="cosmetics"), creative_direction: str | None = Form(default=None)) -> dict[str, Any]:
        items = [(image.filename or f"image-{index + 1}", await image.read()) for index, image in enumerate(images)]
        results = service.batch(items, category=category, creative_direction=creative_direction)
        return {"status": "completed", "total": len(results), "items": results}

    return app


_SERVER: Any | None = None


def run_public_v2_server(service: ColabV2Service, port: int = 8000) -> str:
    """Expose the V2 app through a temporary authenticated ngrok tunnel."""
    if service.runtime.status != "ready":
        raise RuntimeUnavailable("start the V2 runtime before starting the API")
    try:
        import uvicorn
        from pyngrok import ngrok
    except ImportError as exc:
        raise RuntimeUnavailable("uvicorn and pyngrok are required") from exc
    tunnel_token = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if not tunnel_token:
        raise RuntimeUnavailable("NGROK_AUTHTOKEN is required")
    service_token = os.getenv("COLAB_AI_TOKEN", "").strip() or secrets.token_urlsafe(48)
    os.environ["COLAB_AI_TOKEN"] = service_token
    app = create_app(service)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="campaign-studio-v2-api", daemon=True)
    thread.start()
    global _SERVER
    _SERVER = server
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeUnavailable(f"V2 API did not start on port {port}")
    ngrok.set_auth_token(tunnel_token)
    for tunnel in ngrok.get_tunnels():
        try:
            ngrok.disconnect(tunnel.public_url)
        except Exception:
            pass
    public_url = ngrok.connect(str(port), "http").public_url
    print(f"Campaign Studio V2 API: {public_url}")
    print(f"COLAB_AI_TOKEN: {service_token}")
    return public_url


__all__ = [
    "ColabDiffusersProvider",
    "ColabSafetyDetectors",
    "ColabV2Service",
    "FakeInpaintingAdapter",
    "TransformersGroundingDINO",
    "TransformersSAM2Predictor",
    "build_service",
    "create_app",
    "run_public_v2_server",
]
