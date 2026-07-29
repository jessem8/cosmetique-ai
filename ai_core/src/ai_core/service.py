from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .artifacts import MAX_BUNDLE_BYTES, PIPELINE_VERSION, canonical_json_bytes
from .errors import AICoreError, InvalidGenerationRequestError, TargetAmbiguousError
from .model_registry import pinned_model_refs
from .orchestrator import verify_source_request
from .schemas import (
    CandidateBox,
    ErrorCode,
    ErrorPayload,
    GenerationStage,
    GenerationStatus,
    JobResult,
    Manifest,
    RemoteJobRequest,
    ShortText,
    StrictModel,
)


MAX_REQUEST_JSON_BYTES = 256 * 1024
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MIN_VRAM_BYTES = 12 * 1024 * 1024 * 1024
MULTIPART_OVERHEAD_ALLOWANCE = 1024 * 1024


class GPUHealth(StrictModel):
    available: bool
    name: ShortText | None = None
    vram_bytes: int = Field(strict=True, ge=0)


class HealthResponse(StrictModel):
    ready: bool
    runtime_id: ShortText
    gpu: GPUHealth
    models: dict[str, str]
    pipeline_version: Literal["0.1.0"] = PIPELINE_VERSION


class JobCreated(StrictModel):
    id: ShortText
    runtime_id: ShortText
    status: Literal["pending", "processing"]


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    token: str
    runtime_id: str
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    max_source_pixels: int = 40_000_000
    min_vram_bytes: int = DEFAULT_MIN_VRAM_BYTES
    retained_jobs: int = 16
    artifact_dir: str | Path | None = None

    def __post_init__(self) -> None:
        if len(self.token) < 32:
            raise ValueError("runtime bearer token must contain at least 32 characters")
        if not self.runtime_id.strip() or len(self.runtime_id) > 200:
            raise ValueError("runtime ID must contain 1 to 200 characters")
        if not 1 <= self.max_image_bytes <= MAX_BUNDLE_BYTES:
            raise ValueError("runtime image limit is outside the supported range")
        if not 1 <= self.max_source_pixels <= 100_000_000:
            raise ValueError("runtime source pixel limit must be between 1 and 100M")
        if not 1 <= self.min_vram_bytes <= 128 * 1024**3:
            raise ValueError("runtime VRAM minimum is outside the supported range")
        if not 1 <= self.retained_jobs <= 100:
            raise ValueError("retained job count must be between 1 and 100")
        if self.artifact_dir is not None and not str(self.artifact_dir).strip():
            raise ValueError("runtime artifact directory must not be empty")


@dataclass(slots=True)
class _RuntimeJob:
    id: str
    request_id: str
    input_snapshot_hash: str
    status: GenerationStatus = GenerationStatus.PENDING
    stage: GenerationStage | None = None
    completed_stages: tuple[GenerationStage, ...] = ()
    error: ErrorPayload | None = None
    ambiguity: tuple[CandidateBox, ...] | None = None
    manifest: Manifest | None = None
    bundle_path: Path | None = None

    def response(self, runtime_id: str) -> JobResult:
        return JobResult(
            id=self.id,
            runtime_id=runtime_id,
            status=self.status,
            stage=self.stage,
            completed_stages=self.completed_stages,
            error=self.error,
            ambiguity=self.ambiguity,
            manifest=self.manifest,
        )


class _RequestConflict(RuntimeError):
    pass


