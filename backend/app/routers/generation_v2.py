"""Authenticated Campaign Studio V2 generation API."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ai_core.schemas import GenerationRequestV2
from app.dependencies import CurrentUser, DBSession
from app.models import (
    Generation,
    GenerationLifecycleStatus,
    Product,
    ProductLockRevision,
)
from app.schemas import (
    ErrorOut,
    GenerationV2History,
    GenerationV2Out,
    ProviderProfileOut,
)
from app.services.generation_jobs import (
    decode_cursor,
    encode_cursor,
    safe_error_message,
)
from app.services.generation_v2 import (
    create_v2_generation,
    owned_lock,
    transition_v2,
)
from app.services.provider_registry import provider_profiles
from app.services.storage import StorageError, storage


router = APIRouter(prefix="/api/v2", tags=["Campaign Studio V2"])
studio_router = APIRouter(prefix="/api/v1/studio/v2", tags=["Campaign Studio V2"])


def _validate_idempotency_key(value: str) -> str:
    if not 16 <= len(value) <= 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key invalide.")
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise HTTPException(status_code=400, detail="Idempotency-Key invalide.")
    return value


def _owned_generation_query(generation_id: uuid.UUID, user_id: uuid.UUID):
    return (
        select(Generation)
        .join(Generation.product)
        .where(
            Generation.id == generation_id,
            Product.user_id == user_id,
            Generation.lifecycle_status.is_not(None),
        )
        .options(
            selectinload(Generation.product),
            selectinload(Generation.v2_variants),
            selectinload(Generation.v2_attempts),
            selectinload(Generation.cost_ledger),
        )
    )


def _owned_generation(
    db: DBSession, generation_id: uuid.UUID, user_id: uuid.UUID
) -> Generation:
    generation = db.scalar(_owned_generation_query(generation_id, user_id))
    if generation is None:
        raise HTTPException(status_code=404, detail="Génération introuvable.")
    return generation


def generation_v2_out(generation: Generation) -> GenerationV2Out:
    try:
        request = GenerationRequestV2.model_validate(generation.request_v2 or {})
    except Exception as exc:
        # Rows are only written after validation.  Keep a fail-closed response
        # if an operator inspects a manually corrupted row.
        raise HTTPException(status_code=500, detail="Contrat de génération invalide.") from exc
    code = getattr(generation, "last_provider_error_code", None)
    error = ErrorOut(code=code, message=safe_error_message(code)) if code else None
    variants = []
    for variant in sorted(getattr(generation, "v2_variants", []) or [], key=lambda item: item.variant_index):
        qa_manifest = variant.qa_manifest or {}
        artifact_manifest = variant.artifact_manifest or {}
        qa_passed = bool(qa_manifest.get("passed"))
        variants.append(
            {
                "id": str(variant.id),
                "variant_index": variant.variant_index,
                "status": variant.status,
                "image_url": (
                    f"/api/v1/studio/v2/generations/{generation.id}/variants/{variant.id}/image"
                    if variant.storage_key
                    else None
                ),
                "label": artifact_manifest.get("label") or f"Variant {variant.variant_index + 1}",
                "platform": artifact_manifest.get("platform") or "campaign",
                "manifest": artifact_manifest,
                "qa": {
                    "status": "pass" if qa_passed else "review",
                    "passed": qa_passed,
                    "score": qa_manifest.get("score"),
                    "failures": qa_manifest.get("findings", []),
                    **qa_manifest,
                },
                "provenance": artifact_manifest.get("provenance") or {
                    "provider": (variant.provider_usage or {}).get("provider"),
                    "model": (variant.provider_usage or {}).get("model"),
                    "request_id": (variant.provider_usage or {}).get("request_id"),
                    "cost": (variant.provider_usage or {}).get("cost_usd"),
                    "source_sha256": (variant.provider_usage or {}).get("source_sha256"),
                    "output_sha256": variant.checksum,
                },
                "checksum": variant.checksum,
            }
        )
    attempts = []
    for attempt in sorted(getattr(generation, "v2_attempts", []) or [], key=lambda item: item.attempt):
        attempts.append(
            {
                "attempt": attempt.attempt,
                "provider_request_id": attempt.provider_request_id,
                "outcome": attempt.outcome,
                "error_code": attempt.error_code,
                "retryable": attempt.retryable,
                "usage": attempt.usage_snapshot,
                "started_at": attempt.started_at,
                "completed_at": attempt.completed_at,
            }
        )
    return GenerationV2Out(
        id=generation.id,
        product_id=generation.product_id,
        product_lock_revision_id=generation.product_lock_revision_id,
        contract_version=generation.v2_contract_version or request.contract_version,
        lifecycle_status=generation.lifecycle_status or GenerationLifecycleStatus.ACCEPTED.value,
        status=generation.lifecycle_status or GenerationLifecycleStatus.ACCEPTED.value,
        request_hash=generation.request_v2_hash or generation.input_snapshot_hash,
        request=request,
        provider=request.provider,
        provider_execution_plan=generation.provider_execution_plan,
        variant_count=generation.variant_count or request.variant_count,
        attempt_count=generation.attempt_count,
        provider_request_id=generation.provider_request_id,
        unknown_remote_completion=bool(generation.unknown_remote_completion),
        cancellation_requested=bool(generation.cancellation_requested),
        budget_authorized_micros=generation.budget_authorized_micros or request.budget.max_cost_micros,
        budget_reserved_micros=generation.budget_reserved_micros or 0,
        budget_charged_micros=generation.budget_charged_micros or 0,
        variants=variants,
        attempts=attempts,
        created_at=generation.created_at,
        updated_at=generation.updated_at,
        error=error,
    )


@router.get("/providers/profiles", response_model=list[ProviderProfileOut])
def list_provider_profiles(
    current_user: CurrentUser,
) -> list[ProviderProfileOut]:
    # Authentication is required even though these are non-secret metadata.
    return [
        ProviderProfileOut(
            selection=profile.selection,
            capabilities=profile.capabilities,
            id=":".join(
                value
                for value in (
                    profile.selection.provider,
                    profile.selection.profile,
                    profile.selection.model,
                )
                if value
            ),
            name=profile.selection.profile.replace("-", " ").title(),
            model=profile.selection.model,
            description=profile.capabilities.description,
            capability_flags=[
                flag
                for flag, enabled in {
                    "mask-preservation": profile.capabilities.supports_mask_preservation,
                    "scene-generation": profile.capabilities.supports_scene_generation,
                    "qa-provenance": profile.capabilities.supports_qa_provenance,
                }.items()
                if enabled
            ],
        )
        for profile in provider_profiles()
    ]


@router.post(
    "/products/{product_id}/generations",
    response_model=GenerationV2Out,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_generation_v2(
    product_id: uuid.UUID,
    body: GenerationRequestV2,
    db: DBSession,
    current_user: CurrentUser,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> GenerationV2Out:
    key = _validate_idempotency_key(idempotency_key)
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.user_id == current_user.id)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    lock = owned_lock(db, product_id, current_user.id, body.product_lock_revision_id)
    generation = create_v2_generation(
        db,
        product=product,
        owner_id=current_user.id,
        lock=lock,
        body=body,
        idempotency_key=key,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # The unique idempotency constraint is the source of truth under a
        # race.  Re-read the owned row and return it only when its hash agrees.
        concurrent = db.scalar(
            _owned_generation_query_by_key(product_id, current_user.id, key)
        )
        if concurrent is None:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
        if concurrent.request_v2_hash != generation.request_v2_hash:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
        generation = concurrent
    db.refresh(generation)
    # Relationships are loaded explicitly so the response works with both
    # PostgreSQL and test sessions that expire instances on commit.
    generation = db.scalar(_owned_generation_query(generation.id, current_user.id)) or generation
    return generation_v2_out(generation)


def _owned_generation_query_by_key(product_id: uuid.UUID, user_id: uuid.UUID, key: str):
    return (
        select(Generation)
        .join(Generation.product)
        .where(
            Generation.product_id == product_id,
            Product.user_id == user_id,
            Generation.idempotency_key == key,
            Generation.lifecycle_status.is_not(None),
        )
        .options(
            selectinload(Generation.product),
            selectinload(Generation.v2_variants),
            selectinload(Generation.v2_attempts),
            selectinload(Generation.cost_ledger),
        )
    )


@router.get("/generations/{generation_id}", response_model=GenerationV2Out)
def get_generation_v2(
    generation_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    response: Response,
) -> GenerationV2Out:
    generation = _owned_generation(db, generation_id, current_user.id)
    if generation.lifecycle_status not in {
        GenerationLifecycleStatus.READY.value,
        GenerationLifecycleStatus.FAILED.value,
        GenerationLifecycleStatus.CANCELLED.value,
    }:
        response.headers["Retry-After"] = "2"
    return generation_v2_out(generation)


@router.get("/generations", response_model=GenerationV2History)
def list_generations_v2(
    db: DBSession,
    current_user: CurrentUser,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> GenerationV2History:
    query = (
        select(Generation)
        .join(Generation.product)
        .where(Product.user_id == current_user.id, Generation.lifecycle_status.is_not(None))
        .options(
            selectinload(Generation.product),
            selectinload(Generation.v2_variants),
            selectinload(Generation.v2_attempts),
            selectinload(Generation.cost_ledger),
        )
        .order_by(Generation.created_at.desc(), Generation.id.desc())
    )
    if cursor:
        try:
            cursor_created_at, cursor_id = decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Curseur invalide.") from exc
        query = query.where(
            or_(
                Generation.created_at < cursor_created_at,
                and_(
                    Generation.created_at == cursor_created_at,
                    Generation.id < cursor_id,
                ),
            )
        )
    items = db.execute(query.limit(limit + 1)).unique().scalars().all()
    has_more = len(items) > limit
    page = items[:limit]
    next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
    return GenerationV2History(items=[generation_v2_out(item) for item in page], next_cursor=next_cursor)


@router.post("/generations/{generation_id}/cancel", response_model=GenerationV2Out)
def cancel_generation_v2(
    generation_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> GenerationV2Out:
    generation = _owned_generation(db, generation_id, current_user.id)
    current = GenerationLifecycleStatus(generation.lifecycle_status)
    if current in {
        GenerationLifecycleStatus.READY,
        GenerationLifecycleStatus.FAILED,
        GenerationLifecycleStatus.CANCELLED,
    }:
        return generation_v2_out(generation)
    generation.cancellation_requested = True
    try:
        transition_v2(db, generation, GenerationLifecycleStatus.CANCELLED)
    except ValueError:
        # A provider may already be running.  Persist the cancellation request
        # and let the worker reconcile its remote receipt.
        pass
    db.commit()
    db.refresh(generation)
    generation = db.scalar(_owned_generation_query(generation.id, current_user.id)) or generation
    return generation_v2_out(generation)


def list_generation_variants_v2(
    generation_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> dict[str, list[dict]]:
    generation = _owned_generation(db, generation_id, current_user.id)
    return {"variants": generation_v2_out(generation).variants}


def get_generation_variant_image(
    generation_id: uuid.UUID,
    variant_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> FileResponse:
    generation = _owned_generation(db, generation_id, current_user.id)
    variant = next(
        (item for item in generation.v2_variants if item.id == variant_id),
        None,
    )
    if variant is None or not variant.storage_key:
        raise HTTPException(status_code=404, detail="Visuel de variante introuvable.")
    try:
        path = storage.path_for(variant.storage_key)
        if not path.is_file() or path.is_symlink():
            raise StorageError("missing")
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="Visuel de variante introuvable.") from exc
    response = FileResponse(
        path,
        media_type="image/png",
        filename=f"variant-{variant.variant_index + 1}.png",
        content_disposition_type="inline",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def export_generation_v2(
    generation_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> FileResponse:
    generation = _owned_generation(db, generation_id, current_user.id)
    if (
        generation.lifecycle_status != GenerationLifecycleStatus.READY.value
        or not generation.bundle_storage_key
    ):
        raise HTTPException(status_code=404, detail="Bundle introuvable.")
    try:
        path = storage.path_for(generation.bundle_storage_key)
        if not path.is_file() or path.is_symlink():
            raise StorageError("missing")
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="Bundle introuvable.") from exc
    response = FileResponse(
        path,
        media_type="application/zip",
        filename=f"campaign-studio-v2-{generation.id}.zip",
        content_disposition_type="attachment",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@studio_router.get("/provider-profiles", response_model=list[ProviderProfileOut])
def list_studio_provider_profiles(
    current_user: CurrentUser,
) -> list[ProviderProfileOut]:
    return list_provider_profiles(current_user)


@studio_router.post(
    "/generations",
    response_model=GenerationV2Out,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_studio_generation(
    body: GenerationRequestV2,
    db: DBSession,
    current_user: CurrentUser,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> GenerationV2Out:
    """Root Studio V2 alias; product ownership comes from the lock revision."""

    key = _validate_idempotency_key(idempotency_key)
    lock = db.scalar(
        select(ProductLockRevision).where(
            ProductLockRevision.id == body.product_lock_revision_id,
            ProductLockRevision.owner_id == current_user.id,
        )
    )
    if lock is None:
        raise HTTPException(status_code=404, detail="Révision introuvable.")
    product = db.scalar(
        select(Product).where(Product.id == lock.product_id, Product.user_id == current_user.id)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    generation = create_v2_generation(
        db,
        product=product,
        owner_id=current_user.id,
        lock=lock,
        body=body,
        idempotency_key=key,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
    db.refresh(generation)
    generation = db.scalar(_owned_generation_query(generation.id, current_user.id)) or generation
    return generation_v2_out(generation)


studio_router.add_api_route(
    "/generations/{generation_id}",
    get_generation_v2,
    methods=["GET"],
    response_model=GenerationV2Out,
)
studio_router.add_api_route(
    "/generations",
    list_generations_v2,
    methods=["GET"],
    response_model=GenerationV2History,
)
router.add_api_route(
    "/generations/{generation_id}/variants",
    list_generation_variants_v2,
    methods=["GET"],
)
router.add_api_route(
    "/generations/{generation_id}/export",
    export_generation_v2,
    methods=["GET"],
    response_class=FileResponse,
)
studio_router.add_api_route(
    "/generations/{generation_id}/variants",
    list_generation_variants_v2,
    methods=["GET"],
)
router.add_api_route(
    "/generations/{generation_id}/variants/{variant_id}/image",
    get_generation_variant_image,
    methods=["GET"],
    response_class=FileResponse,
)
studio_router.add_api_route(
    "/generations/{generation_id}/variants/{variant_id}/image",
    get_generation_variant_image,
    methods=["GET"],
    response_class=FileResponse,
)
studio_router.add_api_route(
    "/generations/{generation_id}/export",
    export_generation_v2,
    methods=["GET"],
    response_class=FileResponse,
)
studio_router.add_api_route(
    "/generations/{generation_id}/cancel",
    cancel_generation_v2,
    methods=["POST"],
    response_model=GenerationV2Out,
)
