"""Versioned durable-generation API. No in-process AI work occurs here."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ai_core.schemas import ProductSnapshot
from app.core.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import Asset, Generation, GenerationStatus, Product
from app.schemas import (
    AmbiguityOut,
    ArtifactOut,
    CandidateBox,
    ErrorOut,
    GenerationCreate,
    GenerationHistory,
    GenerationOut,
    ProductSummary,
)
from app.services.ai_client import AIClient, AIClientError
from app.services.artifacts import ARTIFACT_NAMES
from app.services.generation_jobs import (
    IdempotencyConflict,
    decode_cursor,
    encode_cursor,
    resolve_idempotency,
    safe_error_message,
    snapshot_hash,
)
from app.services.storage import StorageError, storage


router = APIRouter(tags=["Generations"])


def effective_generation_brief(
    body: GenerationCreate, source_brief: dict | None
) -> dict:
    submitted = body.model_dump(mode="json")
    if source_brief is None:
        return submitted
    merged = dict(source_brief)
    explicitly_submitted = set(body.model_fields_set) | {
        "language",
        "source_generation_id",
    }
    for field in explicitly_submitted:
        merged[field] = submitted[field]
    return GenerationCreate.model_validate(merged).model_dump(mode="json")


def product_summary(product: Product) -> ProductSummary:
    return ProductSummary(
        name=product.name,
        brand=product.brand,
        category=product.category,
    )


def immutable_product_snapshot(product: Product) -> dict:
    return ProductSnapshot(
        name=product.name,
        brand=product.brand,
        category=product.category,
        original_mime=product.original_mime,
        original_sha256=product.original_sha256,
        original_width=product.original_width,
        original_height=product.original_height,
    ).model_dump(mode="json")


def _error(status_code: int, code: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": safe_error_message(code)},
    )


def _owned_generation_query(generation_id: uuid.UUID, user_id: uuid.UUID):
    return (
        select(Generation)
        .join(Generation.product)
        .where(Generation.id == generation_id, Product.user_id == user_id)
        .options(
            joinedload(Generation.assets),
            joinedload(Generation.product),
        )
    )


def _owned_generation(
    db: DBSession, generation_id: uuid.UUID, user_id: uuid.UUID
) -> Generation:
    generation = db.scalar(_owned_generation_query(generation_id, user_id))
    if generation is None:
        raise HTTPException(status_code=404, detail="Génération introuvable.")
    return generation


def _artifact_out(generation_id: uuid.UUID, asset: Asset) -> ArtifactOut:
    return ArtifactOut(
        name=asset.artifact_name,
        url=f"/api/v1/generations/{generation_id}/artifacts/{asset.artifact_name}",
        mime=asset.mime,
        sha256=asset.sha256,
        bytes=asset.byte_size,
        width=asset.width,
        height=asset.height,
        format=asset.format,
    )


def generation_out(generation: Generation) -> GenerationOut:
    error = None
    if generation.error_code:
        error = ErrorOut(
            code=generation.error_code,
            message=generation.error_message
            or safe_error_message(generation.error_code),
        )
    ambiguity = None
    if generation.error_code == "TARGET_AMBIGUOUS" and generation.candidate_boxes:
        candidates = [
            CandidateBox.model_validate(candidate)
            for candidate in generation.candidate_boxes
        ]
        ambiguity = AmbiguityOut(
            original_url=f"/api/v1/products/{generation.product_id}/image",
            candidates=candidates,
        )
    artifacts = None
    if generation.status == GenerationStatus.DONE:
        artifacts = [
            _artifact_out(generation.id, asset)
            for asset in sorted(generation.assets, key=lambda item: item.artifact_name)
        ]
    return GenerationOut(
        id=generation.id,
        product_id=generation.product_id,
        product=product_summary(generation.product),
        source_generation_id=generation.source_generation_id,
        status=generation.status,
        stage=generation.stage,
        completed_stages=generation.completed_stages or [],
        language=generation.language,
        seed=generation.seed,
        attempt_count=generation.attempt_count,
        created_at=generation.created_at,
        updated_at=generation.updated_at,
        error=error,
        ambiguity=ambiguity,
        copy=(
            generation.copy_json
            if generation.status == GenerationStatus.DONE
            else None
        ),
        artifacts=artifacts,
    )


def _validate_idempotency_key(value: str) -> str:
    if not 16 <= len(value) <= 128:
        raise _error(400, "INVALID_GENERATION_REQUEST")
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise _error(400, "INVALID_GENERATION_REQUEST")
    return value


@router.post(
    "/products/{product_id}/generations",
    response_model=GenerationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_generation(
    product_id: uuid.UUID,
    body: GenerationCreate,
    db: DBSession,
    current_user: CurrentUser,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> GenerationOut:
    key = _validate_idempotency_key(idempotency_key)
    product = db.scalar(
        select(Product).where(
            Product.id == product_id, Product.user_id == current_user.id
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable.")

    source = None
    if body.source_generation_id is not None:
        source = db.scalar(
            _owned_generation_query(body.source_generation_id, current_user.id)
        )
        if source is None or source.product_id != product.id:
            raise HTTPException(status_code=404, detail="Génération source introuvable.")

    source_brief = None
    if source is not None and isinstance(source.input_snapshot, dict):
        candidate = source.input_snapshot.get("generation")
        if isinstance(candidate, dict):
            source_brief = candidate
    try:
        generation_brief = effective_generation_brief(body, source_brief)
        effective_request = GenerationCreate.model_validate(generation_brief)
        product_snapshot = immutable_product_snapshot(product)
    except ValidationError as exc:
        raise _error(400, "INVALID_GENERATION_REQUEST") from exc
    snapshot = {
        "product": product_snapshot,
        "generation": generation_brief,
    }
    request_hash = snapshot_hash(snapshot)
    existing = db.scalar(
        select(Generation)
        .where(
            Generation.product_id == product.id,
            Generation.idempotency_key == key,
        )
        .options(
            joinedload(Generation.assets),
            joinedload(Generation.product),
        )
    )
    try:
        replay = resolve_idempotency(
            existing,
            existing.input_snapshot_hash if existing is not None else None,
            request_hash,
        )
    except IdempotencyConflict as exc:
        raise _error(409, "IDEMPOTENCY_CONFLICT") from exc
    if replay is not None:
        return generation_out(replay)

    try:
        with AIClient(
            base_url=settings.COLAB_AI_URL,
            token=settings.AI_SERVICE_TOKEN,
            timeout_seconds=settings.AI_HEALTH_TIMEOUT_SECONDS,
        ) as client:
            runtime_id = client.health().runtime_id
    except (AIClientError, ValueError) as exc:
        raise _error(503, "AI_SERVICE_UNAVAILABLE") from exc

    generation = Generation(
        id=uuid.uuid4(),
        product_id=product.id,
        product=product,
        source_generation_id=body.source_generation_id,
        language=effective_request.language,
        seed=effective_request.seed,
        input_snapshot=snapshot,
        input_snapshot_hash=request_hash,
        request_id=str(uuid.uuid4()),
        idempotency_key=key,
        status=GenerationStatus.PENDING,
        completed_stages=[],
        runtime_id=runtime_id,
    )
    db.add(generation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(Generation)
            .where(
                Generation.product_id == product.id,
                Generation.idempotency_key == key,
            )
            .options(
                joinedload(Generation.assets),
                joinedload(Generation.product),
            )
        )
        try:
            replay = resolve_idempotency(
                concurrent,
                concurrent.input_snapshot_hash if concurrent is not None else None,
                request_hash,
            )
        except IdempotencyConflict as exc:
            raise _error(409, "IDEMPOTENCY_CONFLICT") from exc
        if replay is None:
            raise _error(500, "INTERNAL_ERROR")
        return generation_out(replay)
    db.refresh(generation)
    generation.assets = []
    return generation_out(generation)


@router.get("/generations/{generation_id}", response_model=GenerationOut)
def get_generation(
    generation_id: uuid.UUID, db: DBSession, current_user: CurrentUser
) -> GenerationOut:
    return generation_out(_owned_generation(db, generation_id, current_user.id))


@router.get("/generations", response_model=GenerationHistory)
def list_generations(
    db: DBSession,
    current_user: CurrentUser,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> GenerationHistory:
    query = (
        select(Generation)
        .join(Generation.product)
        .where(Product.user_id == current_user.id)
        .options(
            joinedload(Generation.assets),
            joinedload(Generation.product),
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
    next_cursor = (
        encode_cursor(page[-1].created_at, page[-1].id)
        if has_more and page
        else None
    )
    return GenerationHistory(
        items=[generation_out(item) for item in page], next_cursor=next_cursor
    )


@router.get("/generations/{generation_id}/bundle", response_class=FileResponse)
def get_bundle(
    generation_id: uuid.UUID, db: DBSession, current_user: CurrentUser
) -> FileResponse:
    generation = _owned_generation(db, generation_id, current_user.id)
    if (
        generation.status != GenerationStatus.DONE
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
        filename=f"cosmetique-ai-{generation.id}.zip",
        content_disposition_type="attachment",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get(
    "/generations/{generation_id}/artifacts/{artifact_name}",
    response_class=FileResponse,
)
def get_artifact(
    generation_id: uuid.UUID,
    artifact_name: str,
    db: DBSession,
    current_user: CurrentUser,
) -> FileResponse:
    if artifact_name not in ARTIFACT_NAMES:
        raise HTTPException(status_code=404, detail="Artefact introuvable.")
    generation = _owned_generation(db, generation_id, current_user.id)
    if generation.status != GenerationStatus.DONE:
        raise HTTPException(status_code=404, detail="Artefact introuvable.")
    asset = next(
        (item for item in generation.assets if item.artifact_name == artifact_name),
        None,
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="Artefact introuvable.")
    try:
        path = storage.path_for(asset.storage_key)
        if not path.is_file() or path.is_symlink():
            raise StorageError("missing")
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="Artefact introuvable.") from exc
    disposition = "inline"
    response = FileResponse(
        path,
        media_type=asset.mime,
        filename=asset.artifact_name,
        content_disposition_type=disposition,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
