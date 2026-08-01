"""Single-process PostgreSQL worker with leases and one transient retry."""
from __future__ import annotations

import logging
import hashlib
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session, joinedload

from ai_core.schemas import PipelineSnapshot
from app.core.config import settings
from app.database import SessionLocal
from app.models import (
    Asset,
    AssetFormat,
    Generation,
    GenerationStage,
    GenerationStatus,
)
from app.services.ai_client import (
    AIClient,
    AIClientError,
    AIRuntimeLost,
    AIServiceUnavailable,
)
from app.services.artifacts import (
    ARTIFACT_NAMES,
    ArtifactChecksumMismatch,
    ArtifactContractError,
    CHECKSUM_MEMBER_NAMES,
    validate_bundle,
)
from app.services.generation_jobs import safe_error_message
from app.services.storage import GenerationInstall, StorageError, storage


logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 2
ALLOWED_REMOTE_ERRORS = {
    "TARGET_NOT_FOUND",
    "TARGET_AMBIGUOUS",
    "EXTRACTION_FAILED",
    "MASK_QUALITY_FAILED",
    "CLAIM_SAFETY_FAILED",
    "INVALID_GENERATION_REQUEST",
    "ARTIFACT_CONTRACT_FAILED",
    "INTERNAL_ERROR",
}


class GenerationStaleError(RuntimeError):
    error_code = "GENERATION_STALE"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def job_deadline_exceeded(
    started_at: datetime, now: datetime, deadline_seconds: int
) -> bool:
    return now >= started_at + timedelta(seconds=deadline_seconds)


