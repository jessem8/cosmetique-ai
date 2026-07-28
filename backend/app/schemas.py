"""
Pydantic schemas for request / response validation.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import AssetFormat, GenerationStatus


# ═══════════════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════════════


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        description="Au moins 8 caractères, une majuscule, un chiffre.",
    )

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins une majuscule.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins un chiffre.")
        return v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_active: bool
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[uuid.UUID] = None


# ═══════════════════════════════════════════════════════════════════
#  PRODUCTS
# ═══════════════════════════════════════════════════════════════════


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: Optional[str] = Field(default=None, max_length=100)
    category: Optional[str] = Field(default=None, max_length=50)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    brand: Optional[str]
    category: Optional[str]
    original_image_url: str
    cutout_image_url: Optional[str]
    created_at: datetime


class ProductList(BaseModel):
    items: list[ProductOut]
    total: int


# ═══════════════════════════════════════════════════════════════════
#  GENERATIONS
# ═══════════════════════════════════════════════════════════════════


class GenerationCreate(BaseModel):
    tone: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Ton marketing: luxe, naturel, dynamique, frais, scientifique",
    )
    template: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Template poster: classic, minimal, bold",
    )


class MarketingText(BaseModel):
    titre: str
    sous_titre: str
    bullets: list[str]
    cta: str


class GenerationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    product: Optional[ProductOut] = None
    status: GenerationStatus
    tone: Optional[str]
    template: Optional[str]
    marketing_text: Optional[dict[str, Any]]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    assets: list["AssetOut"] = []


class GenerationStatus_(BaseModel):
    """Lightweight status-only response for polling."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: GenerationStatus
    error_message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
#  ASSETS
# ═══════════════════════════════════════════════════════════════════


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    generation_id: uuid.UUID
    format: AssetFormat
    url: str
    width: int
    height: int
    created_at: datetime


# ─── Rebuild forward refs ──────────────────────────────────────────
GenerationOut.model_rebuild()
