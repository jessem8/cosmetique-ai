"""Authenticated, fail-closed client for the session-bound Colab service."""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from ai_core.artifacts import PIPELINE_VERSION
from ai_core.model_registry import pinned_model_refs
from ai_core.schemas import CandidateBox, ErrorCode, Manifest
from app.models import GenerationStage


MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_JSON_RESPONSE_BYTES = 256 * 1024
EXPECTED_MODEL_REFS = {
    name: f"{model.repo_id}@{model.revision}"
    for name, model in pinned_model_refs().items()
}
RemoteJobId = Annotated[
    str, StringConstraints(pattern=r"^job-[A-Za-z0-9_-]{16,128}$")
]
REMOTE_JOB_ID_ADAPTER = TypeAdapter(RemoteJobId)


class AIClientError(RuntimeError):
    error_code = "REMOTE_PROTOCOL_ERROR"


class AIServiceUnavailable(AIClientError):
    error_code = "AI_SERVICE_UNAVAILABLE"


class AIRuntimeLost(AIClientError):
    error_code = "AI_RUNTIME_LOST"


class AIRemoteAuthFailed(AIClientError):
    error_code = "REMOTE_AUTH_FAILED"


class AIProtocolError(AIClientError):
    error_code = "REMOTE_PROTOCOL_ERROR"


class RemoteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GPUHealth(RemoteModel):
    available: bool
    name: str | None
    vram_bytes: int = Field(ge=0)


class HealthContract(RemoteModel):
    ready: bool
    runtime_id: str = Field(min_length=1, max_length=200)
    gpu: GPUHealth
    models: dict[str, str]
    pipeline_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


class RemoteError(RemoteModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=200)


class RemoteHTTPError(RemoteModel):
    detail: RemoteError


class RemoteJobCreated(RemoteModel):
    id: RemoteJobId
    runtime_id: str = Field(min_length=1, max_length=200)
    status: Literal["pending", "processing"]


class RemoteJob(RemoteModel):
    id: RemoteJobId
    status: Literal["pending", "processing", "done", "error"]
    stage: GenerationStage | None = None
    completed_stages: list[GenerationStage] = Field(default_factory=list)
    runtime_id: str = Field(min_length=1, max_length=200)
    error: RemoteError | None = None
    ambiguity: tuple[CandidateBox, ...] | None = Field(
        default=None, min_length=2, max_length=20
    )
    manifest: Manifest | None = None

    @model_validator(mode="after")
    def require_status_consistency(self) -> "RemoteJob":
        stages = list(GenerationStage)
        expected_prefix = stages[: len(self.completed_stages)]
        if self.completed_stages != expected_prefix:
            raise ValueError("completed stages must be a unique ordered prefix")
        if self.status == "pending":
            if self.stage is not None or self.completed_stages:
                raise ValueError("pending jobs cannot expose stage progress")
        if self.status == "processing":
            if (
                len(self.completed_stages) >= len(stages)
                or self.stage is not stages[len(self.completed_stages)]
            ):
                raise ValueError(
                    "processing stage must immediately follow completed stages"
                )
        if self.status == "done":
            if (
                self.manifest is None
                or self.error is not None
                or self.ambiguity is not None
                or self.completed_stages != stages
                or self.stage is not GenerationStage.PACKAGING
            ):
                raise ValueError("done jobs require a manifest and no error")
        elif self.manifest is not None:
            raise ValueError("only done jobs may expose a manifest")
        if self.status == "error":
            if self.error is None:
                raise ValueError("error jobs require an error payload")
        elif self.error is not None or self.ambiguity is not None:
            raise ValueError("only error jobs may expose error details")
        if self.ambiguity is not None and (
            self.error is None or self.error.code is not ErrorCode.TARGET_AMBIGUOUS
        ):
            raise ValueError("candidate boxes require TARGET_AMBIGUOUS")
        if (
            self.error is not None
            and self.error.code is ErrorCode.TARGET_AMBIGUOUS
            and self.ambiguity is None
        ):
            raise ValueError("TARGET_AMBIGUOUS requires candidate boxes")
        return self


class AIClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
    ):
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("AI_SERVICE_URL invalide.")
        if not token:
            raise ValueError("AI_SERVICE_TOKEN manquant.")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                # Prefer uncompressed bodies so size limits match Content-Length
                # and rebuilt responses are not double-decoded.
                "Accept-Encoding": "identity",
            },
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AIClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            with self._client.stream(method, path, **kwargs) as response:
                self._check_status(response)
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if content_type != "application/json":
                    raise AIProtocolError("Type de réponse distante invalide.")
                try:
                    declared_size = int(response.headers.get("content-length", "0"))
                except ValueError as exc:
                    raise AIProtocolError("Taille de réponse distante invalide.") from exc
                if declared_size > MAX_JSON_RESPONSE_BYTES:
                    raise AIProtocolError("Réponse distante trop volumineuse.")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_JSON_RESPONSE_BYTES:
                        raise AIProtocolError("Réponse distante trop volumineuse.")
                    chunks.append(chunk)
                # iter_bytes() already decompresses; drop encoding headers so the
                # rebuilt Response is not decoded a second time (Cloudflare gzip).
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower()
                    not in {
                        "content-encoding",
                        "content-length",
                        "transfer-encoding",
                    }
                }
                return httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=b"".join(chunks),
                    request=response.request,
                )
        except AIClientError:
            raise
        except (httpx.TransportError, httpx.DecodingError) as exc:
            raise AIServiceUnavailable("Service distant indisponible.") from exc

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise AIRemoteAuthFailed("Authentification distante refusée.")
        if response.status_code == 429 or response.status_code >= 500:
            raise AIServiceUnavailable("Service distant indisponible.")
        if response.status_code >= 400:
            raise AIProtocolError("Requête distante rejetée.")

    @staticmethod
    def _parse(model, response: httpx.Response):
        try:
            return model.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise AIProtocolError("Réponse distante invalide.") from exc

    @staticmethod
    def _read_bounded_json_error(response: httpx.Response) -> RemoteHTTPError:
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type != "application/json":
            raise AIProtocolError("Type de réponse distante invalide.")
        try:
            declared_size = int(response.headers.get("content-length", "0"))
        except ValueError as exc:
            raise AIProtocolError("Taille de réponse distante invalide.") from exc
        if declared_size < 0 or declared_size > MAX_JSON_RESPONSE_BYTES:
            raise AIProtocolError("Réponse distante trop volumineuse.")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > MAX_JSON_RESPONSE_BYTES:
                raise AIProtocolError("Réponse distante trop volumineuse.")
            chunks.append(chunk)
        try:
            return RemoteHTTPError.model_validate_json(b"".join(chunks))
        except ValidationError as exc:
            raise AIProtocolError("Réponse distante invalide.") from exc

    @staticmethod
    def _validate_job_id(job_id: str) -> str:
        try:
            return REMOTE_JOB_ID_ADAPTER.validate_python(job_id)
        except ValidationError as exc:
            raise AIProtocolError("Identifiant de job distant invalide.") from exc

    def health(self) -> HealthContract:
        health = self._parse(HealthContract, self._request("GET", "/v1/health"))
        model_refs_valid = health.models == EXPECTED_MODEL_REFS
        if (
            not health.ready
            or not health.gpu.available
            or not model_refs_valid
            or health.pipeline_version != PIPELINE_VERSION
        ):
            raise AIServiceUnavailable("Runtime GPU non prêt.")
        return health

    def create_job(
        self,
        *,
        image_bytes: bytes,
        image_mime: str,
        request: dict[str, Any],
        expected_runtime_id: str,
    ) -> RemoteJobCreated:
        response = self._request(
            "POST",
            "/v1/jobs",
            files={"image": ("product", image_bytes, image_mime)},
            data={
                "request": json.dumps(
                    request,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            },
        )
        created = self._parse(RemoteJobCreated, response)
        if created.runtime_id != expected_runtime_id:
            raise AIRuntimeLost("Le runtime a changé avant la création du job.")
        return created

    def get_job(self, job_id: str, *, expected_runtime_id: str) -> RemoteJob:
        safe_job_id = self._validate_job_id(job_id)
        job = self._parse(
            RemoteJob, self._request("GET", f"/v1/jobs/{safe_job_id}")
        )
        if job.id != safe_job_id:
            raise AIProtocolError("Le job distant ne correspond pas à la requête.")
        if job.runtime_id != expected_runtime_id:
            raise AIRuntimeLost("Le runtime a changé pendant le job.")
        return job

    def download_bundle(self, job_id: str, *, expected_runtime_id: str) -> bytes:
        safe_job_id = self._validate_job_id(job_id)
        try:
            with self._client.stream(
                "GET",
                f"/v1/jobs/{safe_job_id}/bundle",
                headers={
                    "Accept": "application/zip",
                    "X-Expected-Runtime-ID": expected_runtime_id,
                },
            ) as response:
                if response.status_code == 409:
                    remote_error = self._read_bounded_json_error(response)
                    if remote_error.detail.code is ErrorCode.AI_RUNTIME_LOST:
                        raise AIRuntimeLost(
                            "Le runtime a changé avant le téléchargement du bundle."
                        )
                    raise AIProtocolError("Requête distante rejetée.")
                self._check_status(response)
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if content_type not in {"application/zip", "application/octet-stream"}:
                    raise AIProtocolError("Type de bundle distant invalide.")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BUNDLE_BYTES:
                        raise AIProtocolError("Bundle distant trop volumineux.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.TransportError as exc:
            raise AIServiceUnavailable("Service distant indisponible.") from exc
