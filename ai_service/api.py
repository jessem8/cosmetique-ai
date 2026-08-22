"""Small optional FastAPI surface for Campaign Studio V2 AI operations."""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import NormalizedBox, ProductLock, RefinementContract
from .image import ProductLockProcessor
from .orchestration import BackgroundGenerationOrchestrator, CleanSyntheticQA
from .provider import DeterministicImageProvider, Provider


class AIServiceRequestError(ValueError):
    pass


@dataclass
class AIService:
    """In-memory service useful for local V2 integration and tests.

    Persistence/authentication remain the website backend's concern.  The AI
    service itself keeps only immutable lock objects and never exposes source
    image bytes from its JSON responses.
    """

    lock_processor: ProductLockProcessor = field(default_factory=ProductLockProcessor)
    provider: Provider = field(default_factory=DeterministicImageProvider)
    locks: dict[str, ProductLock] = field(default_factory=dict)

    def create_lock(self, source: bytes, *, mask: Any = None, refinement: Mapping[str, Any] | RefinementContract | None = None, target_box: Mapping[str, Any] | NormalizedBox | None = None) -> ProductLock:
        lock = self.lock_processor.create(source, mask=mask, refinement=refinement, target_box=target_box)
        self.locks[lock.lock_id] = lock
        return lock

    def refine_lock(self, lock_id: str, refinement: Mapping[str, Any] | RefinementContract) -> ProductLock:
        if lock_id not in self.locks:
            raise AIServiceRequestError("product lock not found")
        lock = self.lock_processor.refine(self.locks[lock_id], refinement)
        self.locks[lock.lock_id] = lock
        return lock

    def generate(self, lock_id: str, *, category: str, variants: int = 1, seed: int | None = None, creative_direction: str | None = None, max_cost_usd: float | None = None, require_detectors: bool = True):
        lock = self.locks.get(lock_id)
        if lock is None:
            raise AIServiceRequestError("product lock not found")
        orchestrator = BackgroundGenerationOrchestrator(self.provider)
        return orchestrator.generate(lock, category=category, variants=variants, seed=seed, creative_direction=creative_direction, max_cost_usd=max_cost_usd, duplicate_detector=CleanSyntheticQA() if not require_detectors else None, person_hand_detector=CleanSyntheticQA() if not require_detectors else None, require_detectors=require_detectors)


def _decode_base64(value: str, *, max_bytes: int = 32 * 1024 * 1024) -> bytes:
    try:
        if value.startswith("data:") and "," in value:
            value = value.split(",", 1)[1]
        output = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AIServiceRequestError("image_b64 is invalid") from exc
    if len(output) > max_bytes:
        raise AIServiceRequestError("image exceeds service request bound")
    return output


def create_app(service: AIService | None = None) -> Any:
    """Build the optional V2 FastAPI app without importing FastAPI eagerly."""

    try:
        from fastapi import FastAPI, File, Form, HTTPException, UploadFile
        from fastapi.responses import Response
    except ImportError as exc:
        raise RuntimeError("FastAPI is required for ai_service.create_app()") from exc

    # ``from __future__ import annotations`` stores route annotations as
    # strings.  FastAPI resolves those strings from this module's globals, not
    # from this function's local scope.
    globals().update({"UploadFile": UploadFile, "Response": Response})

    service = service or AIService()
    app = FastAPI(title="Campaign Studio V2 AI", version="2.0.0")

    @app.get("/v2/health")
    def health() -> dict[str, Any]:
        return {"ready": True, "service": "campaign-studio-v2-ai", "locks": len(service.locks)}

    @app.post("/v2/product-locks")
    async def create_lock_upload(
        image: UploadFile = File(...),
        mask: UploadFile | None = File(default=None),
        target_box: str | None = Form(default=None),
        refinement: str | None = Form(default=None),
    ) -> dict[str, Any]:
        try:
            source = await image.read()
            mask_bytes = await mask.read() if mask is not None else None
            if len(source) > 32 * 1024 * 1024 or (mask_bytes is not None and len(mask_bytes) > 32 * 1024 * 1024):
                raise AIServiceRequestError("image exceeds service request bound")
            lock = service.create_lock(
                source,
                mask=mask_bytes,
                target_box=json.loads(target_box) if target_box else None,
                refinement=json.loads(refinement) if refinement else None,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return lock.to_dict()

    @app.post("/v2/product-lock")
    async def create_lock_json(payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            source = _decode_base64(str(payload.get("image_b64", "")))
            mask = _decode_base64(str(payload["mask_b64"])) if payload.get("mask_b64") else None
            lock = service.create_lock(source, mask=mask, target_box=payload.get("target_box"), refinement=payload.get("refinement"))
        except (ValueError, KeyError, AIServiceRequestError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return lock.to_dict()

    @app.get("/v2/product-locks/{lock_id}")
    def get_lock(lock_id: str) -> dict[str, Any]:
        lock = service.locks.get(lock_id)
        if lock is None:
            raise HTTPException(status_code=404, detail="product lock not found")
        return lock.to_dict()

    @app.post("/v2/product-locks/{lock_id}/refinements")
    async def refine_lock(lock_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return service.refine_lock(lock_id, payload).to_dict()
        except (ValueError, AIServiceRequestError) as exc:
            raise HTTPException(status_code=422 if not isinstance(exc, AIServiceRequestError) else 404, detail=str(exc)) from exc

    @app.get("/v2/product-locks/{lock_id}/artifacts/{artifact}")
    def lock_artifact(lock_id: str, artifact: str) -> Response:
        lock = service.locks.get(lock_id)
        if lock is None:
            raise HTTPException(status_code=404, detail="product lock not found")
        values = {"canonical.png": (lock.canonical_image_png, "image/png"), "mask.png": (lock.mask_png, "image/png"), "cutout.png": (lock.cutout_png, "image/png")}
        if artifact not in values:
            raise HTTPException(status_code=404, detail="artifact not found")
        body, media_type = values[artifact]
        return Response(body, media_type=media_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/v2/generations")
    async def generate(payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            outcome = service.generate(
                str(payload.get("lock_id", "")),
                category=str(payload.get("category", "cosmetics")),
                variants=int(payload.get("variants", 1)),
                seed=None if payload.get("seed") is None else int(payload["seed"]),
                creative_direction=payload.get("creative_direction"),
                max_cost_usd=None if payload.get("max_cost_usd") is None else float(payload["max_cost_usd"]),
                require_detectors=bool(payload.get("require_detectors", True)),
            )
        except Exception as exc:
            # Do not expose provider response bodies, keys, paths, or tracebacks
            # over this API.  The caller receives a stable bounded error.
            status = 404 if isinstance(exc, AIServiceRequestError) else 422
            raise HTTPException(status_code=status, detail="generation request could not be completed") from exc
        return {
            "scene_plan": {"category": outcome.scene_plan.category, "plan_sha256": outcome.scene_plan.plan_sha256, "remote_seed_deterministic": outcome.scene_plan.remote_seed_deterministic},
            "variants": [variant.manifest.to_dict() for variant in outcome.variants],
        }

    return app


__all__ = ["AIService", "AIServiceRequestError", "create_app"]