@dataclass(slots=True)
class _JobStore:
    retained_jobs: int
    artifact_dir: Path
    owns_artifact_dir: bool
    jobs: dict[str, _RuntimeJob] = field(default_factory=dict)
    active_job_id: str | None = None
    closed: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    @staticmethod
    def _unlink(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError("runtime artifact cleanup failed") from exc

    def create(
        self,
        *,
        request_id: str,
        input_snapshot_hash: str,
    ) -> tuple[_RuntimeJob, bool]:
        with self.lock:
            if self.closed:
                raise RuntimeError("busy")
            existing = next(
                (
                    job
                    for job in self.jobs.values()
                    if job.request_id == request_id
                ),
                None,
            )
            if existing is not None:
                if existing.input_snapshot_hash != input_snapshot_hash:
                    raise _RequestConflict(
                        "request ID was reused with a different snapshot hash"
                    )
                return existing, False
            if self.active_job_id is not None:
                raise RuntimeError("busy")
            completed_ids = [
                job_id
                for job_id, job in self.jobs.items()
                if job.status in {GenerationStatus.DONE, GenerationStatus.ERROR}
            ]
            while len(self.jobs) >= self.retained_jobs and completed_ids:
                completed = self.jobs[completed_ids.pop(0)]
                self._unlink(completed.bundle_path)
                self.jobs.pop(completed.id, None)
            if len(self.jobs) >= self.retained_jobs:
                raise RuntimeError("busy")
            job = _RuntimeJob(
                id=f"job-{secrets.token_urlsafe(24)}",
                request_id=request_id,
                input_snapshot_hash=input_snapshot_hash,
            )
            self.jobs[job.id] = job
            self.active_job_id = job.id
            return job, True

    def persist_bundle(self, job_id: str, bundle: bytes) -> Path:
        with self.lock:
            job = self.jobs.get(job_id)
            if self.closed or job is None or self.active_job_id != job_id:
                raise RuntimeError("runtime job no longer owns artifact storage")
        temporary = self.artifact_dir / (
            f".{job_id}.{secrets.token_urlsafe(8)}.tmp"
        )
        final = self.artifact_dir / f"{job_id}.zip"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                view = memoryview(bundle)
                for offset in range(0, len(view), 1024 * 1024):
                    stream.write(view[offset : offset + 1024 * 1024])
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final)
            try:
                final.chmod(0o600)
            except OSError:
                pass
            with self.lock:
                job = self.jobs.get(job_id)
                if self.closed or job is None or self.active_job_id != job_id:
                    self._unlink(final)
                    raise RuntimeError(
                        "runtime job lost artifact storage ownership"
                    )
                job.bundle_path = final
            return final
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def discard_bundle(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            path = job.bundle_path if job is not None else None
        try:
            self._unlink(path)
        except RuntimeError:
            return False
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and job.bundle_path == path:
                job.bundle_path = None
        return True

    def get(self, job_id: str) -> _RuntimeJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def finish_active(self, job_id: str) -> None:
        with self.lock:
            if self.active_job_id == job_id:
                self.active_job_id = None

    def cleanup(self) -> None:
        with self.lock:
            self.closed = True
            paths = tuple(
                job.bundle_path
                for job in self.jobs.values()
                if job.bundle_path is not None
            )
            self.jobs.clear()
            self.active_job_id = None
        for path in paths:
            try:
                self._unlink(path)
            except RuntimeError:
                pass
        if self.owns_artifact_dir:
            shutil.rmtree(self.artifact_dir, ignore_errors=True)


def _parse_strict_json(raw: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=reject_duplicates)


def _error_detail(code: ErrorCode, message: str) -> dict[str, str]:
    return {"code": code.value, "message": message[:200]}


def create_app(
    settings: RuntimeSettings,
    *,
    orchestrator: object,
    gpu_probe: Callable[[], GPUHealth | dict[str, Any]],
    readiness_probe: Callable[[], bool] | None = None,
) -> FastAPI:
    owns_artifact_dir = settings.artifact_dir is None
    artifact_dir = (
        Path(tempfile.mkdtemp(prefix="cosmetique-ai-runtime-"))
        if owns_artifact_dir
        else Path(settings.artifact_dir).resolve()
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        artifact_dir.chmod(0o700)
    except OSError:
        pass
    store = _JobStore(
        retained_jobs=settings.retained_jobs,
        artifact_dir=artifact_dir,
        owns_artifact_dir=owns_artifact_dir,
    )
    tasks: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            pending = tuple(tasks)
            if pending:
                _, unfinished = await asyncio.wait(pending, timeout=5)
                for task in unfinished:
                    task.cancel()
            store.cleanup()

    app = FastAPI(
        title="Cosmetique AI Colab Runtime",
        version=PIPELINE_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime_artifact_dir = str(artifact_dir)
    security = HTTPBearer(auto_error=False)

    def authorization_valid(value: str | None) -> bool:
        if value is None:
            return False
        scheme, separator, credential = value.partition(" ")
        return bool(
            separator
            and scheme.casefold() == "bearer"
            and hmac.compare_digest(
                credential.encode("utf-8"),
                settings.token.encode("utf-8"),
            )
        )

    def error_response(
        *,
        status_code: int,
        code: ErrorCode,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"detail": _error_detail(code, message)},
            headers=headers,
        )

    @app.middleware("http")
    async def authenticate_and_bound_declared_length(
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Response:
        if request.url.path.startswith("/v1/"):
            if not authorization_valid(request.headers.get("authorization")):
                return error_response(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    code=ErrorCode.REMOTE_AUTH_FAILED,
                    message="runtime bearer authentication failed",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if (
                request.method == "POST"
                and request.url.path == "/v1/jobs"
            ):
                raw_length = request.headers.get("content-length")
                if raw_length is not None:
                    try:
                        declared_length = int(raw_length)
                    except ValueError:
                        declared_length = -1
                    if declared_length < 0:
                        return error_response(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            code=ErrorCode.INVALID_GENERATION_REQUEST,
                            message="request Content-Length is invalid",
                        )
                    declared_limit = (
                        settings.max_image_bytes
                        + MAX_REQUEST_JSON_BYTES
                        + MULTIPART_OVERHEAD_ALLOWANCE
                    )
                    if declared_length > declared_limit:
                        return error_response(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            code=ErrorCode.INVALID_GENERATION_REQUEST,
                            message="declared multipart request exceeds the runtime limit",
                        )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _: Request,
        __: RequestValidationError,
    ) -> JSONResponse:
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCode.INVALID_GENERATION_REQUEST,
            message="generation request violates the strict runtime contract",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if (
            isinstance(exc.detail, dict)
            and set(exc.detail) == {"code", "message"}
            and isinstance(exc.detail["code"], str)
            and isinstance(exc.detail["message"], str)
        ):
            detail = exc.detail
        else:
            code = (
                ErrorCode.INVALID_GENERATION_REQUEST
                if request.method == "POST"
                and request.url.path == "/v1/jobs"
                else ErrorCode.REMOTE_PROTOCOL_ERROR
            )
            detail = _error_detail(
                code,
                "runtime request could not be processed",
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail},
            headers=exc.headers,
        )

    def authenticate(
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> None:
        valid = (
            credentials is not None
            and credentials.scheme.casefold() == "bearer"
            and hmac.compare_digest(
                credentials.credentials.encode("utf-8"),
                settings.token.encode("utf-8"),
            )
        )
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_error_detail(
                    ErrorCode.REMOTE_AUTH_FAILED,
                    "runtime bearer authentication failed",
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

    def current_gpu() -> GPUHealth:
        try:
            value = gpu_probe()
            return value if isinstance(value, GPUHealth) else GPUHealth.model_validate(value)
        except Exception:
            return GPUHealth(available=False, name=None, vram_bytes=0)

    def runtime_ready() -> tuple[bool, GPUHealth]:
        gpu = current_gpu()
        if not gpu.available or gpu.vram_bytes < settings.min_vram_bytes:
            return False, gpu
        try:
            prerequisites = (
                bool(readiness_probe())
                if readiness_probe is not None
                else bool(orchestrator.preflight())
            )
        except Exception:
            prerequisites = False
        return prerequisites, gpu

    def public_models() -> dict[str, str]:
        return {
            name: f"{ref.repo_id}@{ref.revision}"
            for name, ref in pinned_model_refs().items()
        }

    @app.get(
        "/v1/health",
        response_model=HealthResponse,
        dependencies=[Depends(authenticate)],
    )
    def health() -> HealthResponse:
        ready, gpu = runtime_ready()
        return HealthResponse(
            ready=ready,
            runtime_id=settings.runtime_id,
            gpu=gpu,
            models=public_models(),
        )

    def execute_job(
        runtime_job: _RuntimeJob,
        envelope: RemoteJobRequest,
        source: bytes,
    ) -> None:
        with store.lock:
            runtime_job.status = GenerationStatus.PROCESSING
            runtime_job.stage = GenerationStage.ANALYSIS

        def on_stage_complete(
            stage: GenerationStage,
            completed: tuple[GenerationStage, ...],
        ) -> None:
            all_stages = tuple(GenerationStage)
            with store.lock:
                runtime_job.completed_stages = completed
                runtime_job.stage = (
                    all_stages[len(completed)]
                    if len(completed) < len(all_stages)
                    else GenerationStage.PACKAGING
                )

        try:
            output = orchestrator.run(
                envelope,
                source,
                on_stage_complete=on_stage_complete,
            )
            if len(output.bundle) > MAX_BUNDLE_BYTES:
                raise InvalidGenerationRequestError(
                    "pipeline bundle exceeds the runtime transfer limit"
                )
            store.persist_bundle(runtime_job.id, output.bundle)
            with store.lock:
                runtime_job.status = GenerationStatus.DONE
                runtime_job.stage = GenerationStage.PACKAGING
                runtime_job.completed_stages = tuple(GenerationStage)
                runtime_job.manifest = output.manifest
        except TargetAmbiguousError as exc:
            store.discard_bundle(runtime_job.id)
            with store.lock:
                runtime_job.status = GenerationStatus.ERROR
                runtime_job.stage = GenerationStage.ANALYSIS
                runtime_job.error = ErrorPayload(
                    code=exc.code,
                    message=str(exc),
                )
                runtime_job.ambiguity = exc.candidates
        except AICoreError as exc:
            store.discard_bundle(runtime_job.id)
            with store.lock:
                runtime_job.status = GenerationStatus.ERROR
                runtime_job.error = ErrorPayload(
                    code=exc.code,
                    message=str(exc),
                )
        except Exception:
            store.discard_bundle(runtime_job.id)
            with store.lock:
                runtime_job.status = GenerationStatus.ERROR
                runtime_job.error = ErrorPayload(
                    code=ErrorCode.INTERNAL_ERROR,
                    message="runtime pipeline failed unexpectedly",
                )
        finally:
            store.finish_active(runtime_job.id)

    async def run_in_thread(
        runtime_job: _RuntimeJob,
        envelope: RemoteJobRequest,
        source: bytes,
    ) -> None:
        await asyncio.to_thread(execute_job, runtime_job, envelope, source)

    @app.post(
        "/v1/jobs",
        response_model=JobCreated,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(authenticate)],
    )
    async def create_job(
        request: Annotated[str, Form(...)],
        image: Annotated[UploadFile, File(...)],
    ) -> JobCreated:
        ready, _ = runtime_ready()
        if not ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_error_detail(
                    ErrorCode.AI_SERVICE_UNAVAILABLE,
                    "runtime GPU is not ready",
                ),
            )
        if len(request.encode("utf-8")) > MAX_REQUEST_JSON_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=_error_detail(
                    ErrorCode.INVALID_GENERATION_REQUEST,
                    "generation request exceeds the runtime limit",
                ),
            )
        try:
            envelope = RemoteJobRequest.model_validate(_parse_strict_json(request))
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=_error_detail(
                    ErrorCode.INVALID_GENERATION_REQUEST,
                    "generation request violates the strict runtime contract",
                ),
            ) from exc
        image_content_type = image.content_type
        try:
            source = await image.read(settings.max_image_bytes + 1)
        finally:
            await image.close()
        if len(source) > settings.max_image_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=_error_detail(
                    ErrorCode.INVALID_GENERATION_REQUEST,
                    "source image exceeds the runtime upload limit",
                ),
            )
        if image_content_type != envelope.input.product.original_mime:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=_error_detail(
                    ErrorCode.INVALID_GENERATION_REQUEST,
                    "multipart image MIME differs from the immutable snapshot",
                ),
            )
        try:
            verify_source_request(
                envelope,
                source,
                max_source_pixels=settings.max_source_pixels,
            )
        except InvalidGenerationRequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=_error_detail(exc.code, str(exc)),
            ) from exc
        try:
            runtime_job, created = store.create(
                request_id=envelope.request_id,
                input_snapshot_hash=envelope.input_snapshot_hash,
            )
        except _RequestConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_error_detail(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "request ID conflicts with an existing runtime job",
                ),
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=_error_detail(
                    ErrorCode.AI_SERVICE_UNAVAILABLE,
                    "runtime already has an active job",
                ),
                headers={"Retry-After": "5"},
            ) from exc
        if created:
            task = asyncio.create_task(run_in_thread(runtime_job, envelope, source))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return JobCreated(
            id=runtime_job.id,
            runtime_id=settings.runtime_id,
            status=(
                "pending"
                if runtime_job.status is GenerationStatus.PENDING
                else "processing"
            ),
        )

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=JobResult,
        dependencies=[Depends(authenticate)],
    )
    def get_job(job_id: str) -> JobResult:
        runtime_job = store.get(job_id)
        if runtime_job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_error_detail(
                    ErrorCode.REMOTE_PROTOCOL_ERROR,
                    "runtime job was not found",
                ),
            )
        with store.lock:
            return runtime_job.response(settings.runtime_id)

    @app.get(
        "/v1/jobs/{job_id}/bundle",
        dependencies=[Depends(authenticate)],
    )
    def get_bundle(
        job_id: str,
        expected_runtime_id: Annotated[
            str | None, Header(alias="X-Expected-Runtime-ID")
        ] = None,
    ) -> Response:
        if expected_runtime_id != settings.runtime_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_error_detail(
                    ErrorCode.AI_RUNTIME_LOST,
                    "expected runtime identity does not match this session",
                ),
            )
        runtime_job = store.get(job_id)
        if runtime_job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_error_detail(
                    ErrorCode.REMOTE_PROTOCOL_ERROR,
                    "runtime job was not found",
                ),
            )
        with store.lock:
            if (
                runtime_job.status is not GenerationStatus.DONE
                or runtime_job.bundle_path is None
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_error_detail(
                        ErrorCode.REMOTE_PROTOCOL_ERROR,
                        "runtime bundle is not ready",
                    ),
                )
            bundle_path = runtime_job.bundle_path
        if not bundle_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_error_detail(
                    ErrorCode.REMOTE_PROTOCOL_ERROR,
                    "runtime bundle file is unavailable",
                ),
            )
        return FileResponse(
            path=bundle_path,
            media_type="application/zip",
            filename=f"{runtime_job.id}-campaign.zip",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app
