"""Canonical GPU runtime for Campaign Studio V2.

This module is intentionally import-safe on development machines.  Heavy
libraries are loaded only by ``ColabRuntime.start`` after their model snapshots
have been resolved.  The notebook and FastAPI service share this exact engine;
there is no parallel notebook implementation.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from PIL import Image

from ai_service.contracts import NormalizedBox, ProductLockStatus, RefinementContract
from ai_service.image import ProductLockProcessor
from ai_service.orchestration import BackgroundGenerationOrchestrator, FailClosedQAGate, ScenePlanner
from ai_service.product_lock import ProductLock
from ai_service.segmentation import (
    DFineProposalAdapter,
    GroundingDINOProposalAdapter,
    Proposal,
    SAM2Segmenter,
    select_proposal,
)


class RuntimeUnavailable(RuntimeError):
    """Raised when Colab cannot honestly advertise a ready V2 engine."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo_id: str
    revision: str
    required: bool = True


@dataclass(frozen=True)
class ResolvedModel:
    name: str
    repo_id: str
    requested_revision: str
    resolved_revision: str
    path: str


DEFAULT_MODELS = (
    ModelSpec("grounding_dino", "IDEA-Research/grounding-dino-base", "e76a695ed7ae1032a61530cce4b4e9b65f4e368b"),
    ModelSpec("sam2", "facebook/sam2-hiera-large", "main"),
    ModelSpec("dfine", os.getenv("COLAB_DFINE_REPO", "ustc-community/dfine"), os.getenv("COLAB_DFINE_REVISION", "main"), required=False),
    ModelSpec("inpaint", os.getenv("COLAB_INPAINT_REPO", "diffusers/stable-diffusion-xl-1.0-inpainting-0.1"), os.getenv("COLAB_INPAINT_REVISION", "main")),
)


class ModelRegistry:
    """Resolve and record exact model snapshots before inference begins."""

    def __init__(self, cache_dir: str | Path = "/content/campaign-studio-models", specs: Iterable[ModelSpec] = DEFAULT_MODELS) -> None:
        self.cache_dir = Path(cache_dir)
        self.specs = tuple(specs)
        self.resolved: dict[str, ResolvedModel] = {}

    def resolve(self, token: str | None = None) -> dict[str, ResolvedModel]:
        try:
            from huggingface_hub import HfApi, snapshot_download
        except ImportError as exc:
            raise RuntimeUnavailable("huggingface_hub is required for the Colab model registry") from exc
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        api = HfApi(token=token)
        failures: list[str] = []
        for spec in self.specs:
            try:
                info = api.model_info(spec.repo_id, revision=spec.revision, token=token)
                path = snapshot_download(
                    repo_id=spec.repo_id,
                    revision=info.sha,
                    token=token,
                    local_dir=str(self.cache_dir / spec.name),
                )
                self.resolved[spec.name] = ResolvedModel(spec.name, spec.repo_id, spec.revision, info.sha, path)
            except Exception as exc:
                if spec.required:
                    failures.append(f"{spec.name}: {type(exc).__name__}")
                else:
                    self.resolved.pop(spec.name, None)
        if failures:
            raise RuntimeUnavailable("required model snapshots unavailable: " + ", ".join(failures))
        return dict(self.resolved)

    def manifest(self) -> dict[str, dict[str, str]]:
        return {
            name: {
                "repo_id": item.repo_id,
                "requested_revision": item.requested_revision,
                "resolved_revision": item.resolved_revision,
            }
            for name, item in self.resolved.items()
        }


@dataclass
class DetectorConsensus:
    product_proposals: tuple[Proposal, ...]
    contamination: tuple[str, ...]
    selected: Proposal | None
    reasons: tuple[str, ...]


