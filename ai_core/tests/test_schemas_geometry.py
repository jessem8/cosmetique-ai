from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from ai_core.geometry import normalized_box_to_pixels, pixel_box_to_normalized
from ai_core.schemas import (
    CampaignLanguage,
    ErrorCode,
    GenerationRequest,
    NormalizedBox,
    PixelBox,
)


def test_generation_request_rejects_unknown_fields_and_coercion() -> None:
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(
            {"language": "fr", "seed": "42", "invented_option": True}
        )


def test_generation_request_normalizes_bounded_optional_text() -> None:
    request = GenerationRequest(
        language=CampaignLanguage.FR,
        seed=42,
        audience="  adultes à la peau sensible  ",
        benefits=["  Hydrate la peau  "],
        ingredients=[" Aloe vera "],
        verified_claims=[" Testé sous contrôle dermatologique "],
        cta="  Découvrir  ",
    )

    assert request.audience == "adultes à la peau sensible"
    assert request.benefits == ("Hydrate la peau",)
    assert request.ingredients == ("Aloe vera",)
    assert request.verified_claims == ("Testé sous contrôle dermatologique",)
    assert request.cta == "Découvrir"


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "box", "x": math.nan, "y": 0.1, "width": 0.2, "height": 0.3},
        {"type": "box", "x": 0.9, "y": 0.1, "width": 0.2, "height": 0.3},
        {"type": "box", "x": 0.1, "y": 0.8, "width": 0.2, "height": 0.3},
        {"type": "box", "x": 0.1, "y": 0.1, "width": 0.0, "height": 0.3},
    ],
)
def test_normalized_box_rejects_non_finite_or_out_of_image_rectangles(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        NormalizedBox.model_validate(payload)


def test_pixel_and_normalized_boxes_round_trip_to_cover_original_box() -> None:
    pixels = PixelBox(x_min=12, y_min=18, x_max=66, y_max=86)

    normalized = pixel_box_to_normalized(pixels, image_width=100, image_height=100)
    restored = normalized_box_to_pixels(
        normalized, image_width=100, image_height=100
    )

    assert normalized.model_dump() == {
        "type": "box",
        "x": 0.12,
        "y": 0.18,
        "width": 0.54,
        "height": 0.68,
    }
    assert restored == pixels


def test_geometry_rejects_zero_sized_images() -> None:
    box = NormalizedBox(type="box", x=0.1, y=0.1, width=0.2, height=0.2)

    with pytest.raises(ValueError, match="positive"):
        normalized_box_to_pixels(box, image_width=0, image_height=100)


def test_error_codes_match_the_locked_cross_service_contract() -> None:
    assert {code.value for code in ErrorCode} >= {
        "AI_SERVICE_UNAVAILABLE",
        "AI_RUNTIME_LOST",
        "REMOTE_AUTH_FAILED",
        "REMOTE_PROTOCOL_ERROR",
        "TARGET_NOT_FOUND",
        "TARGET_AMBIGUOUS",
        "EXTRACTION_FAILED",
        "MASK_QUALITY_FAILED",
        "CLAIM_SAFETY_FAILED",
        "INVALID_GENERATION_REQUEST",
        "IDEMPOTENCY_CONFLICT",
        "ARTIFACT_CONTRACT_FAILED",
        "ARTIFACT_CHECKSUM_MISMATCH",
        "ARTIFACT_STORAGE_FAILED",
        "GENERATION_STALE",
        "INTERNAL_ERROR",
    }
