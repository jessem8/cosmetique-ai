"""Durable Campaign Studio V2 worker and provider execution boundary."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import socket
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from PIL import Image
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from ai_core.schemas import GenerationRequestV2
from ai_service import (
    decode_image,
    encode_png,
    compare_interior_pixels,
    record_product_transform,
    restore_original_pixels,
    run_fail_closed_qa,
)
from app.core.config import settings
from app.database import SessionLocal
from app.models import (
    Generation,
    GenerationAttempt,
    GenerationAttemptOutcome,
    GenerationLifecycleStatus,
    GenerationVariantStatus,
    ProviderCostLedger,
)
from app.services.ai_client import (
    AIProtocolError,
    AIRemoteAuthFailed,
    AIRuntimeClient,
    AIRuntimeLost,
    AIServiceUnavailable,
)
from app.services.ai_pipeline import PixelSafetyDetectors, load_lock_object
from app.services.generation_v2 import (
    finalize_cost,
    record_attempt,
    release_cost,
    reserve_cost,
    transition_v2,
)
from app.services.storage import storage


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProviderEvent:
    """Sanitized adapter result; no prompt, image, URL or secret is accepted."""

    outcome: str
    status_code: int | None = None
    provider_request_id: str | None = None
    retry_after_seconds: int | None = None
    charged_micros: int = 0
    usage_snapshot: dict | None = None
    error_code: str | None = None


def _eligible_v2(now: datetime):
    return (
        select(Generation)
        .where(
            Generation.lifecycle_status.in_(
                [
                    GenerationLifecycleStatus.ACCEPTED.value,
                    GenerationLifecycleStatus.QUEUED.value,
                    GenerationLifecycleStatus.WAITING_FOR_PROVIDER.value,
                ]
            ),
            or_(
                Generation.next_attempt_at.is_(None),
                Generation.next_attempt_at <= now,
            ),
            # Accepted/unknown rows are never claimed by the V1 worker.
            Generation.lifecycle_status.is_not(None),
        )
        .order_by(Generation.created_at.asc(), Generation.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )


class V2GenerationWorker:
    def __init__(self, worker_id: str | None = None):
        self.worker_id = (worker_id or f"v2-{settings.WORKER_ID}@{socket.gethostname()}")[:100]

    def claim(self) -> uuid.UUID | None:
        with SessionLocal() as db, db.begin():
            now = utcnow()
            lease_cutoff = now - timedelta(seconds=settings.WORKER_LEASE_SECONDS)
            stale_rows = db.scalars(
                select(Generation).where(
                    Generation.lifecycle_status.in_(
                        [
                            GenerationLifecycleStatus.QUEUED.value,
                            GenerationLifecycleStatus.WAITING_FOR_PROVIDER.value,
                            GenerationLifecycleStatus.PROCESSING_LOCK.value,
                            GenerationLifecycleStatus.PLANNING_SCENE.value,
                            GenerationLifecycleStatus.GENERATING.value,
                            GenerationLifecycleStatus.COMPOSITING.value,
                        ]
                    ),
                    Generation.heartbeat_at.is_not(None),
                    Generation.heartbeat_at < lease_cutoff,
                )
            ).all()
            for stale in stale_rows:
                stale.claimed_by = None
                stale.claimed_at = None
                stale.heartbeat_at = None
                if stale.provider_request_id or stale.unknown_remote_completion:
                    stale.unknown_remote_completion = True
                    stale.lifecycle_status = GenerationLifecycleStatus.NEEDS_REVIEW.value
                    stale.last_provider_error_code = "PROVIDER_COMPLETION_UNKNOWN"
                else:
                    stale.lifecycle_status = GenerationLifecycleStatus.QUEUED.value
                    stale.next_attempt_at = now
            row = db.scalars(_eligible_v2(now)).first()
            if row is None:
                return None
            row.lifecycle_status = GenerationLifecycleStatus.QUEUED.value
            row.claimed_by = self.worker_id
            row.claimed_at = now
            row.heartbeat_at = now
            row.next_attempt_at = None
            row.retryable_error = False
            return row.id

    def heartbeat(self, generation_id: uuid.UUID) -> None:
        with SessionLocal() as db, db.begin():
            row = db.get(Generation, generation_id)
            if not row or row.lifecycle_status is None or row.claimed_by != self.worker_id:
                raise RuntimeError("Le lease V2 du job a été perdu.")
            row.heartbeat_at = utcnow()

    def _load_owned(self, db, generation_id: uuid.UUID) -> Generation:
        row = db.get(Generation, generation_id)
        if not row or row.lifecycle_status is None:
            raise RuntimeError("Job V2 introuvable.")
        if row.claimed_by != self.worker_id and not row.unknown_remote_completion:
            raise RuntimeError("Le lease V2 du job a été perdu.")
        return row

    def mark_waiting_for_provider(self, generation_id: uuid.UUID) -> None:
        with SessionLocal() as db, db.begin():
            row = self._load_owned(db, generation_id)
            current = GenerationLifecycleStatus(row.lifecycle_status)
            if current is GenerationLifecycleStatus.ACCEPTED:
                transition_v2(db, row, GenerationLifecycleStatus.QUEUED)
            if GenerationLifecycleStatus(row.lifecycle_status) is GenerationLifecycleStatus.QUEUED:
                transition_v2(db, row, GenerationLifecycleStatus.WAITING_FOR_PROVIDER)
            row.heartbeat_at = utcnow()

    def start_attempt(self, generation_id: uuid.UUID, *, estimated_cost_micros: int = 0) -> int:
        """Reserve an attempt before external submission; retries are bounded."""
        with SessionLocal() as db, db.begin():
            row = self._load_owned(db, generation_id)
            if row.cancellation_requested:
                try:
                    transition_v2(db, row, GenerationLifecycleStatus.CANCELLED)
                except ValueError:
                    row.lifecycle_status = GenerationLifecycleStatus.CANCELLED.value
                return int(row.attempt_count or 0)
            attempts = int(row.attempt_count or 0) + 1
            max_attempts = int((row.request_v2 or {}).get("budget", {}).get("max_attempts", settings.V2_MAX_ATTEMPTS))
            if attempts > min(max_attempts, settings.V2_MAX_ATTEMPTS):
                transition_v2(
                    db,
                    row,
                    GenerationLifecycleStatus.FAILED,
                    error_code="RETRY_EXHAUSTED",
                    retryable=False,
                )
                return attempts
            reserve_cost(
                db,
                row,
                attempt=attempts,
                amount_micros=estimated_cost_micros,
            )
            row.attempt_count = attempts
            row.heartbeat_at = utcnow()
            current = GenerationLifecycleStatus(row.lifecycle_status)
            if current is GenerationLifecycleStatus.ACCEPTED:
                transition_v2(db, row, GenerationLifecycleStatus.QUEUED)
                current = GenerationLifecycleStatus.QUEUED
            if current is GenerationLifecycleStatus.QUEUED:
                transition_v2(db, row, GenerationLifecycleStatus.WAITING_FOR_PROVIDER)
                current = GenerationLifecycleStatus.WAITING_FOR_PROVIDER
            if current is GenerationLifecycleStatus.WAITING_FOR_PROVIDER:
                transition_v2(db, row, GenerationLifecycleStatus.PROCESSING_LOCK)
            record_attempt(
                db,
                row,
                attempt=attempts,
                outcome=GenerationAttemptOutcome.PROCESSING,
                request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
            )
            return attempts

    def _generation_query(self, generation_id: uuid.UUID):
        return (
            select(Generation)
            .where(Generation.id == generation_id, Generation.lifecycle_status.is_not(None))
            .options(
                selectinload(Generation.product),
                selectinload(Generation.product_lock_revision),
                selectinload(Generation.v2_variants),
                selectinload(Generation.v2_attempts),
                selectinload(Generation.cost_ledger),
            )
        )

    def _snapshot(self, generation_id: uuid.UUID) -> dict[str, Any]:
        with SessionLocal() as db:
            row = db.scalar(self._generation_query(generation_id))
            if row is None or row.product is None or row.product_lock_revision is None:
                raise RuntimeError("Job V2 or product-lock snapshot is unavailable.")
            product = row.product
            lock = row.product_lock_revision
            return {
                "id": row.id,
                "request": dict(row.request_v2 or {}),
                "request_hash": row.request_v2_hash or row.input_snapshot_hash,
                "product": SimpleNamespace(
                    id=product.id,
                    user_id=product.user_id,
                    name=product.name,
                    brand=product.brand,
                    category=product.category,
                    original_storage_key=product.original_storage_key,
                    original_mime=product.original_mime,
                    original_sha256=product.original_sha256,
                    original_width=product.original_width,
                    original_height=product.original_height,
                    original_exif_orientation=product.original_exif_orientation,
                ),
                "lock": SimpleNamespace(
                    id=lock.id,
                    product_id=lock.product_id,
                    owner_id=lock.owner_id,
                    revision=lock.revision,
                    status=lock.status,
                    source_sha256=lock.source_sha256,
                    source_storage_key=lock.source_storage_key,
                    source_mime=lock.source_mime,
                    source_width=lock.source_width,
                    source_height=lock.source_height,
                    source_exif_orientation=lock.source_exif_orientation,
                    target_geometry=lock.target_geometry,
                    mask_storage_key=lock.mask_storage_key,
                    mask_sha256=lock.mask_sha256,
                    cutout_storage_key=lock.cutout_storage_key,
                    cutout_sha256=lock.cutout_sha256,
                    prompts=lock.prompts,
                    scene_spec=lock.scene_spec,
                    metrics=lock.metrics,
                    model_provenance=lock.model_provenance,
                    runtime_lock_id=(lock.model_provenance or {}).get("runtime_lock_id"),
                ),
            }

    def _set_lifecycle(self, generation_id: uuid.UUID, *targets: GenerationLifecycleStatus) -> None:
        with SessionLocal() as db, db.begin():
            row = self._load_owned(db, generation_id)
            for target in targets:
                current = GenerationLifecycleStatus(row.lifecycle_status)
                if current is target:
                    continue
                transition_v2(db, row, target)
            row.heartbeat_at = utcnow()

    @staticmethod
    def _provider_usage(provider: Any) -> dict[str, Any] | None:
        if provider is None:
            return None
        usage = {
            "credits_before": getattr(provider, "last_credits_before", None),
            "credits_after": getattr(provider, "last_credits_after", None),
        }
        return usage if any(value is not None for value in usage.values()) else None

    def _fail_job(
        self,
        generation_id: uuid.UUID,
        *,
        code: str,
        unknown: bool = False,
        provider_request_id: str | None = None,
        client_request_id: str | None = None,
        usage_snapshot: dict[str, Any] | None = None,
    ) -> None:
        with SessionLocal() as db, db.begin():
            row = db.get(Generation, generation_id)
            if row is None or row.lifecycle_status is None:
                return
            attempt = max(1, int(row.attempt_count or 1))
            cost_row = db.scalar(
                select(ProviderCostLedger).where(
                    ProviderCostLedger.generation_id == row.id,
                    ProviderCostLedger.attempt == attempt,
                )
            )
            if cost_row is not None:
                if unknown:
                    finalize_cost(
                        db,
                        row,
                        cost_row,
                        charged_micros=0,
                        usage_snapshot=usage_snapshot,
                        uncertain=True,
                    )
                else:
                    release_cost(db, row, cost_row)
            recorded_usage = dict(usage_snapshot or {})
            if client_request_id:
                recorded_usage.setdefault("client_request_id", client_request_id)
            record_attempt(
                db,
                row,
                attempt=attempt,
                outcome=GenerationAttemptOutcome.UNKNOWN if unknown else GenerationAttemptOutcome.FAILED,
                request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                provider_request_id=provider_request_id,
                error_code=code,
                retryable=False,
                usage_snapshot=recorded_usage or None,
            )
            row.provider_request_id = provider_request_id or row.provider_request_id
            row.last_provider_error_code = code
            if unknown:
                row.unknown_remote_completion = True
                target = GenerationLifecycleStatus.NEEDS_REVIEW
            else:
                target = GenerationLifecycleStatus.FAILED
            try:
                transition_v2(db, row, target, error_code=code, retryable=False)
            except ValueError:
                row.lifecycle_status = target.value
            row.claimed_by = None
            row.claimed_at = None
            row.heartbeat_at = None

    @staticmethod
    def _bundle(
        snapshot: dict[str, Any],
        outcome: Any,
    ) -> tuple[bytes, dict[str, bytes], dict[str, Any], int, str | None]:
        product = snapshot["product"]
        lock = snapshot["lock"]
        source = storage.read_bytes(product.original_storage_key)
        mask = storage.read_bytes(lock.mask_storage_key)
        cutout = storage.read_bytes(lock.cutout_storage_key)
        members: dict[str, bytes] = {
            "source.png": source,
            "mask.png": mask,
            "cutout.png": cutout,
        }
        variants: list[dict[str, Any]] = []
        charged_usd = 0.0
        provider_request_id = None
        for index, variant in enumerate(outcome.variants):
            name = f"variant-{index + 1}.png"
            payload = variant.image.convert("RGBA")
            buffer = io.BytesIO()
            payload.save(buffer, format="PNG", optimize=True)
            image_bytes = buffer.getvalue()
            members[name] = image_bytes
            manifest = variant.manifest.to_dict()
            usage = dict(variant.manifest.usage)
            provenance = {
                "provider": variant.manifest.provider,
                "model": variant.manifest.model,
                "request_id": variant.manifest.request_id,
                "client_request_id": usage.get("client_request_id") or variant.manifest.request_id,
                "provider_request_id": usage.get("provider_request_id"),
                "credits_before": usage.get("credits_before"),
                "credits_after": usage.get("credits_after"),
                "source_sha256": snapshot["lock"].source_sha256,
                "output_sha256": hashlib.sha256(image_bytes).hexdigest(),
                "cost_usd": variant.manifest.cost_usd,
            }
            manifest.update(
                {
                    "artifact_name": name,
                    "mime": "image/png",
                    "byte_size": len(image_bytes),
                    "provenance": provenance,
                }
            )
            variants.append(manifest)
            charged_usd += float(variant.manifest.cost_usd or 0.0)
            provider_request_id = provider_request_id or usage.get("provider_request_id")
        top_manifest = {
            "schema_version": "2.1.0",
            "generation_id": str(snapshot["id"]),
            "product_lock_revision_id": str(lock.id),
            "request_hash": snapshot["request_hash"],
            "source_sha256": lock.source_sha256,
            "mask_sha256": lock.mask_sha256,
            "cutout_sha256": lock.cutout_sha256,
            "preserves_product_pixels": True,
            "variants": variants,
            "charged_usd": charged_usd,
        }
        manifest_bytes = json.dumps(top_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        members["manifest.json"] = manifest_bytes
        bundle_buffer = io.BytesIO()
        with zipfile.ZipFile(bundle_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        return (
            bundle_buffer.getvalue(),
            members,
            top_manifest,
            round(charged_usd * 1_000_000),
            provider_request_id,
        )

    def _finalize_success(
        self,
        snapshot: dict[str, Any],
        outcome: Any,
        attempt: int,
    ) -> None:
        bundle, members, manifest, charged_micros, provider_request_id = self._bundle(snapshot, outcome)
        generation_id = snapshot["id"]
        installed = storage.install_generation(str(generation_id), bundle, members)
        bundle_checksum = hashlib.sha256(bundle).hexdigest()
        all_passed = all(bool(variant.qa.passed) for variant in outcome.variants)
        provider_usage = dict(outcome.variants[0].manifest.usage) if outcome.variants else {}
        with SessionLocal() as db, db.begin():
            row = db.scalar(self._generation_query(generation_id))
            if row is None or row.claimed_by != self.worker_id:
                raise RuntimeError("Le lease V2 du job a été perdu avant finalisation.")
            cost_row = db.scalar(
                select(ProviderCostLedger).where(
                    ProviderCostLedger.generation_id == row.id,
                    ProviderCostLedger.attempt == attempt,
                )
            )
            if cost_row is not None:
                finalize_cost(
                    db,
                    row,
                    cost_row,
                    charged_micros=min(charged_micros, int(cost_row.reserved_micros or 0)),
                    usage_snapshot={
                        "provider": outcome.variants[0].manifest.provider if outcome.variants else None,
                        "model": outcome.variants[0].manifest.model if outcome.variants else None,
                        "provider_request_id": provider_usage.get("provider_request_id"),
                        "client_request_id": provider_usage.get("client_request_id"),
                        "credits_before": provider_usage.get("credits_before"),
                        "credits_after": provider_usage.get("credits_after"),
                        "charged_usd": charged_micros / 1_000_000,
                    },
                )
            record_attempt(
                db,
                row,
                attempt=attempt,
                outcome=GenerationAttemptOutcome.SUCCEEDED,
                request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                provider_request_id=provider_request_id,
                usage_snapshot={
                    "variants": len(outcome.variants),
                    "provider_request_id": provider_usage.get("provider_request_id"),
                    "client_request_id": provider_usage.get("client_request_id"),
                    "credits_before": provider_usage.get("credits_before"),
                    "credits_after": provider_usage.get("credits_after"),
                    "charged_usd": charged_micros / 1_000_000,
                },
            )
            for index, (variant_row, generated) in enumerate(zip(row.v2_variants, outcome.variants)):
                entry = manifest["variants"][index]
                variant_row.status = (
                    GenerationVariantStatus.READY.value
                    if generated.qa.passed
                    else GenerationVariantStatus.NEEDS_REVIEW.value
                )
                variant_row.artifact_manifest = entry
                variant_row.qa_manifest = {
                    "passed": generated.qa.passed,
                    "status": generated.qa.status,
                    "score": 1.0 if generated.qa.passed else 0.0,
                    "findings": list(generated.qa.findings),
                    "pixel_comparison": generated.qa.pixel_comparison.__dict__ if generated.qa.pixel_comparison else None,
                }
                variant_row.provider_usage = {
                    "provider": generated.manifest.provider,
                    "model": generated.manifest.model,
                    "request_id": generated.manifest.request_id,
                    **dict(generated.manifest.usage),
                    "source_sha256": snapshot["lock"].source_sha256,
                    "output_sha256": generated.manifest.image_sha256,
                    "cost_usd": generated.manifest.cost_usd,
                }
                variant_row.storage_key = installed.artifact_keys[f"variant-{index + 1}.png"]
                variant_row.checksum = generated.manifest.image_sha256
            row.artifact_manifest = manifest
            row.bundle_storage_key = installed.bundle_key
            row.bundle_checksum = bundle_checksum
            row.provider_request_id = provider_request_id
            transition_v2(db, row, GenerationLifecycleStatus.COMPOSITING)
            transition_v2(db, row, GenerationLifecycleStatus.QUALITY_REVIEW)
            transition_v2(
                db,
                row,
                GenerationLifecycleStatus.READY if all_passed else GenerationLifecycleStatus.NEEDS_REVIEW,
                error_code=None if all_passed else "QA_NEEDS_REVIEW",
            )
            row.claimed_by = None
            row.claimed_at = None
            row.heartbeat_at = None

    @staticmethod
    def _editable_boundary_mask(lock: Any) -> bytes:
        """Return the accepted mask for local/runtime provenance only."""
        raw = Image.open(io.BytesIO(lock.mask_png)).convert("L")
        rgba = Image.new("RGBA", raw.size, (255, 255, 255, 0))
        rgba.putalpha(raw)
        return encode_png(rgba, mode="RGBA")

    def _compose_local_result(
        self,
        *,
        snapshot: dict[str, Any],
        lock: Any,
        request: GenerationRequestV2,
        background: dict[str, Any],
    ) -> dict[str, Any]:
        """Paste the immutable Product Lock cutout over the SDXL scene."""
        generated = decode_image(background["image_bytes"]).convert("RGBA")
        placement = request.target_box or request.target_hint or snapshot["lock"].target_geometry
        transform = record_product_transform(lock, generated.size, placement=placement)
        restored, transform, protected_mask = restore_original_pixels(
            generated,
            lock,
            transform=transform,
        )
        expected, _, _ = restore_original_pixels(
            generated,
            lock,
            transform=transform,
        )
        image_bytes = encode_png(restored, mode="RGBA")
        persisted = decode_image(image_bytes).convert("RGBA")
        comparison = compare_interior_pixels(persisted, expected, protected_mask)
        detector = PixelSafetyDetectors(
            lock,
            Image.open(io.BytesIO(lock.mask_png)).convert("L"),
        )
        qa = run_fail_closed_qa(
            restored,
            expected,
            protected_mask,
            lock,
            duplicate_detector=detector,
            person_hand_detector=detector,
            require_detectors=True,
        )
        passed = bool(qa.passed and comparison.exact)
        return {
            "stage": "final_candidate",
            "status": "ready" if passed else "needs_review",
            "image_bytes": image_bytes,
            "sha256": hashlib.sha256(image_bytes).hexdigest(),
            "mime": "image/png",
            "model": background.get("model") or "local-sdxl",
            "model_revision": background.get("model_revision"),
            "prompt_sha256": background.get("prompt_sha256"),
            "qa": {
                "passed": passed,
                "status": "passed" if passed else "needs_review",
                "findings": list(qa.findings) + ([] if comparison.exact else ["protected_product_pixels_changed"]),
                "pixel_comparison": comparison.__dict__,
                "transform": transform.to_dict(),
            },
            "metadata": {
                **dict(background.get("metadata") or {}),
                "source": "local-sdxl-background-plus-canonical-cutout-v1",
                "source_sha256": lock.source_sha256,
                "canonical_image_sha256": lock.canonical_image_sha256,
                "mask_sha256": lock.mask_sha256,
                "cutout_sha256": lock.cutout_sha256,
                "protected_product_pixels": comparison.exact,
                "transform": transform.to_dict(),
            },
            "cost_usd": 0.0,
            "provider": "local",
            "provider_request_id": None,
        }

    def _persist_stage(
        self,
        generation_id: uuid.UUID,
        *,
        stage: str,
        image_bytes: bytes,
        checksum: str,
        index: int = 0,
        status: str = "ready",
        model: str | None = None,
        model_revision: str | None = None,
        prompt_sha256: str | None = None,
        qa: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        # Keep stage previews outside the immutable export directory. The
        # terminal installer owns generations/<id>/ and expects to create it
        # with only the validated bundle members.
        key = f"generation-stages/{generation_id}/{stage}-{index + 1}.png"
        storage.put_bytes(key, image_bytes)
        entry: dict[str, Any] = {
            "status": status,
            "storage_key": key,
            "sha256": checksum,
            "mime": "image/png",
            "bytes": len(image_bytes),
            "model": model,
            "model_revision": model_revision,
            "prompt_sha256": prompt_sha256,
            "qa": qa,
        }
        if error_code:
            entry["error_code"] = error_code
        with SessionLocal() as db, db.begin():
            row = self._load_owned(db, generation_id)
            manifest = dict(row.artifact_manifest or {})
            stages = dict(manifest.get("stages") or {})
            stages[stage] = entry
            manifest["stages"] = stages
            row.artifact_manifest = manifest
            if stage == "baseline":
                row.runtime_id = row.runtime_id or "ai-runtime"
        return entry

    def _record_provider_request(self, generation_id: uuid.UUID, attempt: int, request_id: str) -> None:
        """Durably bind the request id before a paid execute call starts."""
        with SessionLocal() as db, db.begin():
            row = self._load_owned(db, generation_id)
            row.provider_request_id = str(request_id)[:200]
            receipt = db.scalar(
                select(GenerationAttempt).where(
                    GenerationAttempt.generation_id == generation_id,
                    GenerationAttempt.attempt == attempt,
                )
            )
            if receipt is not None:
                receipt.provider_request_id = str(request_id)[:200]

    def _enhance_baseline(
        self,
        *,
        snapshot: dict[str, Any],
        request: GenerationRequestV2,
        lock: Any,
        baseline: dict[str, Any],
        generation_id: uuid.UUID,
        attempt: int,
    ) -> tuple[dict[str, Any], Any, str]:
        del generation_id, attempt
        return self._compose_local_result(
            snapshot=snapshot,
            lock=lock,
            request=request,
            background=baseline,
        ), None, ""

    def _finalize_stages(
        self,
        *,
        snapshot: dict[str, Any],
        request: GenerationRequestV2,
        baseline: dict[str, Any],
        enhanced: dict[str, Any],
        attempt: int,
        provider_request_id: str | None,
    ) -> None:
        generation_id = snapshot["id"]
        baseline_entry = self._persist_stage(
            generation_id,
            stage="baseline",
            image_bytes=baseline["image_bytes"],
            checksum=baseline["sha256"],
            model=baseline.get("model"),
            model_revision=baseline.get("model_revision"),
            prompt_sha256=snapshot.get("background_prompt", {}).get("sha256") or baseline.get("prompt_sha256"),
            qa=baseline.get("qa") if isinstance(baseline.get("qa"), dict) else None,
        )
        enhanced_entry = self._persist_stage(
            generation_id,
            stage="enhancement",
            image_bytes=enhanced["image_bytes"],
            checksum=enhanced["sha256"],
            status=enhanced["status"],
            model=enhanced.get("model"),
            model_revision=enhanced.get("model_revision"),
            prompt_sha256=enhanced.get("prompt_sha256"),
            qa=enhanced.get("qa"),
            error_code=None if enhanced["status"] == "ready" else "QA_NEEDS_REVIEW",
        )
        final_entry = dict(enhanced_entry)
        final_entry["status"] = enhanced["status"]
        with SessionLocal() as db, db.begin():
            row = db.scalar(self._generation_query(generation_id))
            if row is None or row.claimed_by != self.worker_id:
                raise RuntimeError("Le lease V2 du job a été perdu avant finalisation.")
            manifest = dict(row.artifact_manifest or {})
            stages = dict(manifest.get("stages") or {})
            stages["baseline"] = baseline_entry
            stages["enhancement"] = enhanced_entry
            stages["final_candidate"] = final_entry
            manifest.update(
                {
                    "schema_version": "2.1.0",
                    "generation_id": str(generation_id),
                    "product_lock_revision_id": str(snapshot["lock"].id),
                    "product_lock_revision": int(snapshot["lock"].revision),
                    "request_hash": snapshot["request_hash"],
                    "background_prompt": snapshot.get("background_prompt"),
                    "baseline": {
                        "sha256": baseline["sha256"],
                        "model": baseline.get("model"),
                        "model_revision": baseline.get("model_revision"),
                        "runtime_job_id": baseline.get("job_id"),
                        "metadata": baseline.get("metadata") or {},
                    },
                    "enhancement": {
                        "provider": "local",
                        "model": enhanced.get("model"),
                        "provider_request_id": None,
                        "qa": enhanced.get("qa"),
                    },
                    "stages": stages,
                }
            )
            final_key = enhanced_entry["storage_key"]
            row.v2_variants[0].status = (
                GenerationVariantStatus.READY.value
                if enhanced["status"] == "ready"
                else GenerationVariantStatus.NEEDS_REVIEW.value
            )
            row.v2_variants[0].artifact_manifest = final_entry
            row.v2_variants[0].qa_manifest = enhanced.get("qa")
            row.v2_variants[0].provider_usage = {
                "provider": "local",
                "model": enhanced.get("model"),
                "request_id": None,
                "source_sha256": snapshot["lock"].source_sha256,
                "output_sha256": enhanced["sha256"],
                "cost_usd": enhanced.get("cost_usd", 0.0),
            }
            row.v2_variants[0].storage_key = final_key
            row.v2_variants[0].checksum = enhanced["sha256"]
            # Keep the existing export contract alive with a small, inspectable
            # ZIP containing source, lock artifacts, both visual stages, and a
            # redacted manifest.
            members: dict[str, bytes] = {
                "source.png": storage.read_bytes(snapshot["product"].original_storage_key),
                "mask.png": storage.read_bytes(snapshot["lock"].mask_storage_key),
                "cutout.png": storage.read_bytes(snapshot["lock"].cutout_storage_key),
                "baseline.png": baseline["image_bytes"],
                "enhancement.png": enhanced["image_bytes"],
            }
            public_manifest = dict(manifest)
            public_manifest.pop("stages", None)
            public_manifest["stages"] = {
                stage: {
                    key: value
                    for key, value in entry.items()
                    if key != "storage_key"
                }
                for stage, entry in stages.items()
            }
            public_manifest_bytes = json.dumps(
                public_manifest,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            members["manifest.json"] = public_manifest_bytes
            bundle_buffer = io.BytesIO()
            with zipfile.ZipFile(bundle_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)
            bundle = bundle_buffer.getvalue()
            installed = storage.install_generation(str(generation_id), bundle, members)
            row.artifact_manifest = manifest
            row.bundle_storage_key = installed.bundle_key
            row.bundle_checksum = hashlib.sha256(bundle).hexdigest()
            cost_row = db.scalar(
                select(ProviderCostLedger).where(
                    ProviderCostLedger.generation_id == row.id,
                    ProviderCostLedger.attempt == attempt,
                )
            )
            charged = 0
            if cost_row is not None:
                finalize_cost(
                    db,
                    row,
                    cost_row,
                    charged_micros=min(charged, int(cost_row.reserved_micros or 0)),
                    usage_snapshot={
                        "provider": "local",
                        "model": enhanced.get("model"),
                        "provider_request_id": None,
                        "charged_usd": 0.0,
                    },
                )
            record_attempt(
                db,
                row,
                attempt=attempt,
                outcome=GenerationAttemptOutcome.SUCCEEDED,
                request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                provider_request_id=provider_request_id,
                usage_snapshot={
                    "baseline_sha256": baseline["sha256"],
                    "enhancement_sha256": enhanced["sha256"],
                    "provider_request_id": provider_request_id,
                },
            )
            row.provider_request_id = provider_request_id or row.provider_request_id
            # The process method already enters COMPOSITING before this final
            # persistence step; transition directly into QA here.
            transition_v2(db, row, GenerationLifecycleStatus.QUALITY_REVIEW)
            transition_v2(
                db,
                row,
                GenerationLifecycleStatus.READY
                if enhanced["status"] == "ready"
                else GenerationLifecycleStatus.NEEDS_REVIEW,
                error_code=None if enhanced["status"] == "ready" else "QA_NEEDS_REVIEW",
            )
            row.claimed_by = None
            row.claimed_at = None
            row.heartbeat_at = None

    def _mark_stage_error(
        self,
        generation_id: uuid.UUID,
        *,
        stage: str,
        code: str,
        unknown: bool = False,
    ) -> None:
        """Persist a truthful stage failure while retaining prior artifacts."""
        with SessionLocal() as db, db.begin():
            row = self._load_owned(db, generation_id)
            manifest = dict(row.artifact_manifest or {})
            stages = dict(manifest.get("stages") or {})
            previous = dict(stages.get(stage) or {})
            previous.update(
                {
                    "status": "needs_review" if unknown else "failed",
                    "error_code": code,
                }
            )
            stages[stage] = previous
            final = dict(stages.get("final_candidate") or {})
            final.update({"status": "needs_review" if unknown else "failed", "error_code": code})
            stages["final_candidate"] = final
            manifest["stages"] = stages
            row.artifact_manifest = manifest

    def process(self, generation_id: uuid.UUID) -> None:
        provider = None
        attempt = 0
        provider_request_id = None
        baseline = None
        try:
            snapshot = self._snapshot(generation_id)
            request = GenerationRequestV2.model_validate(snapshot["request"])
            prompt = (request.scene_prompt or "").strip()
            if len(prompt) < 12:
                raise AIProtocolError("BACKGROUND_PROMPT_REQUIRED")
            reserved = min(
                int(request.budget.max_cost_micros),
                max(1, int(settings.V2_ESTIMATED_COST_PER_VARIANT_MICROS) * request.variant_count),
            )
            attempt = self.start_attempt(generation_id, estimated_cost_micros=reserved)
            if attempt > min(request.budget.max_attempts, settings.V2_MAX_ATTEMPTS):
                return
            self._set_lifecycle(
                generation_id,
                GenerationLifecycleStatus.PLANNING_SCENE,
                GenerationLifecycleStatus.GENERATING,
            )
            lock = load_lock_object(snapshot["product"], snapshot["lock"])
            if not settings.AI_SERVICE_URL:
                raise AIServiceUnavailable("AI_SERVICE_URL is not configured.")
            with AIRuntimeClient(
                base_url=settings.AI_SERVICE_URL,
                token=settings.AI_SERVICE_TOKEN,
                connect_timeout_seconds=settings.AI_CONNECT_TIMEOUT_SECONDS,
                read_timeout_seconds=settings.AI_READ_TIMEOUT_SECONDS,
                health_timeout_seconds=settings.AI_HEALTH_TIMEOUT_SECONDS,
            ) as runtime:
                health = runtime.health()
                if not health.ready:
                    raise AIServiceUnavailable("The private GPU runtime is not inference-ready.")
                baseline = runtime.generate_baseline(
                    generation_id=str(generation_id),
                    lock_id=str(
                        snapshot["lock"].runtime_lock_id or snapshot["lock"].id
                    ),
                    category=snapshot["product"].category,
                    scene_prompt=prompt,
                    negative_prompt=(
                        request.scene.negative_prompt
                        if request.scene is not None
                        else None
                    ),
                    seed=request.seed,
                    variant_count=request.variant_count,
                    target_box=(
                        request.target_box.model_dump(mode="json")
                        if request.target_box is not None
                        else request.target_hint.model_dump(mode="json")
                        if request.target_hint is not None
                        else snapshot["lock"].target_geometry
                    ),
                    prompt_sha256=(
                        snapshot.get("background_prompt", {}).get("sha256")
                        or hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                    ),
                    request_hash=snapshot["request_hash"],
                    deadline_seconds=settings.WORKER_JOB_DEADLINE_SECONDS,
                )
            if baseline.get("status") not in {"ready", "succeeded", "completed"}:
                raise AIProtocolError("AI runtime returned a non-ready baseline.")
            self._set_lifecycle(generation_id, GenerationLifecycleStatus.COMPOSITING)
            composed = self._compose_local_result(
                snapshot=snapshot,
                lock=lock,
                request=request,
                background=baseline,
            )
            baseline = composed
            enhanced = composed
            self._finalize_stages(
                snapshot=snapshot,
                request=request,
                baseline=baseline,
                enhanced=enhanced,
                attempt=attempt,
                provider_request_id=provider_request_id,
            )
        except AIRuntimeLost:
            self._mark_stage_error(
                generation_id,
                stage="baseline" if baseline is None else "enhancement",
                code="AI_RUNTIME_LOST",
            )
            self._fail_job(generation_id, code="AI_RUNTIME_LOST", provider_request_id=provider_request_id)
        except AIRemoteAuthFailed:
            self._mark_stage_error(
                generation_id,
                stage="baseline" if baseline is None else "enhancement",
                code="REMOTE_AUTH_FAILED",
            )
            self._fail_job(generation_id, code="REMOTE_AUTH_FAILED", provider_request_id=provider_request_id)
        except AIServiceUnavailable:
            self._mark_stage_error(
                generation_id,
                stage="baseline" if baseline is None else "enhancement",
                code="AI_SERVICE_UNAVAILABLE",
            )
            self._fail_job(generation_id, code="AI_SERVICE_UNAVAILABLE", provider_request_id=provider_request_id)
        except AIProtocolError as exc:
            code = "BACKGROUND_PROMPT_REQUIRED" if "BACKGROUND_PROMPT_REQUIRED" in str(exc) else "REMOTE_PROTOCOL_ERROR"
            self._mark_stage_error(
                generation_id,
                stage="baseline" if baseline is None else "enhancement",
                code=code,
            )
            self._fail_job(generation_id, code=code, provider_request_id=provider_request_id)
        except Exception:
            logger.exception("v2_generation_failed generation_id=%s", generation_id)
            self._mark_stage_error(
                generation_id,
                stage="baseline" if baseline is None else "enhancement",
                code="V2_PIPELINE_FAILED",
            )
            self._fail_job(
                generation_id,
                code="V2_PIPELINE_FAILED",
                provider_request_id=provider_request_id,
            )
        finally:
            if provider is not None:
                provider.close()

    def handle_provider_event(self, generation_id: uuid.UUID, event: ProviderEvent) -> None:
        """Apply a receipt exactly once, including uncertain completion."""
        with SessionLocal() as db, db.begin():
            row = self._load_owned(db, generation_id)
            status_code = event.status_code
            outcome = event.outcome.lower().strip()
            attempt = max(1, int(row.attempt_count or 1))
            if outcome in {"accepted", "processing"}:
                if event.provider_request_id:
                    row.provider_request_id = event.provider_request_id[:200]
                record_attempt(
                    db,
                    row,
                    attempt=attempt,
                    outcome=GenerationAttemptOutcome.ACCEPTED,
                    request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                    provider_request_id=event.provider_request_id,
                )
                row.heartbeat_at = utcnow()
                current = GenerationLifecycleStatus(row.lifecycle_status)
                if current is GenerationLifecycleStatus.PROCESSING_LOCK:
                    transition_v2(db, row, GenerationLifecycleStatus.PLANNING_SCENE)
                    transition_v2(db, row, GenerationLifecycleStatus.GENERATING)
                return

            if outcome in {"429", "rate_limited", "busy"} or status_code == 429:
                from app.models import ProviderCostLedger

                cost_row = db.scalar(
                    select(ProviderCostLedger).where(
                        ProviderCostLedger.generation_id == row.id,
                        ProviderCostLedger.attempt == attempt,
                    )
                )
                if cost_row is not None:
                    release_cost(db, row, cost_row)
                delay = max(1, min(event.retry_after_seconds or 5, 3_600))
                row.next_attempt_at = utcnow() + timedelta(seconds=delay)
                row.retryable_error = True
                row.last_provider_error_code = "PROVIDER_RATE_LIMITED"
                row.heartbeat_at = None
                row.claimed_by = None
                row.claimed_at = None
                # Keep the accepted contract queued; no charge is finalized.
                row.lifecycle_status = GenerationLifecycleStatus.WAITING_FOR_PROVIDER.value
                return

            if outcome in {"402", "payment_required", "budget_exceeded"} or status_code == 402:
                from app.models import ProviderCostLedger

                cost_row = db.scalar(
                    select(ProviderCostLedger).where(
                        ProviderCostLedger.generation_id == row.id,
                        ProviderCostLedger.attempt == attempt,
                    )
                )
                if cost_row is not None:
                    release_cost(db, row, cost_row)
                record_attempt(
                    db,
                    row,
                    attempt=attempt,
                    outcome=GenerationAttemptOutcome.FAILED,
                    request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                    provider_request_id=event.provider_request_id,
                    error_code="PROVIDER_BUDGET_EXCEEDED",
                    retryable=False,
                )
                transition_v2(
                    db,
                    row,
                    GenerationLifecycleStatus.FAILED,
                    error_code="PROVIDER_BUDGET_EXCEEDED",
                )
                return

            if outcome in {"timeout", "timed_out", "unknown"}:
                record_attempt(
                    db,
                    row,
                    attempt=attempt,
                    outcome=GenerationAttemptOutcome.UNKNOWN,
                    request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                    provider_request_id=event.provider_request_id,
                    error_code="PROVIDER_COMPLETION_UNKNOWN",
                    retryable=False,
                )
                row.unknown_remote_completion = True
                row.provider_request_id = event.provider_request_id or row.provider_request_id
                row.heartbeat_at = None
                row.claimed_by = None
                row.claimed_at = None
                try:
                    transition_v2(
                        db,
                        row,
                        GenerationLifecycleStatus.NEEDS_REVIEW,
                        error_code="PROVIDER_COMPLETION_UNKNOWN",
                    )
                except ValueError:
                    row.lifecycle_status = GenerationLifecycleStatus.NEEDS_REVIEW.value
                return

            if outcome in {"cancelled", "canceled"}:
                record_attempt(
                    db,
                    row,
                    attempt=attempt,
                    outcome=GenerationAttemptOutcome.CANCELLED,
                    request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                    provider_request_id=event.provider_request_id,
                )
                row.cancellation_requested = True
                try:
                    transition_v2(db, row, GenerationLifecycleStatus.CANCELLED)
                except ValueError:
                    row.lifecycle_status = GenerationLifecycleStatus.CANCELLED.value
                return

            if outcome in {"succeeded", "success", "ready"}:
                # Find the attempt reservation without exposing ledger contents.
                from app.models import ProviderCostLedger

                cost_row = db.scalar(
                    select(ProviderCostLedger).where(
                        ProviderCostLedger.generation_id == row.id,
                        ProviderCostLedger.attempt == attempt,
                    )
                )
                if cost_row is not None:
                    finalize_cost(
                        db,
                        row,
                        cost_row,
                        charged_micros=min(event.charged_micros, cost_row.reserved_micros),
                        usage_snapshot=event.usage_snapshot,
                    )
                record_attempt(
                    db,
                    row,
                    attempt=attempt,
                    outcome=GenerationAttemptOutcome.SUCCEEDED,
                    request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                    provider_request_id=event.provider_request_id,
                    usage_snapshot=event.usage_snapshot,
                )
                row.provider_request_id = event.provider_request_id or row.provider_request_id
                current = GenerationLifecycleStatus(row.lifecycle_status)
                if current in {
                    GenerationLifecycleStatus.PROCESSING_LOCK,
                    GenerationLifecycleStatus.PLANNING_SCENE,
                }:
                    transition_v2(db, row, GenerationLifecycleStatus.GENERATING)
                elif current is GenerationLifecycleStatus.NEEDS_REVIEW:
                    transition_v2(db, row, GenerationLifecycleStatus.COMPOSITING)
                current = GenerationLifecycleStatus(row.lifecycle_status)
                if current is GenerationLifecycleStatus.GENERATING:
                    transition_v2(db, row, GenerationLifecycleStatus.COMPOSITING)
                current = GenerationLifecycleStatus(row.lifecycle_status)
                if current is GenerationLifecycleStatus.COMPOSITING:
                    transition_v2(db, row, GenerationLifecycleStatus.QUALITY_REVIEW)
                row.heartbeat_at = None
                row.claimed_by = None
                row.claimed_at = None
                return

            # Any unrecognized event is terminal and safe.  The raw event is
            # deliberately not logged or persisted.
            record_attempt(
                db,
                row,
                attempt=attempt,
                outcome=GenerationAttemptOutcome.FAILED,
                request_snapshot_hash=row.request_v2_hash or row.input_snapshot_hash,
                provider_request_id=event.provider_request_id,
                error_code="PROVIDER_PROTOCOL_ERROR",
                retryable=False,
            )
            try:
                transition_v2(
                    db,
                    row,
                    GenerationLifecycleStatus.FAILED,
                    error_code="PROVIDER_PROTOCOL_ERROR",
                )
            except ValueError:
                row.lifecycle_status = GenerationLifecycleStatus.FAILED.value

    def mark_quality_ready(self, generation_id: uuid.UUID) -> None:
        """Called only after strict variant/QA manifest validation."""
        with SessionLocal() as db, db.begin():
            row = db.get(Generation, generation_id)
            if not row or row.lifecycle_status is None:
                raise RuntimeError("Job V2 introuvable.")
            current = GenerationLifecycleStatus(row.lifecycle_status)
            if current is not GenerationLifecycleStatus.QUALITY_REVIEW:
                raise RuntimeError("Le job V2 n'est pas en revue qualité.")
            transition_v2(db, row, GenerationLifecycleStatus.READY)
            row.claimed_by = None
            row.claimed_at = None
            row.heartbeat_at = None

    def request_cancel(self, generation_id: uuid.UUID) -> None:
        with SessionLocal() as db, db.begin():
            row = db.get(Generation, generation_id)
            if not row or row.lifecycle_status is None:
                return
            row.cancellation_requested = True
            if not row.provider_request_id:
                row.lifecycle_status = GenerationLifecycleStatus.CANCELLED.value
                row.completed_at = utcnow()

    def run_forever(self) -> None:
        logger.info("v2_worker_started worker_id=%s", self.worker_id)
        while True:
            generation_id = self.claim()
            if generation_id is None:
                time.sleep(settings.WORKER_POLL_SECONDS)
                continue
            self.process(generation_id)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    V2GenerationWorker().run_forever()


if __name__ == "__main__":
    main()
