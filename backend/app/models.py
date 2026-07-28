"""
SQLAlchemy ORM models (SQLAlchemy 2.0 compatible — Mapped[] annotations).

Tables:
  users          → owns many products
  products       → owns many generations
  generations    → owns many assets
  assets         → 1 per social format (instagram, facebook, linkedin)
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey,
    Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


# ── Enums ──────────────────────────────────────────────────────────────────


class GenerationStatus(str, enum.Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    DONE       = "done"
    ERROR      = "error"


class AssetFormat(str, enum.Enum):
    INSTAGRAM = "instagram"
    FACEBOOK  = "facebook"
    LINKEDIN  = "linkedin"


# ── Models ─────────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"
    __allow_unmapped__ = True     # Keep compatibility with old-style annotations

    id:              uuid.UUID = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    email:           str       = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: str       = mapped_column(String, nullable=False)
    is_active:       bool      = mapped_column(Boolean, default=True, nullable=False)
    created_at:      datetime  = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relations
    products: Mapped[List["Product"]] = relationship(
        "Product", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


class Product(Base):
    __tablename__ = "products"
    __allow_unmapped__ = True

    id:                uuid.UUID        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id:           uuid.UUID        = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name:              str              = mapped_column(String(200), nullable=False)
    brand:             Optional[str]    = mapped_column(String(100), nullable=True)
    category:          Optional[str]    = mapped_column(String(50), nullable=True)
    original_image_url: str             = mapped_column(String, nullable=False)
    cutout_image_url:  Optional[str]    = mapped_column(String, nullable=True)
    created_at:        datetime         = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relations
    user:        Mapped["User"]             = relationship("User", back_populates="products")
    generations: Mapped[List["Generation"]] = relationship(
        "Generation", back_populates="product", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Product id={self.id} name={self.name}>"


class Generation(Base):
    __tablename__ = "generations"
    __allow_unmapped__ = True

    id:              uuid.UUID           = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    product_id:      uuid.UUID           = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    status:          GenerationStatus    = mapped_column(SAEnum(GenerationStatus, name="generationstatus", values_callable=lambda obj: [e.value for e in obj]), default=GenerationStatus.PENDING, nullable=False)
    tone:            Optional[str]       = mapped_column(String(50), nullable=True)   # luxe | naturel | dynamique | ...
    template:        Optional[str]       = mapped_column(String(50), nullable=True)   # classic | minimal | bold
    marketing_text:  Optional[dict]      = mapped_column(JSONB, nullable=True)        # {titre, sous_titre, bullets, cta}
    error_message:   Optional[str]       = mapped_column(Text, nullable=True)
    created_at:      datetime            = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at:      datetime            = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    product: Mapped["Product"]       = relationship("Product", back_populates="generations")
    assets:  Mapped[List["Asset"]]   = relationship(
        "Asset", back_populates="generation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Generation id={self.id} status={self.status}>"


class Asset(Base):
    __tablename__ = "assets"
    __allow_unmapped__ = True

    id:            uuid.UUID   = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    generation_id: uuid.UUID   = mapped_column(UUID(as_uuid=True), ForeignKey("generations.id", ondelete="CASCADE"), nullable=False, index=True)
    format:        AssetFormat = mapped_column(SAEnum(AssetFormat, name="assetformat", values_callable=lambda obj: [e.value for e in obj]), nullable=False)
    url:           str         = mapped_column(String, nullable=False)
    width:         int         = mapped_column(Integer, nullable=False)
    height:        int         = mapped_column(Integer, nullable=False)
    created_at:    datetime    = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relations
    generation: Mapped["Generation"] = relationship("Generation", back_populates="assets")

    def __repr__(self) -> str:
        return f"<Asset id={self.id} format={self.format} {self.width}x{self.height}>"
