"""
Cloudinary storage service.

Handles image upload and URL retrieval.
Falls back to a local-disk strategy when Cloudinary credentials are absent
(useful for development without a Cloudinary account).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

import cloudinary
import cloudinary.uploader

from app.core.config import settings

# ── Configure Cloudinary once ──────────────────────────────────────────────
_cloudinary_configured = False


def _ensure_configured() -> bool:
    global _cloudinary_configured
    if _cloudinary_configured:
        return True
    if not (
        settings.CLOUDINARY_CLOUD_NAME
        and settings.CLOUDINARY_API_KEY
        and settings.CLOUDINARY_API_SECRET
    ):
        return False
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )
    _cloudinary_configured = True
    return True


# ── Local fallback directory ───────────────────────────────────────────────
_LOCAL_UPLOAD_DIR = Path("uploads")
_LOCAL_UPLOAD_DIR.mkdir(exist_ok=True)


def upload_image(
    file_bytes: bytes,
    *,
    folder: str = "cosmetique_ai",
    public_id: Optional[str] = None,
    resource_type: str = "image",
) -> str:
    """
    Upload image bytes to Cloudinary (or local disk as fallback).

    Returns the public URL of the uploaded image.
    """
    if not public_id:
        public_id = str(uuid.uuid4())

    if _ensure_configured():
        result = cloudinary.uploader.upload(
            file_bytes,
            folder=folder,
            public_id=public_id,
            resource_type=resource_type,
            overwrite=True,
            format="png",
        )
        return result["secure_url"]

    # ── Local fallback ──────────────────────────────────────────────────────
    file_path = _LOCAL_UPLOAD_DIR / f"{public_id}.png"
    file_path.write_bytes(file_bytes)
    # Return a local URL (only useful in dev with static file serving)
    return f"/uploads/{public_id}.png"


def upload_pil_image(pil_image, *, folder: str = "cosmetique_ai", public_id: Optional[str] = None) -> str:
    """
    Convenience wrapper: accept a PIL.Image and upload it.
    """
    import io
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return upload_image(buf.getvalue(), folder=folder, public_id=public_id)
