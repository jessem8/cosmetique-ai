from __future__ import annotations

import io

from PIL import Image, ImageDraw

from ai.colab_v2_runtime import ColabProductLockEngine
from ai_service.contracts import NormalizedBox, Proposal
from ai_service.segmentation import GroundingDINOProposalAdapter, SAM2Segmenter


def _source(*, format_name: str = "PNG", color: str = "navy") -> bytes:
    image = Image.new("RGB", (80, 60), "white")
    ImageDraw.Draw(image).rectangle((22, 8, 56, 51), fill=color)
    output = io.BytesIO()
    image.save(output, format=format_name)
    return output.getvalue()


class _Detector:
    def __init__(self, *, hand: bool = False, person: bool = False, extra_product: bool = False) -> None:
        self.hand = hand
        self.person = person
        self.extra_product = extra_product

    def propose(self, image, prompt: str):
        product = Proposal(NormalizedBox(0.25, 0.12, 0.45, 0.74), 0.95, "product")
        if prompt == "cosmetic product packaging":
            proposals = [product]
            if self.extra_product:
                proposals.append(Proposal(NormalizedBox(0.03, 0.18, 0.16, 0.52), 0.82, "extra-product"))
            return proposals
        if prompt == "human hand" and self.hand:
            return [Proposal(NormalizedBox(0.02, 0.02, 0.20, 0.30), 0.90, "hand")]
        if prompt == "person" and self.person:
            return [Proposal(NormalizedBox(0.76, 0.05, 0.20, 0.90), 0.88, "person")]
        return []


class _SAM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        mask = Image.new("L", (80, 60), 0)
        ImageDraw.Draw(mask).rectangle((22, 8, 56, 51), fill=255)
        return {"mask": mask, "score": 0.96}


def _engine(*, hand: bool = False, person: bool = False, extra_product: bool = False) -> tuple[ColabProductLockEngine, _SAM]:
    sam = _SAM()
    engine = ColabProductLockEngine(
        proposal=GroundingDINOProposalAdapter(
            detector=_Detector(hand=hand, person=person, extra_product=extra_product)
        ),
        sam2=SAM2Segmenter(predictor=sam),
    )
    return engine, sam


def test_colab_engine_creates_accepted_lock_from_consensus_and_sam2() -> None:
    engine, _ = _engine()
    lock = engine.create_lock(_source())

    assert lock.accepted is True
    assert lock.revision == 0
    assert lock.algorithm == "canonical-binary-mask-v1"
    assert lock.metrics.foreground_pixels > 0


def test_hand_detection_becomes_negative_sam_prompt_and_generation_lock_continues() -> None:
    engine, sam = _engine(hand=True)
    lock = engine.create_lock(_source())

    assert lock.accepted is True
    assert "hand_detected" in lock.reasons
    assert "correction_required" not in lock.reasons
    assert sam.calls
    assert 0 in sam.calls[-1]["point_labels"]


def test_person_and_extra_product_are_exclusion_prompts_not_global_rejections() -> None:
    engine, sam = _engine(person=True, extra_product=True)
    lock = engine.create_lock(_source())

    assert lock.accepted is True
    assert "person_detected" in lock.reasons
    assert "multiple_product_detected" in lock.reasons
    assert sam.calls[-1]["point_labels"].count(0) >= 2


def test_jpeg_source_mime_is_preserved_in_lock_provenance() -> None:
    engine, _ = _engine()
    lock = engine.create_lock(_source(format_name="JPEG"))

    assert lock.source_mime == "image/jpeg"


def test_consensus_duplicate_boxes_do_not_count_as_multiple_products() -> None:
    box = NormalizedBox(0.25, 0.12, 0.45, 0.74)
    same_product = [Proposal(box, 0.95, "grounding"), Proposal(box, 0.91, "dfine")]
    second_product = same_product + [Proposal(NormalizedBox(0.02, 0.1, 0.18, 0.5), 0.8, "second")]

    assert ColabProductLockEngine._distinct_product_count(same_product) == 1
    assert ColabProductLockEngine._distinct_product_count(second_product) == 2