class ColabProductLockEngine:
    """GPU-backed proposal, segmentation and fail-closed lock creation."""

    def __init__(
        self,
        *,
        proposal: GroundingDINOProposalAdapter,
        sam2: SAM2Segmenter,
        dfine: DFineProposalAdapter | None = None,
        processor: ProductLockProcessor | None = None,
    ) -> None:
        self.proposal = proposal
        self.sam2 = sam2
        self.dfine = dfine
        self.processor = processor or ProductLockProcessor()

    def _proposals(self, image: Image.Image, prompt: str) -> tuple[Proposal, ...]:
        primary = self.proposal.propose(image, prompt=prompt)
        if self.dfine is None:
            return primary

    @staticmethod
    def _iou(left: NormalizedBox, right: NormalizedBox) -> float:
        x0 = max(left.x, right.x)
        y0 = max(left.y, right.y)
        x1 = min(left.x + left.width, right.x + right.width)
        y1 = min(left.y + left.height, right.y + right.height)
        intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        union = left.width * left.height + right.width * right.height - intersection
        return intersection / union if union else 0.0

    @classmethod
    def _distinct_product_count(cls, proposals: Iterable[Proposal]) -> int:
        distinct: list[Proposal] = []
        for proposal in sorted(proposals, key=lambda item: item.score, reverse=True):
            if proposal.score < 0.55:
                continue
            if all(cls._iou(proposal.box, accepted.box) < 0.65 for accepted in distinct):
                distinct.append(proposal)
        return len(distinct)
        try:
            return tuple(primary) + tuple(self.dfine.propose(image, prompt=prompt))
        except Exception:
            # D-FINE is an optional corroborator.  Its absence is recorded by
            # runtime health and never silently converted into a clean lock.
            return primary

    def inspect(self, image: Image.Image) -> DetectorConsensus:
        products = self._proposals(image, "cosmetic product packaging")
        selected = select_proposal(products, minimum_score=0.55, ambiguity_margin=0.08)
        contamination: list[str] = []
        for label, prompt in (("hand_detected", "human hand"), ("person_detected", "person"), ("multiple_product_detected", "cosmetic product packaging")):
            candidates = self._proposals(image, prompt)
            if label == "multiple_product_detected":
                if self._distinct_product_count(candidates) > 1:
                    contamination.append(label)
            elif any(item.score >= 0.45 for item in candidates):
                contamination.append(label)
        reasons = list(contamination)
        if selected is None:
            reasons.append("ambiguous_or_missing_product_proposal")
        return DetectorConsensus(tuple(products), tuple(sorted(set(contamination))), selected, tuple(sorted(set(reasons))))

    def create_lock(
        self,
        source: bytes,
        *,
        refinement: RefinementContract | Mapping[str, Any] | None = None,
        target_box: NormalizedBox | Mapping[str, Any] | None = None,
    ) -> ProductLock:
        image = Image.open(io.BytesIO(source)).convert("RGBA")
        consensus = self.inspect(image)
        chosen = target_box or consensus.selected
        if isinstance(chosen, Proposal):
            chosen = chosen.box
        contract = refinement if isinstance(refinement, RefinementContract) else RefinementContract.from_mapping(refinement or {})
        if chosen is not None and contract.box is None:
            contract = RefinementContract(contract.positive_points, contract.negative_points, chosen)
        if chosen is None or consensus.contamination:
            # Create a revision with explicit reasons; the UI can correct it but
            # no scene generation is permitted until SAM2 produces acceptance.
            lock = self.processor.create(source, mask=None, refinement=contract, target_box=chosen)
            return replace(
                lock,
                status=ProductLockStatus.NEEDS_REVIEW,
                reasons=tuple(sorted(set(lock.reasons + consensus.reasons + ("correction_required",)))),
            )
        result = self.sam2.segment(image, refinement=contract, target_box=chosen)
        return self.processor.create(source, mask=result, refinement=contract, target_box=chosen)

    def refine(self, lock: ProductLock, source: bytes, refinement: RefinementContract | Mapping[str, Any]) -> ProductLock:
        contract = refinement if isinstance(refinement, RefinementContract) else RefinementContract.from_mapping(refinement)
        image = Image.open(io.BytesIO(source)).convert("RGBA")
        consensus = self.inspect(image)
        chosen = contract.box or (consensus.selected.box if consensus.selected else None)
        if chosen is None or consensus.contamination:
            revised = self.processor.refine(lock, contract)
            return replace(
                revised,
                status=ProductLockStatus.NEEDS_REVIEW,
                reasons=tuple(sorted(set(revised.reasons + consensus.reasons + ("correction_required",)))),
            )
        result = self.sam2.segment(image, refinement=contract, target_box=chosen)
        return self.processor.create(
            source,
            mask=result,
            refinement=contract,
            target_box=chosen,
            lock_id=lock.lock_id,
            revision=lock.revision + 1,
            source_mime=lock.source_mime,
            source_sha256=lock.source_sha256,
        )


@dataclass
class ColabRuntime:
    registry: ModelRegistry
    runtime_id: str = field(default_factory=lambda: hashlib.sha256(os.urandom(32)).hexdigest()[:20])
    engine: ColabProductLockEngine | None = None
    status: str = "starting"
    reason: str | None = None

    def start(self, factories: Mapping[str, Callable[[ResolvedModel], Any]], *, token: str | None = None) -> None:
        try:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeUnavailable("CUDA GPU is unavailable")
            models = self.registry.resolve(token)
            proposal = GroundingDINOProposalAdapter(detector=factories["grounding_dino"](models["grounding_dino"]))
            sam2 = SAM2Segmenter(predictor=factories["sam2"](models["sam2"]))
            dfine = DFineProposalAdapter(detector=factories["dfine"](models["dfine"])) if "dfine" in models and "dfine" in factories else None
            self.engine = ColabProductLockEngine(proposal=proposal, sam2=sam2, dfine=dfine)
            self.status, self.reason = "ready", None
        except Exception as exc:
            self.engine, self.status, self.reason = None, "unavailable", f"{type(exc).__name__}: {exc}"[:500]
            raise

    def health(self) -> dict[str, Any]:
        return {
            "primary_engine": "colab",
            "status": self.status,
            "runtime_id": self.runtime_id,
            "models": self.registry.manifest(),
            "reason": self.reason,
        }


__all__ = [
    "ColabProductLockEngine",
    "ColabRuntime",
    "DEFAULT_MODELS",
    "DetectorConsensus",
    "ModelRegistry",
    "ModelSpec",
    "ResolvedModel",
    "RuntimeUnavailable",
]
