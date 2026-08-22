"""Campaign Studio V2 product-lock revision routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ai_core.schemas import ProductLockRevisionCreate, ProductLockStatus
from app.dependencies import CurrentUser, DBSession
from app.models import ProductLockRevision
from app.schemas import (
    ProductLockAction,
    ProductLockRootCreate,
    ProductLockRevisionList,
    ProductLockRevisionOut,
)
from app.services.product_locks import (
    create_revision,
    get_owned_revision,
    owned_product,
    revision_out,
    transition_revision,
)
from app.services.storage import StorageError, storage


router = APIRouter(prefix="/api/v2/product-locks", tags=["Campaign Studio V2"])
alias_router = APIRouter(prefix="/api/v2", tags=["Campaign Studio V2"])
studio_router = APIRouter(prefix="/api/v1/studio/v2", tags=["Campaign Studio V2"])


def _response(revision: ProductLockRevision) -> ProductLockRevisionOut:
    return revision_out(revision)


@router.post(
    "/products/{product_id}",
    response_model=ProductLockRevisionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_product_lock(
    product_id: uuid.UUID,
    body: ProductLockRevisionCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> ProductLockRevisionOut:
    product = owned_product(db, product_id, current_user.id)
    revision = create_revision(
        db,
        product=product,
        owner_id=current_user.id,
        request=body,
        run_ai=True,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Révision concurrente.") from exc
    db.refresh(revision)
    return _response(revision)


@router.get(
    "/products/{product_id}",
    response_model=ProductLockRevisionList,
)
def list_product_locks(
    product_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=100),
) -> ProductLockRevisionList:
    owned_product(db, product_id, current_user.id)
    query = (
        select(ProductLockRevision)
        .where(
            ProductLockRevision.product_id == product_id,
            ProductLockRevision.owner_id == current_user.id,
        )
        .order_by(ProductLockRevision.revision.desc())
        .limit(limit)
    )
    items = db.scalars(query).all()
    return ProductLockRevisionList(
        items=[_response(item) for item in items], total=len(items)
    )


@router.get("/{revision_id}", response_model=ProductLockRevisionOut)
def get_product_lock(
    revision_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> ProductLockRevisionOut:
    return _response(get_owned_revision(db, revision_id, current_user.id))


@router.post("/{revision_id}/refine", response_model=ProductLockRevisionOut, status_code=201)
def refine_product_lock(
    revision_id: uuid.UUID,
    body: ProductLockRevisionCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> ProductLockRevisionOut:
    parent = get_owned_revision(db, revision_id, current_user.id)
    product = owned_product(db, parent.product_id, current_user.id)
    revision = create_revision(
        db,
        product=product,
        owner_id=current_user.id,
        request=body,
        parent=parent,
        run_ai=True,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Révision concurrente.") from exc
    db.refresh(revision)
    return _response(revision)


def _transition(
    revision_id: uuid.UUID,
    body: ProductLockAction,
    target: ProductLockStatus,
    db,
    current_user,
) -> ProductLockRevisionOut:
    revision = get_owned_revision(db, revision_id, current_user.id)
    transition_revision(db, revision, target, reason=body.reason)
    db.commit()
    db.refresh(revision)
    return _response(revision)


@router.post("/{revision_id}/validate", response_model=ProductLockRevisionOut)
def validate_product_lock(
    revision_id: uuid.UUID,
    body: ProductLockAction,
    db: DBSession,
    current_user: CurrentUser,
) -> ProductLockRevisionOut:
    return _transition(
        revision_id, body, ProductLockStatus.VALIDATED, db, current_user
    )


@router.post("/{revision_id}/reject", response_model=ProductLockRevisionOut)
def reject_product_lock(
    revision_id: uuid.UUID,
    body: ProductLockAction,
    db: DBSession,
    current_user: CurrentUser,
) -> ProductLockRevisionOut:
    return _transition(
        revision_id, body, ProductLockStatus.REJECTED, db, current_user
    )


def get_product_lock_artifact(
    revision_id: uuid.UUID,
    artifact: str,
    db: DBSession,
    current_user: CurrentUser,
) -> FileResponse:
    revision = get_owned_revision(db, revision_id, current_user.id)
    keys = {
        "mask.png": revision.mask_storage_key,
        "cutout.png": revision.cutout_storage_key,
    }
    key = keys.get(artifact)
    if not key:
        raise HTTPException(status_code=404, detail="Artefact de lock introuvable.")
    try:
        path = storage.path_for(key)
        if not path.is_file() or path.is_symlink():
            raise StorageError("missing")
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="Artefact de lock introuvable.") from exc
    response = FileResponse(
        path,
        media_type="image/png",
        filename=artifact,
        content_disposition_type="inline",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/{revision_id}/needs-review", response_model=ProductLockRevisionOut)
def mark_product_lock_for_review(
    revision_id: uuid.UUID,
    body: ProductLockAction,
    db: DBSession,
    current_user: CurrentUser,
) -> ProductLockRevisionOut:
    return _transition(
        revision_id, body, ProductLockStatus.NEEDS_REVIEW, db, current_user
    )


# Friendly resource-oriented aliases keep integrations independent from the
# historical ``/product-locks/products`` spelling.  Both forms are the same
# authenticated implementation and therefore share ownership checks.
alias_router.add_api_route(
    "/products/{product_id}/locks",
    create_product_lock,
    methods=["POST"],
    response_model=ProductLockRevisionOut,
    status_code=status.HTTP_201_CREATED,
)
alias_router.add_api_route(
    "/products/{product_id}/locks",
    list_product_locks,
    methods=["GET"],
    response_model=ProductLockRevisionList,
)
alias_router.add_api_route(
    "/locks/{revision_id}",
    get_product_lock,
    methods=["GET"],
    response_model=ProductLockRevisionOut,
)


@studio_router.post(
    "/product-locks",
    response_model=ProductLockRevisionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_studio_product_lock(
    body: ProductLockRootCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> ProductLockRevisionOut:
    """Root Studio V2 compatibility route with product_id in the body."""

    product = owned_product(db, body.product_id, current_user.id)
    revision = create_revision(
        db,
        product=product,
        owner_id=current_user.id,
        request=ProductLockRevisionCreate.model_validate(
            body.model_dump(exclude={"product_id"})
        ),
        run_ai=True,
    )
    db.commit()
    db.refresh(revision)
    return _response(revision)


@studio_router.get("/product-locks", response_model=ProductLockRevisionList)
def list_studio_product_locks(
    db: DBSession,
    current_user: CurrentUser,
    product_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> ProductLockRevisionList:
    query = select(ProductLockRevision).where(
        ProductLockRevision.owner_id == current_user.id
    )
    if product_id is not None:
        owned_product(db, product_id, current_user.id)
        query = query.where(ProductLockRevision.product_id == product_id)
    items = db.scalars(
        query.order_by(ProductLockRevision.created_at.desc()).limit(limit)
    ).all()
    return ProductLockRevisionList(
        items=[_response(item) for item in items], total=len(items)
    )


# Resource operations use the same exact ownership-safe implementation.
studio_router.add_api_route(
    "/product-locks/{revision_id}",
    get_product_lock,
    methods=["GET"],
    response_model=ProductLockRevisionOut,
)
studio_router.add_api_route(
    "/product-locks/{revision_id}/refine",
    refine_product_lock,
    methods=["POST"],
    response_model=ProductLockRevisionOut,
    status_code=status.HTTP_201_CREATED,
)
studio_router.add_api_route(
    "/product-locks/{revision_id}/validate",
    validate_product_lock,
    methods=["POST"],
    response_model=ProductLockRevisionOut,
)
studio_router.add_api_route(
    "/product-locks/{revision_id}/reject",
    reject_product_lock,
    methods=["POST"],
    response_model=ProductLockRevisionOut,
)
studio_router.add_api_route(
    "/product-locks/{revision_id}/artifacts/{artifact}",
    get_product_lock_artifact,
    methods=["GET"],
    response_class=FileResponse,
)
alias_router.add_api_route(
    "/locks/{revision_id}/refine",
    refine_product_lock,
    methods=["POST"],
    response_model=ProductLockRevisionOut,
    status_code=status.HTTP_201_CREATED,
)
alias_router.add_api_route(
    "/locks/{revision_id}/validate",
    validate_product_lock,
    methods=["POST"],
    response_model=ProductLockRevisionOut,
)
alias_router.add_api_route(
    "/locks/{revision_id}/reject",
    reject_product_lock,
    methods=["POST"],
    response_model=ProductLockRevisionOut,
)
