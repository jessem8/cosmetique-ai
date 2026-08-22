from __future__ import annotations

import io

from PIL import Image, ImageDraw

from ai.colab_v2_runtime import ColabProductLockEngine
from ai_service.contracts import NormalizedBox, Proposal
from ai_service.segmentation import GroundingDINOProposalAdapter, SAM2Segmenter


def _source() -> bytes:
    image = Image.new("RGB", (80, 60), "white")
    ImageDraw.Draw(image).rectangle((22, 8, 56, 51), fill="navy")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _Detector:
    def __init__(self, hand: bool = False) -> None:
        self.hand = hand

    def propose(self, image, prompt: str):
        box = NormalizedBox(0.25, 0.12, 0.45, 0.74)
        if prompt == "cosmetic product packaging":
            return [Proposal(box, 0.95, "product")]
        if prompt == "human hand" and self.hand:
            return [Proposal(NormalizedBox(0.02, 0.02, 0.2, 0.3), 0.9, "hand")]
        return []


class _SAM:
    def predict(self, **kwargs):
        mask = Image.new("L", (80, 60), 0)
        ImageDraw.Draw(mask).rectangle((22, 8, 56, 51), fill=255)
        return {"mask": mask, "score": 0.96}


def _engine(hand: bool = False) -> ColabProductLockEngine:
    return ColabProductLockEngine(
        proposal=GroundingDINOProposalAdapter(detector=_Detector(hand)),
        sam2=SAM2Segmenter(predictor=_SAM()),
    )


def test_colab_engine_creates_accepted_lock_from_consensus_and_sam2() -> None:
    lock = _engine().create_lock(_source())

    assert lock.accepted is True
    assert lock.revision == 0
    assert lock.algorithm == "canonical-binary-mask-v1"
    assert lock.metrics.foreground_pixels > 0


def test_colab_engine_requires_correction_when_hand_is_detected() -> None:
    lock = _engine(hand=True).create_lock(_source())

    assert lock.accepted is False
    assert "hand_detected" in lock.reasons
    assert "correction_required" in lock.reasons


def test_consensus_duplicate_boxes_do_not_count_as_multiple_products() -> None:
    box = NormalizedBox(0.25, 0.12, 0.45, 0.74)
    same_product = [Proposal(box, 0.95, "grounding"), Proposal(box, 0.91, "dfine")]
    second_product = same_product + [Proposal(NormalizedBox(0.02, 0.1, 0.18, 0.5), 0.8, "second")]

    assert ColabProductLockEngine._distinct_product_count(same_product) == 1
    assert ColabProductLockEngine._distinct_product_count(second_product) == 2
