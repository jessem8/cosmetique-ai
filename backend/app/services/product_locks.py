"""Ownership-safe immutable product-lock revision operations."""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from typing import Any

from PIL import Image

from ai_core.schemas import (
    ProductLockRevisionCreate,
    ProductLockStatus,
    ProductLockPrompts,
    NormalizedBox,
    SceneSpec,
    SourceImageBinding,
)
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, ProductLockRevision as ProductLockRevisionModel
from app.schemas import ProductLockRevisionOut
from app.services.ai_pipeline import (
    AIModelUnavailable,
    PixelSafetyDetectors,
    create_lock_object,
    persist_lock_artifacts,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def owned_product(db: Session, product_id: uuid.UUID, user_id: uuid.UUID) -> Product:
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.user_id == user_id)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    return product


def source_binding(product: Product, orientation: int = 1) -> SourceImageBinding:
    stored_orientation = int(getattr(product, "original_exif_orientation", 1) or 1)
    # The browser may omit orientation for backwards compatibility, but it may
    # not override the upload-time EXIF normalization binding.
    if orientation not in {1, stored_orientation}:
        raise HTTPException(status_code=409, detail="Orientation EXIF incohérente.")
    orientation = stored_orientation
    return SourceImageBinding(
        sha256=product.original_sha256,
        mime=product.original_mime,
        width=product.original_width,
        height=product.original_height,
        exif_orientation=orientation,
    )


def _target_payload(request: ProductLockRevisionCreate) -> dict[str, Any] | None:
    target = request.normalized_target
    return target.model_dump(mode="json") if target is not None else None


def _prompt_payload(prompts: ProductLockPrompts) -> dict[str, Any]:
    return prompts.model_dump(mode="json")


def _scene_payload(scene: SceneSpec | None) -> dict[str, Any] | None:
    return scene.model_dump(mode="json") if scene is not None else None


def _validate_source_hash(product: Product, requested: str | None) -> None:
    if requested is not None and requested.lower() != product.original_sha256.lower():
        # Deliberately use 409: the caller supplied a validly-shaped hash, but
        # it does not belong to this tenant-owned product.
        raise HTTPException(status_code=409, detail="La source du produit a changé.")


def create_revision(
    db: Session,
    *,
    product: Product,
    owner_id: uuid.UUID,
    request: ProductLockRevisionCreate,
    parent: ProductLockRevisionModel | None = None,
    run_ai: bool = False,
) -> ProductLockRevisionModel:
    if parent is not None:
        # Refinement is a new immutable snapshot, not a partial patch.  Fields
        # omitted by the browser inherit from the exact parent revision while
        # explicitly supplied target/prompt values replace them.
        payload = request.model_dump(mode="json")
        supplied = request.model_fields_set
        if "source_sha256" not in supplied and parent.source_sha256:
            payload["source_sha256"] = parent.source_sha256
        if "source_orientation" not in supplied:
            payload["source_orientation"] = parent.source_exif_orientation
        if (
            "target_box" not in supplied
            and "target_hint" not in supplied
            and parent.target_geometry is not None
        ):
            payload["target_box"] = parent.target_geometry
        if "prompts" not in supplied:
            payload["prompts"] = parent.prompts or {}
        if "scene" not in supplied and parent.scene_spec is not None:
            payload["scene"] = parent.scene_spec
        request = ProductLockRevisionCreate.model_validate(payload)
    _validate_source_hash(product, request.source_sha256)
    if parent is not None and (
        parent.product_id != product.id or parent.owner_id != owner_id
    ):
        raise HTTPException(status_code=404, detail="Révision introuvable.")

    current_revision = db.scalar(
        select(func.max(ProductLockRevisionModel.revision)).where(
            ProductLockRevisionModel.product_id == product.id
        )
    )
    next_revision = int(current_revision or 0) + 1
    binding = source_binding(product, request.source_orientation)
    revision = ProductLockRevisionModel(
        id=uuid.uuid4(),
        product_id=product.id,
        owner_id=owner_id,
        revision=next_revision,
        status=ProductLockStatus.PROCESSING.value,
        source_sha256=binding.sha256,
        source_storage_key=product.original_storage_key,
        source_mime=binding.mime,
        source_width=binding.width,
        source_height=binding.height,
        source_exif_orientation=binding.exif_orientation,
        parent_revision_id=parent.id if parent else None,
        target_geometry=_target_payload(request),
        prompts=_prompt_payload(request.prompts),
        scene_spec=_scene_payload(request.scene),
        metrics={},
        model_provenance={},
    )
    if parent is not None:
        # The parent row's source and prompt fields are never rewritten.  The
        # state marker is an operational lifecycle transition only.
        parent.status = ProductLockStatus.SUPERSEDED.value
        parent.superseded_at = utcnow()
    db.add(revision)
    if run_ai:
        try:
            lock = create_lock_object(
                product,
                request,
                lock_id=str(revision.id),
                revision=revision.revision,
            )
            persist_lock_artifacts(product, revision, lock)
            revision.status = ProductLockStatus.NEEDS_REVIEW.value
            # A generated artifact is not an acceptance decision.  The user
            # must still review and explicitly validate the immutable revision.
            # ``accepted`` is additionally forced low when the conservative
            # skin heuristic finds likely hand contamination in the preserved
            # region.
            with Image.open(io.BytesIO(lock.mask_png)) as raw_mask:
                hand_check = PixelSafetyDetectors(lock, raw_mask.convert("L")).detect_person_or_hands(
                    Image.open(io.BytesIO(lock.canonical_image_png)).convert("RGBA")
                )
            if hand_check.get("hands"):
                revision.metrics["accepted"] = 0.0
                revision.model_provenance["reasons"] = ",".join(
                    value
                    for value in (
                        revision.model_provenance.get("reasons", ""),
                        "possible_hand_contamination",
                    )
                    if value
                )
        except AIModelUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="Le runtime de segmentation réel est indisponible; aucune découpe fictive n'a été enregistrée.",
            ) from exc
    return revision


