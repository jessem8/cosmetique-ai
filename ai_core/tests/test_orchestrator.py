from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image, ImageDraw

from ai_core.artifacts import validate_bundle
from ai_core.errors import InvalidGenerationRequestError, TargetAmbiguousError
from ai_core.model_registry import PINNED_DEPENDENCIES, pinned_model_refs
from ai_core.orchestrator import PipelineOrchestrator
from ai_core.schemas import (
    CampaignLanguage,
    CandidateBox,
    CopyPlan,
    GenerationStage,
    PlatformCopySelection,
    RemoteJobRequest,
)


def canonical_hash(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_image_bytes() -> bytes:
    image = Image.new("RGB", (100, 120), (238, 233, 228))
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 20, 69, 99), fill=(18, 52, 91))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def job_request(*, target_hint: dict | None = None) -> RemoteJobRequest:
    source = source_image_bytes()
    snapshot = {
        "product": {
            "name": "Sérum Éclat",
            "brand": "Maison Exemple",
            "category": "Sérum",
            "original_mime": "image/png",
            "original_sha256": hashlib.sha256(source).hexdigest(),
            "original_width": 100,
            "original_height": 120,
        },
        "generation": {
            "language": "fr",
            "seed": 42,
            "audience": None,
            "benefits": ["Hydrate la peau"],
            "ingredients": ["Aloe vera"],
            "verified_claims": [],
            "cta": "Découvrir",
            "creative_direction": "Studio perle, lumière douce",
            "target_hint": target_hint,
            "source_generation_id": None,
        },
    }
    return RemoteJobRequest.model_validate(
        {
            "generation_id": "generation-123",
            "request_id": "request-123",
            "input_snapshot_hash": canonical_hash(snapshot),
            "input": snapshot,
        }
    )


def safe_copy_plan() -> CopyPlan:
    selection = PlatformCopySelection(evidence_ids=("benefit-1",))
    return CopyPlan(
        language=CampaignLanguage.FR,
        instagram=selection,
        facebook=selection,
        linkedin=selection,
    )


class StepClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


class Detector:
    def __init__(
        self, events: list[str], candidates: tuple[CandidateBox, ...] | None = None
    ) -> None:
        self.events = events
        self.candidates = candidates or (
            CandidateBox(
                id="candidate-1",
                score=0.93,
                type="box",
                x=0.3,
                y=1 / 6,
                width=0.4,
                height=2 / 3,
            ),
        )

    def detect(self, image: Image.Image, prompt: str) -> tuple[CandidateBox, ...]:
        self.events.append("detector.detect")
        return self.candidates

    def unload(self) -> None:
        self.events.append("detector.unload")


class Segmenter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def segment(self, image: Image.Image, target) -> Image.Image:
        self.events.append("segmenter.segment")
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rectangle((30, 20, 69, 99), fill=255)
        return mask

    def unload(self) -> None:
        self.events.append("segmenter.unload")


class Background:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def generate(self, *, prompt: str, negative_prompt: str, seed: int) -> Image.Image:
        self.events.append("background.generate")
        return Image.new("RGB", (1024, 1024), (246, 240, 235))

    def unload(self) -> None:
        self.events.append("background.unload")


class Copy:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def generate(self, *, product, request, ledger) -> CopyPlan:
        self.events.append("copy.generate")
        return safe_copy_plan()

    def unload(self) -> None:
        self.events.append("copy.unload")


class BackgroundValidator:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def validate(self, image: Image.Image) -> bool:
        self.events.append("background_validator.validate")
        return True

    def unload(self) -> None:
        self.events.append("background_validator.unload")


def orchestrator(events: list[str], detector=None) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        detector=detector or Detector(events),
        segmenter=Segmenter(events),
        background_generator=Background(events),
        background_validator=BackgroundValidator(events),
        copy_generator=Copy(events),
        runtime_id="runtime-123",
        models=pinned_model_refs(),
        dependencies=PINNED_DEPENDENCIES,
        clock=StepClock(),
    )


