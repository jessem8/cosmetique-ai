"""Optional segmentation and proposal adapters.

Every heavyweight import is inside a method.  Installing V2 in a model-free
website worker therefore does not pull torch, rembg, SAM2, Grounding DINO, or
D-FINE into the import graph.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from PIL import Image

from .contracts import NormalizedBox, NormalizedPoint, PointLabel, Proposal, RefinementContract, SegmentationResult
from .image import _coerce_mask


class OptionalDependencyError(RuntimeError):
    """Raised when an optional model adapter is used without its dependency."""


class SegmentationAdapter(Protocol):
    name: str

    def segment(self, image: Image.Image, *, target_box: NormalizedBox | None = None, refinement: RefinementContract | None = None) -> SegmentationResult:
        ...


class ProposalAdapter(Protocol):
    name: str

    def propose(self, image: Image.Image, *, prompt: str = "product") -> tuple[Proposal, ...]:
        ...


def _load_rembg_remove() -> Callable[..., Any]:
    try:
        from rembg import remove  # type: ignore
    except ImportError as exc:
        raise OptionalDependencyError("rembg is not installed; install the optional V2 segmentation extra") from exc
    return remove


class RembgSegmenter:
    """Current rembg baseline with lazy model loading."""

    name = "rembg"

    def __init__(self, *, remove_fn: Callable[..., Any] | None = None, model: str = "isnet-general-use", session: Any = None) -> None:
        self._remove_fn = remove_fn
        self.model = model
        self.session = session

    def segment(self, image: Image.Image, *, target_box: NormalizedBox | None = None, refinement: RefinementContract | None = None) -> SegmentationResult:
        remove_fn = self._remove_fn or _load_rembg_remove()
        kwargs: dict[str, Any] = {}
        if self.session is not None:
            kwargs["session"] = self.session
        output = remove_fn(image.convert("RGBA"), **kwargs)
        if isinstance(output, Image.Image):
            rgba = output.convert("RGBA")
        elif isinstance(output, (bytes, bytearray, memoryview)):
            from .image import decode_image

            rgba = decode_image(bytes(output))
        else:
            raise ValueError("rembg returned an unsupported result")
        return SegmentationResult(
            mask=rgba.getchannel("A"),
            confidence=None,
            model=self.model,
            provider="rembg",
            metadata={"target_box": target_box.to_dict() if target_box else None},
        )


class U2NetSegmenter:
    """Direct CPU ONNX U2Net adapter without importing rembg/pymatting.

    The Docker CPU environment can load ONNX Runtime but the newest rembg
    wrapper currently crashes while importing its optional numba/pymatting
    stack.  This adapter uses the same public U2Net weights and preprocessing
    directly, so its provenance remains ``onnxruntime/u2net`` and never claims
    SAM2 or rembg execution.
    """

    name = "u2net-onnx"
    model_url = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"
    model_md5 = "60024c5c889badc19c04ad937298a77b"

    def __init__(self, *, model_dir: str | os.PathLike[str] | None = None, model: str = "u2net") -> None:
        self.model = model
        self.model_dir = Path(model_dir or os.getenv("V2_SEGMENTATION_MODEL_DIR", "/app/.models"))
        self._session: Any = None

    def _model_path(self) -> Path:
        destination = self.model_dir / "u2net.onnx"
        if destination.is_file():
            return destination
        self.model_dir.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(prefix="u2net-", suffix=".onnx", dir=self.model_dir)
        os.close(file_descriptor)
        temporary = Path(temporary_name)
        try:
            with urllib.request.urlopen(self.model_url, timeout=180) as response, temporary.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            digest = hashlib.md5(temporary.read_bytes()).hexdigest()
            if digest != self.model_md5:
                raise OptionalDependencyError("downloaded U2Net weights failed checksum validation")
            os.replace(temporary, destination)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if isinstance(exc, OptionalDependencyError):
                raise
            raise OptionalDependencyError("U2Net weights could not be downloaded") from exc
        return destination

    def _get_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as exc:
            raise OptionalDependencyError("onnxruntime is not installed; no segmentation model was executed") from exc
        try:
            self._session = ort.InferenceSession(
                str(self._model_path()),
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            raise OptionalDependencyError("U2Net ONNX session could not be initialized") from exc
        return self._session

    def segment(self, image: Image.Image, *, target_box: NormalizedBox | None = None, refinement: RefinementContract | None = None) -> SegmentationResult:
        try:
            import numpy as np  # type: ignore
        except ImportError as exc:
            raise OptionalDependencyError("numpy is not installed; no segmentation model was executed") from exc
        session = self._get_session()
        resized = image.convert("RGB").resize((320, 320), Image.Resampling.LANCZOS)
        array = np.asarray(resized).astype("float32")
        array = array / max(float(array.max()), 1e-6)
        mean = np.asarray((0.485, 0.456, 0.406), dtype="float32")
        std = np.asarray((0.229, 0.224, 0.225), dtype="float32")
        normalized = (array - mean) / std
        normalized = normalized.transpose((2, 0, 1))[None, ...].astype("float32")
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: normalized})
        prediction = outputs[0][:, 0, :, :]
        maximum = float(prediction.max())
        minimum = float(prediction.min())
        prediction = (prediction - minimum) / max(maximum - minimum, 1e-6)
        mask = Image.fromarray((np.squeeze(prediction).clip(0, 1) * 255).astype("uint8"), mode="L")
        mask = mask.resize(image.size, Image.Resampling.LANCZOS)
        return SegmentationResult(
            mask=mask,
            confidence=None,
            model=self.model,
            provider="onnxruntime",
            metadata={
                "execution_provider": "CPUExecutionProvider",
                "weights": "u2net.onnx",
                "target_box": target_box.to_dict() if target_box else None,
                "refinement_applied_by": "ProductLockProcessor",
            },
        )


class SyntheticSegmenter:
    """Deterministic injected segmenter for CPU orchestration fixtures."""

    name = "synthetic-segmenter"

    def __init__(self, mask: Any, *, confidence: float = 1.0, model: str = "synthetic-mask-v1") -> None:
        self.mask = mask
        self.confidence = confidence
        self.model = model

    def segment(self, image: Image.Image, *, target_box: NormalizedBox | None = None, refinement: RefinementContract | None = None) -> SegmentationResult:
        return SegmentationResult(
            mask=self.mask,
            confidence=self.confidence,
            model=self.model,
            provider="synthetic",
            metadata={"target_box": target_box.to_dict() if target_box else None},
        )


class SAM2Segmenter:
    """SAM2 box/point segmentation adapter.

    ``predictor`` is injectable for tests and for deployments that already
    construct a SAM2 predictor.  The default loader only imports SAM2 when
    ``segment`` is called and intentionally never downloads a checkpoint.
    """

    name = "sam2"

    def __init__(self, *, predictor: Any = None, model: str = "sam2", predictor_factory: Callable[[], Any] | None = None) -> None:
        self.predictor = predictor
        self.model = model
        self.predictor_factory = predictor_factory

    def _get_predictor(self) -> Any:
        if self.predictor is not None:
            return self.predictor
        if self.predictor_factory is not None:
            self.predictor = self.predictor_factory()
            return self.predictor
        try:
            import sam2  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise OptionalDependencyError("SAM2 is not installed; provide an injected predictor or optional dependency") from exc
        raise OptionalDependencyError("SAM2 checkpoint/predictor must be injected; V2 never downloads weights")

    @staticmethod
    def _parse_output(output: Any) -> tuple[Any, float | None, Mapping[str, Any]]:
        if isinstance(output, SegmentationResult):
            return output.mask, output.confidence, dict(output.metadata)
        if isinstance(output, Mapping):
            mask = output.get("mask", output.get("masks"))
            scores = output.get("score", output.get("scores"))
            score = float(scores[0] if isinstance(scores, (list, tuple)) else scores) if scores is not None else None
            # Model adapters commonly return the raw mask in the same mapping
            # as their auxiliary metadata.  Provenance must remain JSON-safe;
            # retaining a PIL/NumPy mask here makes immutable lock manifests
            # impossible to serialize.
            metadata = {
                str(key): value
                for key, value in output.items()
                if key not in {"mask", "masks", "score", "scores"}
                and isinstance(value, (str, int, float, bool, type(None)))
            }
            return mask, score, metadata
        if isinstance(output, tuple):
            if len(output) == 0:
                raise ValueError("SAM2 predictor returned an empty tuple")
            mask = output[0]
            score = output[1] if len(output) > 1 and output[1] is not None else None
            return mask, (float(score) if score is not None else None), {}
        return output, None, {}

    def segment(self, image: Image.Image, *, target_box: NormalizedBox | None = None, refinement: RefinementContract | None = None) -> SegmentationResult:
        predictor = self._get_predictor()
        positive = tuple(refinement.positive_points) if refinement else ()
        negative = tuple(refinement.negative_points) if refinement else ()
        points = [(point.x, point.y) for point in (*positive, *negative)]
        labels = [1] * len(positive) + [0] * len(negative)
        box = target_box or (refinement.box if refinement else None)
        payload = {
            "image": image.convert("RGB"),
            "points": points,
            "point_labels": labels,
            "box": box.to_dict() if box else None,
            "normalized_coordinates": True,
        }
        if hasattr(predictor, "predict"):
            try:
                output = predictor.predict(**payload)
            except TypeError:
                # A tiny injected fake commonly accepts positional values; the
                # fallback remains deterministic and does not broaden imports.
                output = predictor.predict(payload["image"], points, labels, payload["box"])
        elif callable(predictor):
            output = predictor(payload)
        else:
            raise TypeError("SAM2 predictor must expose predict() or be callable")
        mask, confidence, metadata = self._parse_output(output)
        return SegmentationResult(mask=mask, confidence=confidence, model=self.model, provider="sam2", metadata=metadata)


def _normalise_proposals(value: Any, *, width: int, height: int, label: str = "product") -> tuple[Proposal, ...]:
    if isinstance(value, Mapping):
        value = value.get("proposals", value.get("boxes", ()))
    output: list[Proposal] = []
    for index, item in enumerate(value or ()):
        if isinstance(item, Proposal):
            output.append(item)
            continue
        if isinstance(item, Mapping):
            box_value = item.get("box", item)
            score = item.get("score", item.get("confidence", 0.0))
            item_label = str(item.get("label", label))
            proposal_id = str(item.get("id", f"{label}-{index}"))
        else:
            sequence = list(item)
            if len(sequence) < 5:
                raise ValueError("proposal tuple must contain x, y, width, height, score")
            box_value = {"type": "box", "x": sequence[0], "y": sequence[1], "width": sequence[2], "height": sequence[3]}
            score = sequence[4]
            item_label = label
            proposal_id = f"{label}-{index}"
        # Adapters may return pixel xyxy boxes; normalized box dictionaries are
        # preferred, but pixel conversion remains explicit and deterministic.
        if isinstance(box_value, Mapping):
            has_xyxy = any(key in box_value for key in ("x1", "y1", "x2", "y2"))
            numeric_values = [float(box_value.get(key, 0)) for key in ("x", "y", "width", "height")]
            if has_xyxy or max(abs(value) for value in numeric_values) > 1:
                x1 = float(box_value.get("x1", box_value.get("x", 0))) / width
                y1 = float(box_value.get("y1", box_value.get("y", 0))) / height
                x2 = float(box_value.get("x2", float(box_value.get("x", 0)) + float(box_value.get("width", 0)))) / width
                y2 = float(box_value.get("y2", float(box_value.get("y", 0)) + float(box_value.get("height", 0)))) / height
                box_value = {"type": "box", "x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}
        output.append(Proposal(box=NormalizedBox.from_mapping(box_value), score=float(score), label=item_label, proposal_id=proposal_id))
    return tuple(sorted(output, key=lambda proposal: (-proposal.score, proposal.proposal_id)))


class GroundingDINOProposalAdapter:
    """Grounding DINO text proposal adapter with injected detector support."""

    name = "grounding-dino"

    def __init__(self, *, detector: Any = None, model: str = "grounding-dino-tiny", detector_factory: Callable[[], Any] | None = None) -> None:
        self.detector = detector
        self.model = model
        self.detector_factory = detector_factory

    def _get_detector(self) -> Any:
        if self.detector is not None:
            return self.detector
        if self.detector_factory is not None:
            self.detector = self.detector_factory()
            return self.detector
        try:
            import transformers  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise OptionalDependencyError("Grounding DINO is not installed; inject a detector for CPU tests") from exc
        raise OptionalDependencyError("Grounding DINO weights must be injected; V2 never downloads weights")

    def propose(self, image: Image.Image, *, prompt: str = "product") -> tuple[Proposal, ...]:
        detector = self._get_detector()
        if hasattr(detector, "propose"):
            value = detector.propose(image, prompt=prompt)
        elif hasattr(detector, "predict"):
            value = detector.predict(image, prompt)
        elif callable(detector):
            value = detector(image, prompt)
        else:
            raise TypeError("Grounding DINO detector must be callable")
        return _normalise_proposals(value, width=image.width, height=image.height, label=prompt)


class DFineProposalAdapter:
    """D-FINE object proposal adapter with lazy optional imports."""

    name = "d-fine"

    def __init__(self, *, detector: Any = None, model: str = "d-fine", detector_factory: Callable[[], Any] | None = None) -> None:
        self.detector = detector
        self.model = model
        self.detector_factory = detector_factory

    def _get_detector(self) -> Any:
        if self.detector is not None:
            return self.detector
        if self.detector_factory is not None:
            self.detector = self.detector_factory()
            return self.detector
        try:
            import torch  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise OptionalDependencyError("D-FINE is not installed; inject a detector for CPU tests") from exc
        raise OptionalDependencyError("D-FINE weights must be injected; V2 never downloads weights")

    def propose(self, image: Image.Image, *, prompt: str = "product") -> tuple[Proposal, ...]:
        detector = self._get_detector()
        if hasattr(detector, "propose"):
            value = detector.propose(image, prompt=prompt)
        elif hasattr(detector, "predict"):
            value = detector.predict(image)
        elif callable(detector):
            value = detector(image)
        else:
            raise TypeError("D-FINE detector must be callable")
        return _normalise_proposals(value, width=image.width, height=image.height, label=prompt)


def select_proposal(proposals: Sequence[Proposal], *, minimum_score: float = 0.50, ambiguity_margin: float = 0.05) -> Proposal | None:
    """Select only a clearly best proposal; ``None`` means abstain/ask review."""

    ordered = sorted((proposal for proposal in proposals if proposal.score >= minimum_score), key=lambda item: (-item.score, item.proposal_id))
    if not ordered:
        return None
    if len(ordered) > 1 and ordered[0].score - ordered[1].score < ambiguity_margin:
        return None
    return ordered[0]


# Naming aliases keep integrations readable across the two common spellings
# while retaining one implementation and one lazy-import path.
Sam2Segmenter = SAM2Segmenter
SAM2Adapter = SAM2Segmenter
GroundingDINOAdapter = GroundingDINOProposalAdapter
DFINEProposalAdapter = DFineProposalAdapter
DfineProposalAdapter = DFineProposalAdapter
DFINEAdapter = DFineProposalAdapter
RembgAdapter = RembgSegmenter


__all__ = [
    "DFineProposalAdapter",
    "DFINEProposalAdapter",
    "DFINEAdapter",
    "DfineProposalAdapter",
    "GroundingDINOAdapter",
    "GroundingDINOProposalAdapter",
    "OptionalDependencyError",
    "ProposalAdapter",
    "RembgSegmenter",
    "RembgAdapter",
    "U2NetSegmenter",
    "SyntheticSegmenter",
    "SAM2Adapter",
    "SAM2Segmenter",
    "Sam2Segmenter",
    "SegmentationAdapter",
    "select_proposal",
]
