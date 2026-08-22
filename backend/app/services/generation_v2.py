"""Persistence and lifecycle primitives for Campaign Studio V2 jobs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ai_core.schemas import (
    GenerationRequestV2,
    ProductLockPrompts,
    ProductLockStatus,
    ProviderExecutionPlan,
)
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models import (
    CostLedgerStatus,
    Generation,
    GenerationAttempt,
    GenerationAttemptOutcome,
    GenerationLifecycleStatus,
    GenerationStatus,
    GenerationVariant,
    GenerationVariantStatus,
    Product,
    ProductLockRevision,
    ProviderCostLedger,
)
from app.services.generation_jobs import (
    IdempotencyConflict,
    resolve_idempotency,
    safe_error_message,
    snapshot_hash,
)
from app.services.provider_registry import ProviderProfile, require_provider_profile


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def owned_lock(
    db: Session, product_id: uuid.UUID, owner_id: uuid.UUID, revision_id: uuid.UUID
) -> ProductLockRevision:
    lock = db.scalar(
        select(ProductLockRevision).where(
            ProductLockRevision.id == revision_id,
            ProductLockRevision.product_id == product_id,
            ProductLockRevision.owner_id == owner_id,
        )
    )
    if lock is None:
        raise HTTPException(status_code=404, detail="Révision introuvable.")
    return lock


def _merge_lock_defaults(
    body: GenerationRequestV2, lock: ProductLockRevision
) -> GenerationRequestV2:
    """Fill omitted optional browser fields from the immutable lock snapshot."""

    payload = body.model_dump(mode="json")
    prompts = ProductLockPrompts.model_validate(lock.prompts or {})
    supplied = body.model_fields_set
    for field in (
        "audience",
        "benefits",
        "ingredients",
        "verified_claims",
        "cta",
        "creative_direction",
        "scene_prompt",
    ):
        if field not in supplied:
            value = getattr(prompts, field)
            if value is not None and value != []:
                payload[field] = value

    if "target_box" not in supplied and "target_hint" not in supplied:
        target = lock.target_geometry
        if isinstance(target, dict):
            payload["target_box"] = target

    if "scene" not in supplied and lock.scene_spec:
        payload["scene"] = lock.scene_spec
    return GenerationRequestV2.model_validate(payload)


def request_snapshot(
    *,
    product: Product,
    lock: ProductLockRevision,
    request: GenerationRequestV2,
    profile: ProviderProfile,
) -> dict[str, Any]:
    """Build the immutable browser->DB->worker contract snapshot."""

    return {
        "contract_version": request.contract_version,
        "product": {
            "id": str(product.id),
            "name": product.name,
            "brand": product.brand,
            "category": product.category,
            "source_sha256": product.original_sha256,
            "source_mime": product.original_mime,
            "source_width": product.original_width,
            "source_height": product.original_height,
        },
        "product_lock_revision": {
            "id": str(lock.id),
            "revision": lock.revision,
            "status": lock.status,
            "source_sha256": lock.source_sha256,
            "source_width": lock.source_width,
            "source_height": lock.source_height,
            "source_exif_orientation": lock.source_exif_orientation,
            "target_geometry": lock.target_geometry,
            "prompts": lock.prompts,
            "scene": lock.scene_spec,
        },
        # Keep all browser fields in one canonical object.  Do not trim this
        # to a provider-specific subset: this is the known contract-loss seam.
        "generation": request.model_dump(mode="json"),
        "provider": {
            "selection": profile.selection.model_dump(mode="json"),
            "capabilities": profile.capabilities.model_dump(mode="json"),
        },
    }


def worker_request_payload(generation: Generation) -> dict[str, Any]:
    """Return the complete V2 request handed to a server-side adapter.

    Adapters receive this payload after the accepted row is durable.  It is a
    pure projection of the stored snapshot: no provider URL, API key, raw
    image bytes, or private storage key is included.
    """

    request = GenerationRequestV2.model_validate(generation.request_v2 or {})
    snapshot = generation.input_snapshot.get("v2", {}) if isinstance(generation.input_snapshot, dict) else {}
    lock = snapshot.get("product_lock_revision", {}) if isinstance(snapshot, dict) else {}
    return {
        "contract_version": generation.v2_contract_version or request.contract_version,
        "generation_id": str(generation.id),
        "request_id": generation.request_id,
        "input_snapshot_hash": generation.input_snapshot_hash,
        "product_lock_revision_id": str(generation.product_lock_revision_id),
        "request": request.model_dump(mode="json"),
        "lock": {
            "id": lock.get("id"),
            "revision": lock.get("revision"),
            "source_sha256": lock.get("source_sha256"),
            "source_width": lock.get("source_width"),
            "source_height": lock.get("source_height"),
            "source_exif_orientation": lock.get("source_exif_orientation"),
            "target_geometry": lock.get("target_geometry"),
            "prompts": lock.get("prompts"),
            "scene": lock.get("scene"),
        },
        "provider": generation.provider_selection,
        "variant_count": generation.variant_count,
        "budget": {
            "authorized_micros": generation.budget_authorized_micros,
            "max_attempts": request.budget.max_attempts,
        },
    }


# Name used by a few worker adapters; keep it an alias so all adapters consume
# the same canonical projection.
remote_v2_generation_input = worker_request_payload


def _plan(
    request: GenerationRequestV2,
    snapshot_hash_value: str,
    profile: ProviderProfile,
) -> ProviderExecutionPlan:
    return ProviderExecutionPlan(
        selection=profile.selection,
        capabilities=profile.capabilities,
        request_hash=snapshot_hash_value,
        input_snapshot_hash=snapshot_hash_value,
        timeout_seconds=settings.WORKER_JOB_DEADLINE_SECONDS,
    )


def create_v2_generation(
    db: Session,
    *,
    product: Product,
    owner_id: uuid.UUID,
    lock: ProductLockRevision,
    body: GenerationRequestV2,
    idempotency_key: str,
) -> Generation:
    if lock.status != ProductLockStatus.VALIDATED.value:
        raise HTTPException(
            status_code=409,
            detail="La révision du produit doit être validée avant la génération.",
        )
    if lock.source_sha256 != product.original_sha256:
        raise HTTPException(status_code=409, detail="La source du produit a changé.")
    if body.product_lock_revision_id != lock.id:
        raise HTTPException(status_code=400, detail="Révision de produit incohérente.")
    try:
        profile = require_provider_profile(body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Profil provider non autorisé.") from exc
    if body.variant_count > profile.capabilities.max_variants:
        raise HTTPException(status_code=422, detail="Nombre de variantes non autorisé.")
    if body.budget.max_cost_micros > profile.capabilities.max_budget_micros:
        raise HTTPException(status_code=422, detail="Budget supérieur à l'enveloppe autorisée.")
    if body.budget.max_attempts > settings.V2_MAX_ATTEMPTS:
        raise HTTPException(status_code=422, detail="Nombre de tentatives non autorisé.")

    request = _merge_lock_defaults(body, lock)
    snapshot = request_snapshot(
        product=product, lock=lock, request=request, profile=profile
    )
    request_hash = snapshot_hash(snapshot)
    existing = db.scalar(
        select(Generation)
        .where(
            Generation.product_id == product.id,
            Generation.idempotency_key == idempotency_key,
        )
        .options(
            selectinload(Generation.product),
            selectinload(Generation.v2_variants),
            selectinload(Generation.v2_attempts),
            selectinload(Generation.cost_ledger),
        )
    )
    try:
        replay = resolve_idempotency(
            existing,
            (
                existing.request_v2_hash
                if existing is not None and existing.request_v2_hash
                else existing.input_snapshot_hash if existing is not None else None
            ),
            request_hash,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
    if replay is not None:
        return replay

    plan = _plan(request, request_hash, profile)
    generation = Generation(
        id=uuid.uuid4(),
        product_id=product.id,
        product=product,
        source_generation_id=None,
        language=request.language.value,
        seed=request.seed,
        input_snapshot={"v2": snapshot},
        input_snapshot_hash=request_hash,
        request_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        # Legacy status remains PENDING for compatibility.  V2 clients use
        # lifecycle_status and never infer readiness from this old enum.
        status=GenerationStatus.PENDING,
        completed_stages=[],
        v2_contract_version=request.contract_version,
        lifecycle_status=GenerationLifecycleStatus.ACCEPTED.value,
        product_lock_revision_id=lock.id,
        request_v2=request.model_dump(mode="json"),
        request_v2_hash=request_hash,
        provider_selection=request.provider.model_dump(mode="json"),
        provider_execution_plan=plan.model_dump(mode="json"),
        provider_execution_snapshot={
            "selection": plan.selection.model_dump(mode="json"),
            "capabilities": plan.capabilities.model_dump(mode="json"),
            "request_hash": request_hash,
        },
        variant_count=request.variant_count,
        budget_authorized_micros=request.budget.max_cost_micros,
        budget_reserved_micros=0,
        budget_charged_micros=0,
        unknown_remote_completion=False,
        cancellation_requested=False,
        retryable_error=False,
    )
    db.add(generation)
    # Accepted work is durably visible before any provider readiness check.
    db.flush()
    for index in range(request.variant_count):
        db.add(
            GenerationVariant(
                generation_id=generation.id,
                variant_index=index,
                status=GenerationVariantStatus.PENDING.value,
            )
        )
    db.add(
        ProviderCostLedger(
            generation_id=generation.id,
            attempt=0,
            ledger_key=f"{generation.id}:authorization",
            status=CostLedgerStatus.RESERVED.value,
            authorized_micros=request.budget.max_cost_micros,
            reserved_micros=0,
            charged_micros=0,
        )
    )
    return generation


def transition_v2(
    db: Session,
    generation: Generation,
    target: GenerationLifecycleStatus,
    *,
    error_code: str | None = None,
    retryable: bool = False,
) -> Generation:
    current = GenerationLifecycleStatus(generation.lifecycle_status or GenerationLifecycleStatus.ACCEPTED.value)
    allowed: dict[GenerationLifecycleStatus, set[GenerationLifecycleStatus]] = {
        GenerationLifecycleStatus.ACCEPTED: {
            GenerationLifecycleStatus.QUEUED,
            GenerationLifecycleStatus.WAITING_FOR_PROVIDER,
            GenerationLifecycleStatus.CANCELLED,
            GenerationLifecycleStatus.FAILED,
        },
        GenerationLifecycleStatus.QUEUED: {
            GenerationLifecycleStatus.WAITING_FOR_PROVIDER,
            GenerationLifecycleStatus.PROCESSING_LOCK,
            GenerationLifecycleStatus.CANCELLED,
            GenerationLifecycleStatus.FAILED,
        },
        GenerationLifecycleStatus.WAITING_FOR_PROVIDER: {
            GenerationLifecycleStatus.PROCESSING_LOCK,
            GenerationLifecycleStatus.QUEUED,
            GenerationLifecycleStatus.CANCELLED,
            GenerationLifecycleStatus.FAILED,
        },
        GenerationLifecycleStatus.PROCESSING_LOCK: {
            GenerationLifecycleStatus.PLANNING_SCENE,
            GenerationLifecycleStatus.CANCELLED,
            GenerationLifecycleStatus.FAILED,
        },
        GenerationLifecycleStatus.PLANNING_SCENE: {
            GenerationLifecycleStatus.GENERATING,
            GenerationLifecycleStatus.CANCELLED,
            GenerationLifecycleStatus.FAILED,
        },
        GenerationLifecycleStatus.GENERATING: {
            GenerationLifecycleStatus.COMPOSITING,
            GenerationLifecycleStatus.QUALITY_REVIEW,
            GenerationLifecycleStatus.NEEDS_REVIEW,
            GenerationLifecycleStatus.CANCELLED,
            GenerationLifecycleStatus.FAILED,
        },
        GenerationLifecycleStatus.COMPOSITING: {
            GenerationLifecycleStatus.QUALITY_REVIEW,
            GenerationLifecycleStatus.NEEDS_REVIEW,
            GenerationLifecycleStatus.FAILED,
        },
        GenerationLifecycleStatus.QUALITY_REVIEW: {
            GenerationLifecycleStatus.READY,
            GenerationLifecycleStatus.NEEDS_REVIEW,
            GenerationLifecycleStatus.FAILED,
        },
        GenerationLifecycleStatus.NEEDS_REVIEW: {
            GenerationLifecycleStatus.QUALITY_REVIEW,
            GenerationLifecycleStatus.COMPOSITING,
            GenerationLifecycleStatus.GENERATING,
            GenerationLifecycleStatus.READY,
            GenerationLifecycleStatus.CANCELLED,
            GenerationLifecycleStatus.FAILED,
        },
    }
    if target not in allowed.get(current, set()):
        raise ValueError(f"invalid V2 lifecycle transition {current.value}->{target.value}")
    generation.lifecycle_status = target.value
    generation.retryable_error = retryable
    generation.last_provider_error_code = error_code
    if target in {
        GenerationLifecycleStatus.FAILED,
        GenerationLifecycleStatus.CANCELLED,
        GenerationLifecycleStatus.READY,
    }:
        generation.completed_at = utcnow()
    return generation


def record_attempt(
    db: Session,
    generation: Generation,
    *,
    attempt: int,
    outcome: GenerationAttemptOutcome,
    request_snapshot_hash: str,
    provider_request_id: str | None = None,
    error_code: str | None = None,
    retryable: bool = False,
    usage_snapshot: dict[str, Any] | None = None,
) -> GenerationAttempt:
    existing = db.scalar(
        select(GenerationAttempt).where(
            GenerationAttempt.generation_id == generation.id,
            GenerationAttempt.attempt == attempt,
        )
    )
    if existing is not None:
        # Idempotent reconciliation: an uncertain remote response must not
        # create a second charge or a second attempt row.
        terminal = {
            GenerationAttemptOutcome.SUCCEEDED.value,
            GenerationAttemptOutcome.FAILED.value,
            GenerationAttemptOutcome.UNKNOWN.value,
            GenerationAttemptOutcome.CANCELLED.value,
        }
        if outcome.value in terminal and existing.outcome not in terminal:
            existing.outcome = outcome.value
            existing.provider_request_id = provider_request_id or existing.provider_request_id
            existing.error_code = error_code or existing.error_code
            existing.retryable = retryable
            existing.usage_snapshot = usage_snapshot or existing.usage_snapshot
            existing.completed_at = utcnow()
        return existing
    row = GenerationAttempt(
        generation_id=generation.id,
        attempt=attempt,
        provider_request_id=provider_request_id,
        outcome=outcome.value,
        request_snapshot_hash=request_snapshot_hash,
        error_code=error_code,
        retryable=retryable,
        usage_snapshot=usage_snapshot,
        started_at=utcnow(),
        completed_at=(utcnow() if outcome in {GenerationAttemptOutcome.SUCCEEDED, GenerationAttemptOutcome.FAILED, GenerationAttemptOutcome.CANCELLED} else None),
    )
    db.add(row)
    generation.provider_request_id = provider_request_id or generation.provider_request_id
    generation.unknown_remote_completion = outcome is GenerationAttemptOutcome.UNKNOWN
    return row


def reserve_cost(
    db: Session,
    generation: Generation,
    *,
    attempt: int,
    amount_micros: int,
    provider_request_id: str | None = None,
) -> ProviderCostLedger:
    """Reserve cumulative budget exactly once for an attempt."""

    if amount_micros < 0:
        raise ValueError("cost must be non-negative")
    key = f"{generation.id}:attempt:{attempt}"
    existing = db.scalar(
        select(ProviderCostLedger).where(ProviderCostLedger.ledger_key == key)
    )
    if existing is not None:
        return existing
    authorized = int(generation.budget_authorized_micros or 0)
    charged = int(generation.budget_charged_micros or 0)
    reserved = int(generation.budget_reserved_micros or 0)
    if charged + reserved + amount_micros > authorized:
        raise ValueError("cumulative provider authorization envelope exceeded")
    row = ProviderCostLedger(
        generation_id=generation.id,
        attempt=attempt,
        ledger_key=key,
        provider_request_id=provider_request_id,
        status=CostLedgerStatus.RESERVED.value,
        authorized_micros=authorized,
        reserved_micros=amount_micros,
        charged_micros=0,
    )
    db.add(row)
    generation.budget_reserved_micros = reserved + amount_micros
    return row


def finalize_cost(
    db: Session,
    generation: Generation,
    ledger: ProviderCostLedger,
    *,
    charged_micros: int,
    usage_snapshot: dict[str, Any] | None = None,
    uncertain: bool = False,
) -> ProviderCostLedger:
    if charged_micros < 0 or charged_micros > ledger.reserved_micros:
        raise ValueError("provider charge exceeds reserved authorization")
    if ledger.status in {CostLedgerStatus.CHARGED.value, CostLedgerStatus.UNKNOWN.value}:
        return ledger
    ledger.charged_micros = charged_micros
    ledger.status = CostLedgerStatus.UNKNOWN.value if uncertain else CostLedgerStatus.CHARGED.value
    ledger.usage_snapshot = usage_snapshot
    ledger.finalized_at = utcnow()
    generation.budget_reserved_micros = max(
        0, int(generation.budget_reserved_micros or 0) - int(ledger.reserved_micros or 0)
    )
    generation.budget_charged_micros = int(generation.budget_charged_micros or 0) + charged_micros
    return ledger


def release_cost(
    db: Session,
    generation: Generation,
    ledger: ProviderCostLedger,
) -> ProviderCostLedger:
    """Release a reservation when the provider confirms no work was charged."""

    if ledger.status != "reserved":
        return ledger
    ledger.status = "released"
    ledger.finalized_at = utcnow()
    generation.budget_reserved_micros = max(
        0, int(generation.budget_reserved_micros or 0) - int(ledger.reserved_micros or 0)
    )
    return ledger