def test_pipeline_runs_real_stages_in_order_and_builds_valid_bundle() -> None:
    events: list[str] = []
    progress: list[tuple[GenerationStage, tuple[GenerationStage, ...]]] = []

    output = orchestrator(events).run(
        job_request(),
        source_image_bytes(),
        on_stage_complete=lambda stage, completed: progress.append((stage, completed)),
    )
    manifest = validate_bundle(output.bundle)

    assert manifest.generation_id == "generation-123"
    assert manifest.runtime_id == "runtime-123"
    assert manifest.target_selection.model_dump() == {
        "type": "box",
        "x": 0.3,
        "y": 1 / 6,
        "width": 0.4,
        "height": 2 / 3,
    }
    assert events == [
        "detector.detect",
        "segmenter.segment",
        "detector.unload",
        "segmenter.unload",
        "background.generate",
        "background.unload",
        "background_validator.validate",
        "background_validator.unload",
        "copy.generate",
        "copy.unload",
    ]
    assert [stage for stage, _ in progress] == list(GenerationStage)
    assert progress[-1][1] == tuple(GenerationStage)


def test_packaging_progress_is_published_only_after_self_validation(
    monkeypatch,
) -> None:
    events: list[str] = []
    observed: list[str] = []
    from ai_core import orchestrator as module

    original_validate = module.validate_bundle

    def recording_validate(bundle: bytes):
        observed.append("validated")
        return original_validate(bundle)

    monkeypatch.setattr(module, "validate_bundle", recording_validate)
    orchestrator(events).run(
        job_request(),
        source_image_bytes(),
        on_stage_complete=lambda stage, completed: observed.append(stage.value),
    )

    assert observed.index("validated") < observed.index("packaging")


def test_pipeline_returns_normalized_candidates_instead_of_guessing_target() -> None:
    events: list[str] = []
    detector = Detector(
        events,
        candidates=(
            CandidateBox(
                id="candidate-1",
                score=0.92,
                type="box",
                x=0.05,
                y=0.1,
                width=0.35,
                height=0.8,
            ),
            CandidateBox(
                id="candidate-2",
                score=0.87,
                type="box",
                x=0.55,
                y=0.1,
                width=0.35,
                height=0.8,
            ),
        ),
    )

    with pytest.raises(TargetAmbiguousError) as caught:
        orchestrator(events, detector=detector).run(job_request(), source_image_bytes())

    assert [candidate.model_dump() for candidate in caught.value.candidates] == [
        {
            "type": "box",
            "x": 0.05,
            "y": 0.1,
            "width": 0.35,
            "height": 0.8,
            "id": "candidate-1",
            "score": 0.92,
        },
        {
            "type": "box",
            "x": 0.55,
            "y": 0.1,
            "width": 0.35,
            "height": 0.8,
            "id": "candidate-2",
            "score": 0.87,
        },
    ]
    assert events == ["detector.detect", "detector.unload", "segmenter.unload"]


def test_explicit_target_hint_bypasses_detection_but_still_runs_mask_qa() -> None:
    events: list[str] = []
    hint = {
        "type": "box",
        "x": 0.3,
        "y": 1 / 6,
        "width": 0.4,
        "height": 2 / 3,
    }

    output = orchestrator(events).run(
        job_request(target_hint=hint), source_image_bytes()
    )

    assert output.manifest.target_selection.model_dump() == hint
    assert "detector.detect" not in events
    assert "segmenter.segment" in events


def test_pipeline_rejects_snapshot_hash_mismatch_before_inference() -> None:
    events: list[str] = []
    request = job_request().model_copy(update={"input_snapshot_hash": "f" * 64})

    with pytest.raises(InvalidGenerationRequestError, match="snapshot hash"):
        orchestrator(events).run(request, source_image_bytes())

    assert events == []


def test_pipeline_rejects_uploaded_bytes_different_from_snapshot() -> None:
    events: list[str] = []
    altered = source_image_bytes() + b"tamper"

    with pytest.raises(InvalidGenerationRequestError, match="source image checksum"):
        orchestrator(events).run(job_request(), altered)

    assert events == []