def claim_statement(now: datetime | None = None):
    now = now or utcnow()
    lease_cutoff = now - timedelta(seconds=settings.WORKER_LEASE_SECONDS)
    return (
        select(Generation)
        .where(
            or_(
                and_(
                    Generation.status == GenerationStatus.PENDING,
                    or_(
                        Generation.next_attempt_at.is_(None),
                        Generation.next_attempt_at <= now,
                    ),
                    Generation.attempt_count < MAX_ATTEMPTS,
                ),
                and_(
                    Generation.status == GenerationStatus.PROCESSING,
                    Generation.heartbeat_at.is_not(None),
                    Generation.heartbeat_at < lease_cutoff,
                    Generation.attempt_count < MAX_ATTEMPTS,
                ),
            )
        )
        .order_by(Generation.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def exhausted_lease_statement(now: datetime | None = None):
    now = now or utcnow()
    lease_cutoff = now - timedelta(seconds=settings.WORKER_LEASE_SECONDS)
    return (
        update(Generation)
        .where(
            Generation.status == GenerationStatus.PROCESSING,
            Generation.heartbeat_at.is_not(None),
            Generation.heartbeat_at < lease_cutoff,
            Generation.attempt_count >= MAX_ATTEMPTS,
        )
        .values(
            status=GenerationStatus.ERROR,
            error_code="GENERATION_STALE",
            error_message=safe_error_message("GENERATION_STALE"),
            completed_at=now,
            claimed_by=None,
            claimed_at=None,
            heartbeat_at=None,
            next_attempt_at=None,
        )
    )


def is_transient_error(error: Exception) -> bool:
    return isinstance(error, AIServiceUnavailable)


def expected_target_selection(input_snapshot: dict) -> dict | None:
    generation = input_snapshot.get("generation")
    if not isinstance(generation, dict):
        raise RuntimeError("Snapshot de génération invalide.")
    target = generation.get("target_hint")
    if target is not None and not isinstance(target, dict):
        raise RuntimeError("Sélection cible du snapshot invalide.")
    return target


def browser_candidate_boxes(candidates) -> list[dict]:
    return [candidate.model_dump(mode="json") for candidate in candidates]


def remote_generation_input(input_snapshot: dict) -> dict:
    return PipelineSnapshot.model_validate(input_snapshot).model_dump(mode="json")


def owns_processing_lease(generation: Generation | None, worker_id: str) -> bool:
    return bool(
        generation is not None
        and generation.status == GenerationStatus.PROCESSING
        and generation.claimed_by == worker_id
    )


class GenerationWorker:
    def __init__(self, worker_id: str | None = None):
        host = socket.gethostname()
        self.worker_id = (worker_id or f"{settings.WORKER_ID}@{host}")[:100]

    def _new_client(self) -> AIClient:
        return AIClient(
            base_url=settings.COLAB_AI_URL,
            token=settings.AI_SERVICE_TOKEN,
            timeout_seconds=settings.AI_READ_TIMEOUT_SECONDS,
        )

    def claim(self) -> uuid.UUID | None:
        with SessionLocal() as db, db.begin():
            now = utcnow()
            reaped = db.execute(exhausted_lease_statement(now))
            if reaped.rowcount:
                logger.warning("worker_reaped_exhausted_leases count=%s", reaped.rowcount)
            generation = db.scalars(claim_statement(now)).first()
            if generation is None:
                return None
            generation.status = GenerationStatus.PROCESSING
            generation.claimed_by = self.worker_id
            generation.claimed_at = now
            generation.heartbeat_at = now
            generation.started_at = generation.started_at or now
            generation.attempt_count += 1
            generation.next_attempt_at = None
            generation.error_code = None
            generation.error_message = None
            db.flush()
            return generation.id

    def heartbeat(
        self,
        generation_id: uuid.UUID,
        *,
        stage: GenerationStage | None = None,
        completed_stages: list[GenerationStage] | None = None,
    ) -> None:
        with SessionLocal() as db, db.begin():
            generation = db.get(Generation, generation_id)
            if (
                generation is None
                or generation.status != GenerationStatus.PROCESSING
                or generation.claimed_by != self.worker_id
            ):
                raise RuntimeError("Le lease du job a été perdu.")
            generation.heartbeat_at = utcnow()
            if stage is not None:
                generation.stage = stage
            if completed_stages is not None:
                generation.completed_stages = [value.value for value in completed_stages]

    def _load(self, generation_id: uuid.UUID) -> Generation:
        with SessionLocal() as db:
            generation = db.scalar(
                select(Generation)
                .where(Generation.id == generation_id)
                .options(joinedload(Generation.product))
            )
            if generation is None:
                raise RuntimeError("Job introuvable.")
            db.expunge(generation)
            db.expunge(generation.product)
            return generation

    def _set_remote_identity(
        self, generation_id: uuid.UUID, remote_job_id: str, runtime_id: str
    ) -> None:
        with SessionLocal() as db, db.begin():
            generation = db.get(Generation, generation_id)
            if not owns_processing_lease(generation, self.worker_id):
                raise RuntimeError("Job non modifiable.")
            generation.remote_job_id = remote_job_id
            generation.runtime_id = runtime_id
            generation.heartbeat_at = utcnow()

    def _finish_remote_error(
        self,
        generation_id: uuid.UUID,
        code: str,
        candidates: list[dict] | None,
    ) -> None:
        normalized_code = code.value if hasattr(code, "value") else code
        safe_code = (
            normalized_code
            if normalized_code in ALLOWED_REMOTE_ERRORS
            else "REMOTE_PROTOCOL_ERROR"
        )
        with SessionLocal() as db, db.begin():
            generation = db.get(Generation, generation_id)
            if not owns_processing_lease(generation, self.worker_id):
                return
            generation.status = GenerationStatus.ERROR
            generation.error_code = safe_code
            generation.error_message = safe_error_message(safe_code)
            generation.candidate_boxes = (
                candidates if safe_code == "TARGET_AMBIGUOUS" else None
            )
            generation.completed_at = utcnow()
            generation.claimed_by = None
            generation.heartbeat_at = None

    def _install_success(
        self,
        generation_snapshot: Generation,
        bundle: bytes,
        expected_runtime_id: str,
    ) -> None:
        generation_id = generation_snapshot.id
        target_selection = expected_target_selection(
            generation_snapshot.input_snapshot
        )
        target_expectation = (
            {"expected_target_selection": target_selection}
            if target_selection is not None
            else {"require_target_selection": True}
        )
        validated = validate_bundle(
            bundle,
            expected_runtime_id=expected_runtime_id,
            expected_generation_id=str(generation_id),
            expected_request_id=generation_snapshot.request_id,
            expected_input_snapshot_hash=generation_snapshot.input_snapshot_hash,
            expected_seed=generation_snapshot.seed,
            expected_language=generation_snapshot.language,
            expected_source_size=(
                generation_snapshot.product.original_width,
                generation_snapshot.product.original_height,
            ),
            **target_expectation,
        )
        installed: GenerationInstall = storage.install_generation(
            str(generation_id), bundle, validated.members
        )
        records = getattr(
            validated,
            "records",
            {item["name"]: item for item in validated.manifest.get("artifacts", [])},
        )

        try:
            with SessionLocal() as db, db.begin():
                generation = db.get(Generation, generation_id, with_for_update=True)
                if (
                    not owns_processing_lease(generation, self.worker_id)
                    or generation.runtime_id != expected_runtime_id
                ):
                    raise RuntimeError("Job non modifiable.")
                db.execute(delete(Asset).where(Asset.generation_id == generation_id))
                for name in sorted(ARTIFACT_NAMES & set(installed.artifact_keys)):
                    if name == "manifest.json":
                        payload = validated.members[name]
                        record = {
                            "mime": "application/json",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "bytes": len(payload),
                            "width": None,
                            "height": None,
                        }
                    else:
                        record = records[name]
                    asset_format = None
                    if name in {"instagram.jpg", "facebook.jpg", "linkedin.jpg"}:
                        asset_format = AssetFormat(name.removesuffix(".jpg"))
                    db.add(
                        Asset(
                            generation_id=generation_id,
                            artifact_name=name,
                            format=asset_format,
                            storage_key=installed.artifact_keys[name],
                            mime=record["mime"],
                            sha256=record["sha256"],
                            byte_size=record["bytes"],
                            width=record.get("width"),
                            height=record.get("height"),
                        )
                    )
                generation.status = GenerationStatus.DONE
                generation.stage = GenerationStage.PACKAGING
                generation.completed_stages = [stage.value for stage in GenerationStage]
                generation.copy_json = {
                    **validated.copy,
                    "_meta": {
                        **validated.copy.get("_meta", {}),
                        "ocr": validated.ocr,
                    },
                }
                generation.artifact_manifest = validated.manifest
                generation.bundle_storage_key = installed.bundle_key
                generation.bundle_checksum = validated.checksum
                generation.completed_at = utcnow()
                generation.claimed_by = None
                generation.heartbeat_at = None
        except Exception:
            if installed.created:
                self._cleanup_failed_install(generation_id, validated.checksum)
            raise

    def _cleanup_failed_install(
        self, generation_id: uuid.UUID, expected_bundle_sha256: str
    ) -> None:
        """Delete only while the database still proves this worker owns the lease."""
        try:
            with SessionLocal() as db, db.begin():
                generation = db.get(Generation, generation_id, with_for_update=True)
                if (
                    not owns_processing_lease(generation, self.worker_id)
                    or generation.bundle_storage_key is not None
                ):
                    return
                storage.delete_generation(
                    str(generation_id),
                    expected_bundle_sha256=expected_bundle_sha256,
                )
        except Exception:
            logger.exception(
                "generation_cleanup_failed generation_id=%s", generation_id
            )

    def _fail_or_retry(self, generation_id: uuid.UUID, error: Exception) -> None:
        with SessionLocal() as db, db.begin():
            generation = db.get(Generation, generation_id)
            if not owns_processing_lease(generation, self.worker_id):
                return
            if is_transient_error(error) and generation.attempt_count < MAX_ATTEMPTS:
                generation.status = GenerationStatus.PENDING
                generation.next_attempt_at = utcnow() + timedelta(
                    seconds=settings.WORKER_RETRY_DELAY_SECONDS
                )
                generation.claimed_by = None
                generation.claimed_at = None
                generation.heartbeat_at = None
                return

            if isinstance(error, AIClientError):
                code = error.error_code
            elif isinstance(error, ArtifactChecksumMismatch):
                code = "ARTIFACT_CHECKSUM_MISMATCH"
            elif isinstance(error, ArtifactContractError):
                code = "ARTIFACT_CONTRACT_FAILED"
            elif isinstance(error, StorageError):
                code = "ARTIFACT_STORAGE_FAILED"
            elif isinstance(error, GenerationStaleError):
                code = "GENERATION_STALE"
            else:
                code = "INTERNAL_ERROR"
            generation.status = GenerationStatus.ERROR
            generation.error_code = code
            generation.error_message = safe_error_message(code)
            generation.completed_at = utcnow()
            generation.claimed_by = None
            generation.heartbeat_at = None

    def process(self, generation_id: uuid.UUID) -> None:
        generation = self._load(generation_id)
        try:
            image_bytes = storage.read_bytes(generation.product.original_storage_key)
            filename = generation.product.original_storage_key.rsplit("/", 1)[-1] or "product.jpg"
            with self._new_client() as client:
                health = client.health()
                if generation.runtime_id and health.runtime_id != generation.runtime_id:
                    raise AIRuntimeLost("Le runtime Colab a changé.")
                runtime_id = health.runtime_id
                self._set_remote_identity(generation.id, "colab-direct", runtime_id)
                bundle = client.generate_campaign(
                    image_bytes=image_bytes,
                    filename=filename,
                    fields={
                        "brand": generation.product.brand or "",
                        "product_name": generation.product.name,
                        "category": generation.product.category,
                        "tone": "premium",
                        "seed": str(generation.seed),
                        "generation_id": str(generation.id),
                        "request_id": generation.request_id,
                        "input_snapshot_hash": generation.input_snapshot_hash,
                        "language": generation.language.value,
                    },
                )
                self._install_success(generation, bundle, runtime_id)
        except Exception as exc:
            logger.error(
                "generation_failed generation_id=%s error_type=%s",
                generation_id,
                type(exc).__name__,
            )
            self._fail_or_retry(generation_id, exc)

    def run_forever(self) -> None:
        logger.info("worker_started worker_id=%s", self.worker_id)
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
    GenerationWorker().run_forever()


if __name__ == "__main__":
    main()
