"""Canonical GPU runtime for Campaign Studio V2.

This module is intentionally import-safe on development machines.  Heavy
libraries are loaded only by ``ColabRuntime.start`` after their model snapshots
have been resolved.  The notebook and FastAPI service share this exact engine;
there is no parallel notebook implementation.
"""
from __future__ import annotations

import gc
import hashlib
import importlib
import io
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from PIL import Image

from ai_service.contracts import NormalizedBox, NormalizedPoint, PointLabel, ProductLockStatus, RefinementContract
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
    ModelSpec("grounding_dino", "IDEA-Research/grounding-dino-base", os.getenv("COLAB_GROUNDING_DINO_REVISION", "main")),
    ModelSpec("sam2", "facebook/sam2-hiera-large", os.getenv("COLAB_SAM2_REVISION", "main")),
    ModelSpec("inpaint", os.getenv("COLAB_INPAINT_REPO", "diffusers/stable-diffusion-xl-1.0-inpainting-0.1"), os.getenv("COLAB_INPAINT_REVISION", "main")),
)
if os.getenv("COLAB_ENABLE_DFINE", "0").casefold() in {"1", "true", "yes"}:
    DEFAULT_MODELS = DEFAULT_MODELS + (
        ModelSpec("dfine", os.getenv("COLAB_DFINE_REPO", "ustc-community/dfine"), os.getenv("COLAB_DFINE_REVISION", "main"), required=False),
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
    contaminant_proposals: tuple[Proposal, ...]
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
        try:
            return tuple(primary) + tuple(self.dfine.propose(image, prompt=prompt))
        except Exception:
            # D-FINE is an optional corroborator. Its failure must never be
            # converted into a clean lock.
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

    def inspect(self, image: Image.Image) -> DetectorConsensus:
        products = self._proposals(image, "cosmetic product packaging")
        selected = select_proposal(products, minimum_score=0.55, ambiguity_margin=0.08)
        reasons: list[str] = []
        if selected is None and products:
            selected = max(products, key=lambda item: item.score)
            reasons.append("ambiguous_product_proposal_maximum_effort")
        elif selected is None:
            reasons.append("missing_product_proposal")

        contaminants: list[Proposal] = []
        contamination: list[str] = []
        hands = tuple(item for item in self._proposals(image, "human hand") if item.score >= 0.45)
        people = tuple(item for item in self._proposals(image, "person") if item.score >= 0.45)
        if hands:
            contaminants.extend(hands)
            contamination.append("hand_detected")
        if people:
            contaminants.extend(people)
            contamination.append("person_detected")

        if selected is not None:
            extras = tuple(
                item
                for item in products
                if item.score >= 0.55
                and item is not selected
                and self._iou(item.box, selected.box) < 0.65
            )
            if extras:
                contaminants.extend(extras)
                contamination.append("multiple_product_detected")

        reasons.extend(contamination)
        return DetectorConsensus(
            tuple(products),
            tuple(contaminants),
            tuple(sorted(set(contamination))),
            selected,
            tuple(sorted(set(reasons))),
        )

    @staticmethod
    def _point(box: NormalizedBox, label: PointLabel) -> NormalizedPoint:
        return NormalizedPoint(
            x=min(1.0, max(0.0, box.x + box.width / 2)),
            y=min(1.0, max(0.0, box.y + box.height / 2)),
            label=label,
        )

    @classmethod
    def _guided_refinement(
        cls,
        base: RefinementContract,
        selected: NormalizedBox,
        contaminants: Iterable[Proposal],
    ) -> RefinementContract:
        positives = list(base.positive_points)
        negatives = list(base.negative_points)
        if not positives:
            positives.append(cls._point(selected, PointLabel.POSITIVE))
        seen = {(round(point.x, 6), round(point.y, 6)) for point in negatives}
        for proposal in contaminants:
            point = cls._point(proposal.box, PointLabel.NEGATIVE)
            key = (round(point.x, 6), round(point.y, 6))
            if key not in seen:
                negatives.append(point)
                seen.add(key)
        return RefinementContract(tuple(positives), tuple(negatives[:63]), base.box or selected)

    @staticmethod
    def _source_mime(source: bytes) -> str:
        with Image.open(io.BytesIO(source)) as image:
            return Image.MIME.get(image.format or "", "image/png")

    def create_lock(
        self,
        source: bytes,
        *,
        refinement: RefinementContract | Mapping[str, Any] | None = None,
        target_box: NormalizedBox | Mapping[str, Any] | None = None,
    ) -> ProductLock:
        image = Image.open(io.BytesIO(source)).convert("RGBA")
        source_mime = self._source_mime(source)
        consensus = self.inspect(image)
        chosen = target_box or consensus.selected
        if isinstance(chosen, Proposal):
            chosen = chosen.box
        contract = refinement if isinstance(refinement, RefinementContract) else RefinementContract.from_mapping(refinement or {})
        if chosen is None:
            lock = self.processor.create(source, mask=None, refinement=contract, target_box=None, source_mime=source_mime)
            return replace(
                lock,
                status=ProductLockStatus.NEEDS_REVIEW,
                reasons=tuple(sorted(set(lock.reasons + consensus.reasons + ("product_detection_failed",)))),
            )
        contract = self._guided_refinement(contract, chosen, consensus.contaminant_proposals)
        result = self.sam2.segment(image, refinement=contract, target_box=chosen)
        lock = self.processor.create(
            source,
            mask=result,
            refinement=contract,
            target_box=chosen,
            source_mime=source_mime,
        )
        return replace(lock, reasons=tuple(sorted(set(lock.reasons + consensus.reasons))))

    def refine(self, lock: ProductLock, source: bytes, refinement: RefinementContract | Mapping[str, Any]) -> ProductLock:
        contract = refinement if isinstance(refinement, RefinementContract) else RefinementContract.from_mapping(refinement)
        image = Image.open(io.BytesIO(source)).convert("RGBA")
        consensus = self.inspect(image)
        chosen = contract.box or (consensus.selected.box if consensus.selected else None)
        if chosen is None:
            revised = self.processor.refine(lock, contract)
            return replace(
                revised,
                status=ProductLockStatus.NEEDS_REVIEW,
                reasons=tuple(sorted(set(revised.reasons + consensus.reasons + ("product_detection_failed",)))),
            )
        guided = self._guided_refinement(contract, chosen, consensus.contaminant_proposals)
        result = self.sam2.segment(image, refinement=guided, target_box=chosen)
        revised = self.processor.create(
            source,
            mask=result,
            refinement=guided,
            target_box=chosen,
            lock_id=lock.lock_id,
            revision=lock.revision + 1,
            source_mime=lock.source_mime,
            source_sha256=lock.source_sha256,
        )
        return replace(revised, reasons=tuple(sorted(set(revised.reasons + consensus.reasons))))


@dataclass
class ColabRuntime:
    registry: ModelRegistry
    runtime_id: str = field(default_factory=lambda: hashlib.sha256(os.urandom(32)).hexdigest()[:20])
    engine: ColabProductLockEngine | None = None
    factories: Mapping[str, Callable[[ResolvedModel], Any]] | None = None
    models: Mapping[str, ResolvedModel] | None = None
    status: str = "starting"
    reason: str | None = None

    def _load_detectors(self) -> None:
        if self.factories is None or self.models is None:
            raise RuntimeUnavailable("detector factories are not configured")
        proposal = GroundingDINOProposalAdapter(
            detector=self.factories["grounding_dino"](self.models["grounding_dino"])
        )
        sam2 = SAM2Segmenter(
            predictor=self.factories["sam2"](self.models["sam2"])
        )
        dfine = (
            DFineProposalAdapter(detector=self.factories["dfine"](self.models["dfine"]))
            if "dfine" in self.models and "dfine" in self.factories
            else None
        )
        self.engine = ColabProductLockEngine(proposal=proposal, sam2=sam2, dfine=dfine)

    def start(self, factories: Mapping[str, Callable[[ResolvedModel], Any]], *, token: str | None = None) -> None:
        try:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeUnavailable("CUDA GPU is unavailable")
            self.factories = factories
            self.models = self.registry.resolve(token)
            self._load_detectors()
            self.status, self.reason = "ready", None
        except Exception as exc:
            self.engine, self.status, self.reason = None, "unavailable", f"{type(exc).__name__}: {exc}"[:500]
            raise

    def suspend_detectors(self) -> None:
        """Release detector objects before loading the large SDXL pipeline."""
        self.engine = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def resume_detectors(self) -> None:
        if self.engine is None:
            self._load_detectors()


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
