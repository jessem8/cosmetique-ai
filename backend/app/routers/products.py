"""Private product upload and retrieval routes."""
from __future__ import annotations

import hashlib
import io
import uuid
import warnings
from dataclasses import dataclass

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import Product
from app.schemas import ProductList, ProductOut
from app.services.storage import StorageError, storage


router = APIRouter(prefix="/products", tags=["Products"])
FORMAT_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MIME_EXTENSION = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_SOURCE_DIMENSION = 20_000
UPLOAD_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class ImageMetadata:
    mime: str
    width: int
    height: int
    sha256: str


def normalize_required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Ce champ est obligatoire.",
        )
    return normalized


async def read_upload_bounded(
    upload: UploadFile,
    *,
    max_bytes: int,
    chunk_bytes: int = UPLOAD_CHUNK_BYTES,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(chunk_bytes, max_bytes - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="L'image dépasse la taille autorisée.",
            )
        chunks.append(chunk)


def inspect_image(payload: bytes, content_type: str, *, max_pixels: int) -> ImageMetadata:
    if not payload:
        raise HTTPException(status_code=400, detail="L'image est vide.")
    if content_type not in settings.allowed_mime_list:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Ce format d'image n'est pas pris en charge.",
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as source:
                detected_mime = FORMAT_MIME.get(source.format or "")
                if detected_mime != content_type:
                    raise HTTPException(
                        status_code=400, detail="Le contenu ne correspond pas au format annoncé."
                    )
                corrected = ImageOps.exif_transpose(source)
                width, height = corrected.size
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_SOURCE_DIMENSION
                    or height > MAX_SOURCE_DIMENSION
                    or width * height > max_pixels
                ):
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="Les dimensions de l'image dépassent la limite.",
                    )
                corrected.load()
    except HTTPException:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(status_code=400, detail="L'image est invalide.") from exc
    return ImageMetadata(
        mime=content_type,
        width=width,
        height=height,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def product_out(product: Product) -> ProductOut:
    return ProductOut(
        id=product.id,
        name=product.name,
        brand=product.brand,
        category=product.category,
        image_url=f"/api/v1/products/{product.id}/image",
        created_at=product.created_at,
    )


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    db: DBSession,
    current_user: CurrentUser,
    image: UploadFile = File(...),
    name: str = Form(..., min_length=1, max_length=200),
    category: str = Form(..., min_length=1, max_length=80),
    brand: str | None = Form(default=None, max_length=100),
) -> ProductOut:
    content_type = image.content_type or ""
    try:
        payload = await read_upload_bounded(
            image, max_bytes=settings.max_upload_bytes
        )
    finally:
        await image.close()
    metadata = inspect_image(
        payload, content_type, max_pixels=settings.MAX_IMAGE_PIXELS
    )
    normalized_name = normalize_required_text(name)
    normalized_category = normalize_required_text(category)
    product_id = uuid.uuid4()
    extension = MIME_EXTENSION[metadata.mime]
    storage_key = (
        f"products/{current_user.id}/{product_id}/original.{extension}"
    )
    try:
        storage.put_bytes(storage_key, payload)
    except StorageError as exc:
        raise HTTPException(
            status_code=500, detail="L'image n'a pas pu être enregistrée."
        ) from exc

    product = Product(
        id=product_id,
        user_id=current_user.id,
        name=normalized_name,
        brand=brand.strip() if brand and brand.strip() else None,
        category=normalized_category,
        original_storage_key=storage_key,
        original_mime=metadata.mime,
        original_sha256=metadata.sha256,
        original_width=metadata.width,
        original_height=metadata.height,
    )
    db.add(product)
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        try:
            storage.delete(storage_key)
        except StorageError:
            pass
        raise HTTPException(
            status_code=500, detail="Le produit n'a pas pu être enregistré."
        ) from exc
    db.refresh(product)
    return product_out(product)


@router.get("", response_model=ProductList)
def list_products(
    db: DBSession,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
) -> ProductList:
    predicate = Product.user_id == current_user.id
    total = db.scalar(select(func.count()).select_from(Product).where(predicate)) or 0
    items = db.scalars(
        select(Product)
        .where(predicate)
        .order_by(Product.created_at.desc(), Product.id.desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return ProductList(items=[product_out(item) for item in items], total=total)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: uuid.UUID, db: DBSession, current_user: CurrentUser
) -> ProductOut:
    product = db.scalar(
        select(Product).where(
            Product.id == product_id, Product.user_id == current_user.id
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    return product_out(product)


@router.get("/{product_id}/image", response_class=FileResponse)
def get_product_image(
    product_id: uuid.UUID, db: DBSession, current_user: CurrentUser
) -> FileResponse:
    product = db.scalar(
        select(Product).where(
            Product.id == product_id, Product.user_id == current_user.id
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    try:
        path = storage.path_for(product.original_storage_key)
        if not path.is_file() or path.is_symlink():
            raise StorageError("missing")
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="Image introuvable.") from exc
    response = FileResponse(
        path,
        media_type=product.original_mime,
        filename=f"produit-{product.id}.{MIME_EXTENSION[product.original_mime]}",
        content_disposition_type="inline",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
