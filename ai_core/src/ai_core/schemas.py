"""Small schema compatibility layer used by the model-free website backend."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class CampaignLanguage(str, Enum):
    FR = 'fr'
    EN = 'en'


class NormalizedBox(StrictModel):
    type: Literal['box'] = 'box'
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode='after')
    def stays_inside_image(self) -> 'NormalizedBox':
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError('box must stay inside the normalized image')
        return self


class CandidateBox(NormalizedBox):
    id: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0, le=1)


class ProductSnapshot(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    original_mime: Literal['image/jpeg', 'image/png', 'image/webp']
    original_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    original_width: int = Field(gt=0, le=20_000)
    original_height: int = Field(gt=0, le=20_000)

    @model_validator(mode='after')
    def enforce_hard_pixel_cap(self) -> 'ProductSnapshot':
        if self.original_width * self.original_height > 100_000_000:
            raise ValueError('source image exceeds the hard pixel cap')
        return self


class GenerationRequest(StrictModel):
    language: CampaignLanguage
    seed: int = Field(ge=0, le=4_294_967_295)
    audience: str | None = Field(default=None, max_length=300)
    benefits: list[str] = Field(default_factory=list, max_length=12)
    ingredients: list[str] = Field(default_factory=list, max_length=30)
    verified_claims: list[str] = Field(default_factory=list, max_length=20)
    cta: str | None = Field(default=None, max_length=80)
    creative_direction: str | None = Field(default=None, max_length=500)
    target_hint: NormalizedBox | None = None
    source_generation_id: str | None = Field(default=None, max_length=100)

    @field_validator('audience', 'cta', 'creative_direction', 'source_generation_id')
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator('benefits', 'ingredients', 'verified_claims')
    @classmethod
    def clean_text_lists(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or len(item) > 240:
                raise ValueError('generation text values must be bounded and non-empty')
            if item.casefold() not in seen:
                seen.add(item.casefold())
                result.append(item)
        return result


class PipelineSnapshot(StrictModel):
    product: ProductSnapshot
    generation: GenerationRequest
