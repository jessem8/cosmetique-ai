"""
Products router: upload image + metadata → product creation, list, and detail.

Security:
  - JWT required on all routes
  - File validation: MIME type (magic bytes), max size
  - User isolation: users can only access their own products
"""

import io
import json
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select

from app.core.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import Product
from app.schemas import ProductCreate, ProductList, ProductOut
from app.services.storage import upload_image

router = APIRouter(prefix="/products", tags=["Products"])

# ── Allowed MIME magic bytes ───────────────────────────────────────────────
_MAGIC_BYTES: dict[str, bytes] = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG",
    "image/webp": b"RIFF",
}


def _validate_image(file_bytes: bytes, content_type: str) -> None:
    """Validate image by magic bytes (not just Content-Type header)."""
    if content_type not in settings.allowed_mime_list:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Type MIME non supporté: {content_type}. Acceptés: {settings.ALLOWED_MIME_TYPES}",
        )
    magic = _MAGIC_BYTES.get(content_type, b"")
    if magic and not file_bytes.startswith(magic):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier corrompu ou extension falsifiée.",
        )
    if len(file_bytes) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Fichier trop lourd (max {settings.MAX_UPLOAD_SIZE_MB} MB).",
        )


@router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Uploader un produit (image + métadonnées)",
)
async def create_product(
    db: DBSession,
    current_user: CurrentUser,
    image: UploadFile = File(..., description="Image produit (JPEG, PNG, WebP)"),
    name: str = Form(..., min_length=1, max_length=200),
    brand: Optional[str] = Form(default=None, max_length=100),
    category: Optional[str] = Form(default=None, max_length=50),
) -> Product:
    """
    Upload product image and create a DB record.

    - Validates file type via magic bytes.
    - Uploads to Cloudinary (or local fallback).
    - Returns the created product.
    """
    file_bytes = await image.read()
    _validate_image(file_bytes, image.content_type or "")

    image_url = upload_image(
        file_bytes,
        folder="cosmetique_ai/originals",
        public_id=f"original_{uuid.uuid4()}",
    )

    product = Product(
        user_id=current_user.id,
        name=name.strip(),
        brand=brand.strip() if brand else None,
        category=category.strip().lower() if category else None,
        original_image_url=image_url,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get(
    "",
    response_model=ProductList,
    summary="Liste des produits de l'utilisateur",
)
def list_products(
    db: DBSession,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    total = db.scalar(
        select(func.count()).select_from(Product).where(Product.user_id == current_user.id)
    )
    items = db.scalars(
        select(Product)
        .where(Product.user_id == current_user.id)
        .order_by(Product.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return {"items": items, "total": total}


@router.get(
    "/{product_id}",
    response_model=ProductOut,
    summary="Détail d'un produit",
)
def get_product(
    product_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> Product:
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    if product.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    return product
