from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.schemas import CampaignLanguage, GenerationCreate, ProductLockRevisionCreate, TargetBox


def valid_request(**changes):
    payload = {
        "language": "fr",
        "seed": 42,
        "audience": "adultes à la peau sensible",
        "benefits": ["Hydrate la peau"],
        "ingredients": ["Aloe vera"],
        "verified_claims": ["Testé sous contrôle dermatologique"],
        "cta": "Découvrir",
        "creative_direction": "Studio minéral, lumière douce",
    }
    payload.update(changes)
    return payload


def test_generation_request_rejects_unknown_fields():
    # Catches accidental request widening that could bypass the verified-facts ledger.
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GenerationCreate.model_validate(valid_request(unsupported_claim="miracle"))


@pytest.mark.parametrize(
    "box",
    [
        {"type": "box", "x": -0.1, "y": 0, "width": 0.5, "height": 0.5},
        {"type": "box", "x": 0.6, "y": 0, "width": 0.5, "height": 0.5},
        {"type": "box", "x": 0, "y": 0.6, "width": 0.5, "height": 0.5},
        {"type": "box", "x": 0, "y": 0, "width": 0, "height": 0.5},
        {"type": "box", "x": math.nan, "y": 0, "width": 0.5, "height": 0.5},
    ],
)
def test_target_box_must_be_finite_positive_and_inside_image(box):
    # Catches invalid normalized geometry being sent to the GPU service.
    with pytest.raises(ValidationError):
        TargetBox.model_validate(box)


def test_generation_seed_matches_the_shared_unsigned_32_bit_contract():
    assert GenerationCreate.model_validate(
        valid_request(seed=4_294_967_295)
    ).seed == 4_294_967_295
    with pytest.raises(ValidationError):
        GenerationCreate.model_validate(valid_request(seed=4_294_967_296))


def test_product_lock_refinement_matches_cpu_runtime_point_budget():
    with pytest.raises(ValidationError, match="at most 64 correction points"):
        ProductLockRevisionCreate.model_validate({
            "positive_points": [{"x": 0.1, "y": 0.1}] * 32,
            "negative_points": [{"x": 0.2, "y": 0.2}] * 33,
        })


def test_generation_request_normalizes_bounded_text_and_language():
    request = GenerationCreate.model_validate(
        valid_request(language=CampaignLanguage.EN, audience="  peau sensible  ")
    )
    assert request.language is CampaignLanguage.EN
    assert request.audience == "peau sensible"
    assert request.model_dump(mode="json")["language"] == "en"