def get_owned_revision(
    db: Session, revision_id: uuid.UUID, owner_id: uuid.UUID
) -> ProductLockRevisionModel:
    revision = db.scalar(
        select(ProductLockRevisionModel).where(
            ProductLockRevisionModel.id == revision_id,
            ProductLockRevisionModel.owner_id == owner_id,
        )
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Révision introuvable.")
    return revision


def _public_scene(revision: ProductLockRevisionModel) -> SceneSpec | None:
    scene = getattr(revision, "scene_spec", None)
    if not isinstance(scene, dict):
        return None
    try:
        return SceneSpec.model_validate(scene)
    except Exception:
        return None


def revision_out(revision: ProductLockRevisionModel) -> ProductLockRevisionOut:
    target_box = None
    if isinstance(revision.target_geometry, dict):
        try:
            target_box = NormalizedBox.model_validate(revision.target_geometry)
        except Exception:
            target_box = None
    confidence = (revision.metrics or {}).get("score")
    candidates = []
    if target_box is not None:
        candidates.append(
            {
                "id": str(revision.id),
                "label": "Segmentation candidate",
                "confidence": float(confidence) if isinstance(confidence, (int, float)) else None,
                "box": target_box.model_dump(mode="json"),
                "status": revision.status,
            }
        )
    return ProductLockRevisionOut(
        id=revision.id,
        product_id=revision.product_id,
        revision=revision.revision,
        status=ProductLockStatus(revision.status),
        source=SourceImageBinding(
            sha256=revision.source_sha256,
            mime=revision.source_mime,
            width=revision.source_width,
            height=revision.source_height,
            exif_orientation=revision.source_exif_orientation,
        ),
        parent_revision_id=revision.parent_revision_id,
        prompts=ProductLockPrompts.model_validate(revision.prompts or {}),
        scene=_public_scene(revision),
        target_box=target_box,
        source_image_url=f"/api/v1/products/{revision.product_id}/image",
        mask_url=(
            f"/api/v1/studio/v2/product-locks/{revision.id}/artifacts/mask.png"
            if revision.mask_storage_key
            else None
        ),
        cutout_url=(
            f"/api/v1/studio/v2/product-locks/{revision.id}/artifacts/cutout.png"
            if revision.cutout_storage_key
            else None
        ),
        candidates=candidates,
        metrics={
            str(key): float(value)
            for key, value in (revision.metrics or {}).items()
            if isinstance(value, (int, float))
        },
        model_provenance={
            str(key): str(value)
            for key, value in (revision.model_provenance or {}).items()
        },
        created_at=revision.created_at,
    )


def transition_revision(
    db: Session,
    revision: ProductLockRevisionModel,
    status: ProductLockStatus,
    *,
    reason: str | None = None,
) -> ProductLockRevisionModel:
    current = ProductLockStatus(revision.status)
    if status is ProductLockStatus.VALIDATED and (revision.model_provenance or {}).get("pipeline") == "ai_service":
        if not revision.mask_storage_key or not revision.cutout_storage_key:
            raise HTTPException(status_code=409, detail="La découpe réelle du produit est indisponible.")
        if revision.revision < 2:
            raise HTTPException(
                status_code=409,
                detail="La première segmentation est seulement un candidat. Soumettez au moins une revue de boîte/points avant validation.",
            )
        if float((revision.metrics or {}).get("accepted", 0.0)) < 1.0:
            raise HTTPException(
                status_code=409,
                detail="La découpe reste ambiguë ou contaminée. Ajoutez des corrections négatives et soumettez une nouvelle révision.",
            )
    allowed = {
        ProductLockStatus.PROCESSING: {
            ProductLockStatus.NEEDS_REVIEW,
            ProductLockStatus.VALIDATED,
            ProductLockStatus.REJECTED,
        },
        ProductLockStatus.NEEDS_REVIEW: {
            ProductLockStatus.VALIDATED,
            ProductLockStatus.REJECTED,
        },
    }
    if status not in allowed.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail="Cette révision est immuable ou ne peut plus changer d'état.",
        )
    revision.status = status.value
    if status is ProductLockStatus.VALIDATED:
        revision.validated_at = utcnow()
    elif status is ProductLockStatus.REJECTED:
        revision.rejected_at = utcnow()
        revision.rejection_reason = reason
    db.add(revision)
    return revision
