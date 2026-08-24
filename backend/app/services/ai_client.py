"""Small fail-closed client for the Colab-first AI service."""
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_JSON_RESPONSE_BYTES = 256 * 1024


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
    # Colab may add informational model fields without breaking the stable contract.
    model_config = ConfigDict(extra="ignore")


class HealthContract(RemoteModel):
    status: str = Field(min_length=1, max_length=32)
    ready: bool
    runtime_id: str = Field(min_length=1, max_length=200)
    pipeline_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    gpu: bool | dict[str, Any]


class AIClient:
    """Calls only the three public Colab endpoints; Docker never loads AI models."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        timeout_seconds: float = 600,
        transport: httpx.BaseTransport | None = None,
    ):
        value = str(base_url or "").strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("COLAB_AI_URL invalide.")
        self.base_url = value
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=value,
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AIClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise AIRemoteAuthFailed("Authentification Colab refusée.")
        if response.status_code == 429 or response.status_code >= 500:
            detail = response.text[:300].strip()
            message = "Service Colab indisponible."
            if detail:
                message += f" {detail}"
            raise AIServiceUnavailable(message)
        if response.status_code >= 400:
            detail = response.text[:300].strip()
            raise AIProtocolError(
                "Requête Colab rejetée." + (f" {detail}" if detail else "")
            )

    def health(self) -> HealthContract:
        try:
            response = self._client.get("/health")
            self._raise_for_status(response)
            if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise AIProtocolError("Réponse health Colab invalide.")
            payload = HealthContract.model_validate(response.json())
        except AIClientError:
            raise
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise AIServiceUnavailable("Service Colab indisponible ou invalide.") from exc
        # The campaign endpoint is now fully deterministic after isolation:
        # it does not depend on the optional Ollama copy service.  Older
        # Colab health payloads still include Ollama in ``ready``; accept a
        # reachable GPU runtime so that stale optional-model state cannot
        # block a safe campaign render.
        if not payload.ready and not bool(payload.gpu):
            raise AIServiceUnavailable("Le runtime GPU Colab n'est pas prêt.")
        return payload

    def generate_campaign(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        fields: dict[str, str],
    ) -> bytes:
        try:
            response = self._client.post(
                "/generate-campaign",
                files={
                    "image": (
                        filename,
                        image_bytes,
                        "application/octet-stream",
                    )
                },
                data=fields,
            )
            self._raise_for_status(response)
        except AIClientError:
            raise
        except httpx.HTTPError as exc:
            raise AIServiceUnavailable("Service Colab indisponible.") from exc
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type not in {"application/zip", "application/octet-stream"}:
            raise AIProtocolError("Le service Colab n'a pas renvoyé un ZIP.")
        if len(response.content) > MAX_BUNDLE_BYTES:
            raise AIProtocolError("Le ZIP Colab dépasse la limite autorisée.")
        if not response.content:
            raise AIProtocolError("Le ZIP Colab est vide.")
        return response.content

    def generate_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post("/generate-text", json=payload)
            self._raise_for_status(response)
            if len(response.content) > MAX_JSON_RESPONSE_BYTES:
                raise AIProtocolError("Réponse texte Colab trop volumineuse.")
            value = response.json()
        except AIClientError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise AIServiceUnavailable("Service Colab indisponible ou invalide.") from exc
        if not isinstance(value, dict):
            raise AIProtocolError("La réponse texte Colab doit être un objet JSON.")
        return value


# ---------------------------------------------------------------------------
# Private Docker V2 runtime boundary.  This is deliberately separate from the
# legacy Colab client above: a missing runtime is an explicit error and never
# silently falls back to Colab, a synthetic provider, or a local placeholder.

import base64
import time
from urllib.parse import quote, urlsplit


MAX_RUNTIME_ARTIFACT_BYTES = 80 * 1024 * 1024


class RuntimeHealthContract(RemoteModel):
    status: str = Field(min_length=1, max_length=64)
    ready: bool = False
    runtime_id: str = Field(default="private-ai-runtime", min_length=1, max_length=200)
    pipeline_version: str = Field(default="2.1.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    gpu: bool | dict[str, Any] = Field(default_factory=lambda: {"available": False})
    phases: dict[str, Any] = Field(default_factory=dict)
    container_up: bool = True
    cuda_ready: bool = False
    models_loading: bool = False
    inference_ready: bool = False
    engine_mode: str = "unknown"


class AIRuntimeClient:
    """Authenticated client for the private ai-runtime Docker service.

    Responses may contain internal artifact URLs because this class runs only
    inside the backend worker. URL values are consumed immediately and are
    never copied to browser-facing schemas; public projections use backend
    artifact routes and checksums only.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 600.0,
        health_timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ):
        value = str(base_url or "").strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("AI_SERVICE_URL invalide.")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("AI_SERVICE_URL doit être une origine sans identifiants.")
        if not str(token or "").strip():
            raise ValueError("AI_SERVICE_TOKEN est requis pour le runtime privé.")
        self.base_url = value
        self._host = parsed.hostname.casefold().rstrip(".")
        self._client = httpx.Client(
            base_url=value,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "X-Client": "campaign-studio-backend",
            },
            timeout=httpx.Timeout(
                read_timeout_seconds,
                connect=connect_timeout_seconds,
            ),
            follow_redirects=False,
            transport=transport,
        )
        self._health_timeout = health_timeout_seconds

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AIRuntimeClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise AIRemoteAuthFailed("Authentification du runtime privé refusée.")
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise AIServiceUnavailable("Runtime GPU privé indisponible.")
        if response.status_code >= 400:
            raise AIProtocolError("Requête runtime rejetée.")

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        if len(response.content) > MAX_JSON_RESPONSE_BYTES:
            raise AIProtocolError("Réponse runtime trop volumineuse.")
        try:
            value = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise AIProtocolError("Réponse JSON runtime invalide.") from exc
        if not isinstance(value, dict):
            raise AIProtocolError("Réponse runtime inattendue.")
        return value

    def health(self) -> RuntimeHealthContract:
        try:
            response = self._client.get(
                "/v2/health",
                timeout=self._health_timeout,
            )
            self._raise_for_status(response)
            payload = self._json(response)
            payload.setdefault("ready", bool(payload.get("inference_ready", False)))
            payload.setdefault("runtime_id", "private-ai-runtime")
            payload.setdefault("pipeline_version", "2.1.0")
            payload.setdefault(
                "gpu",
                {"available": bool(payload.get("cuda_ready", False))},
            )
            payload.setdefault(
                "phases",
                {
                    "container": bool(payload.get("container_up", True)),
                    "cuda": bool(payload.get("cuda_ready", False)),
                    "models": not bool(payload.get("models_loading", False)),
                    "inference": bool(payload.get("inference_ready", False)),
                },
            )
            return RuntimeHealthContract.model_validate(payload)
        except AIClientError:
            raise
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise AIServiceUnavailable("Runtime GPU privé indisponible ou invalide.") from exc

    def _artifact_url(self, lock_id: str, artifact: str) -> str:
        allowed = {"canonical.png", "mask.png", "cutout.png"}
        if artifact not in allowed:
            raise AIProtocolError("Artifact runtime non autorisé.")
        return f"/v2/product-locks/{quote(str(lock_id), safe='')}/artifacts/{quote(artifact, safe='')}"

    def get_product_lock_artifact(self, lock_id: str, artifact: str) -> bytes:
        try:
            response = self._client.get(self._artifact_url(lock_id, artifact))
            self._raise_for_status(response)
        except AIClientError:
            raise
        except httpx.HTTPError as exc:
            raise AIRuntimeLost("Artifact runtime indisponible.") from exc
        content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
        if content_type != "image/png" or not response.content:
            raise AIProtocolError("Artifact runtime invalide.")
        if len(response.content) > MAX_RUNTIME_ARTIFACT_BYTES:
            raise AIProtocolError("Artifact runtime trop volumineux.")
        return bytes(response.content)

    def create_product_lock(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        source_sha256: str,
        lock_id: str,
        revision: int,
        target_box: dict[str, Any] | None = None,
        refinement: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, str] = {
            "source_sha256": str(source_sha256),
            "lock_id": str(lock_id),
            "revision": str(int(revision)),
        }
        if target_box is not None:
            fields["target_box"] = json.dumps(target_box, separators=(",", ":"))
        if refinement is not None:
            fields["refinement"] = json.dumps(refinement, separators=(",", ":"))
        try:
            response = self._client.post(
                "/v2/product-locks",
                files={"image": (filename or "product.png", image_bytes, "application/octet-stream")},
                data=fields,
            )
            self._raise_for_status(response)
            return self._json(response)
        except AIClientError:
            raise
        except httpx.HTTPError as exc:
            raise AIServiceUnavailable("Création du Product Lock impossible.") from exc

    @staticmethod
    def _decode_image(value: Any) -> bytes | None:
        if not isinstance(value, str) or not value:
            return None
        if value.startswith("data:") and "," in value:
            value = value.split(",", 1)[1]
        try:
            result = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise AIProtocolError("Image runtime encodée invalide.")
        if not result or len(result) > MAX_RUNTIME_ARTIFACT_BYTES:
            raise AIProtocolError("Image runtime invalide ou trop volumineuse.")
        return result

    def _download_internal_url(self, value: str) -> bytes:
        parsed = urlsplit(value)
        if parsed.hostname and parsed.hostname.casefold().rstrip(".") != self._host:
            raise AIProtocolError("URL d'artifact runtime hors service privé.")
        path = parsed.path
        if not path.startswith("/v2/"):
            raise AIProtocolError("URL d'artifact runtime invalide.")
        try:
            response = self._client.get(path)
            self._raise_for_status(response)
        except AIClientError:
            raise
        except httpx.HTTPError as exc:
            raise AIRuntimeLost("Artifact runtime indisponible.") from exc
        if not response.content or len(response.content) > MAX_RUNTIME_ARTIFACT_BYTES:
            raise AIProtocolError("Artifact runtime invalide.")
        return bytes(response.content)

    def _stage_payload(self, payload: dict[str, Any], stage: str) -> dict[str, Any]:
        value: Any = payload.get(stage)
        if value is None:
            value = payload
        if isinstance(value, list):
            value = value[0] if value else {}
        if not isinstance(value, dict):
            raise AIProtocolError(f"Réponse runtime {stage} invalide.")
        artifact = value.get("artifact") if isinstance(value.get("artifact"), dict) else value
        image = None
        for key in ("image_b64", "b64_json", "base64", "data_url"):
            image = self._decode_image(artifact.get(key))
            if image is not None:
                break
        if image is None and isinstance(artifact.get("image"), str):
            image = self._decode_image(artifact.get("image"))
        if image is None:
            url = artifact.get("artifact_url") or artifact.get("url")
            if isinstance(url, str) and url:
                image = self._download_internal_url(url)
        if image is None:
            artifacts = payload.get("artifacts")
            if isinstance(artifacts, list):
                for candidate in artifacts:
                    if not isinstance(candidate, dict):
                        continue
                    candidate_stage = str(candidate.get("stage") or candidate.get("name") or "").casefold()
                    if stage.casefold() not in candidate_stage:
                        continue
                    image = self._decode_image(candidate.get("image_b64") or candidate.get("b64_json") or candidate.get("base64"))
                    if image is None and isinstance(candidate.get("artifact_url"), str):
                        image = self._download_internal_url(candidate["artifact_url"])
                    if image is not None:
                        artifact = candidate
                        break
        if image is None and stage == "baseline":
            generation_id = payload.get("id") or payload.get("generation_id")
            if isinstance(generation_id, str) and generation_id:
                try:
                    response = self._client.get(
                        f"/v2/generations/{quote(generation_id, safe='')}/variants/0/image"
                    )
                    self._raise_for_status(response)
                    if response.content and len(response.content) <= MAX_RUNTIME_ARTIFACT_BYTES:
                        image = bytes(response.content)
                    variants = payload.get("variants")
                    if isinstance(variants, list) and variants and isinstance(variants[0], dict):
                        artifact = {**variants[0], **value}
                except AIClientError:
                    raise
                except httpx.HTTPError as exc:
                    raise AIRuntimeLost("Artifact baseline runtime indisponible.") from exc
        if image is None:
            raise AIProtocolError(f"Réponse runtime sans artifact {stage}.")
        import hashlib as _hashlib
        checksum = _hashlib.sha256(image).hexdigest()
        declared = str(artifact.get("sha256") or artifact.get("checksum") or "").lower()
        if declared and declared != checksum:
            raise AIProtocolError(f"Checksum artifact runtime {stage} invalide.")
        declared_bytes = artifact.get("bytes")
        if declared_bytes is not None and int(declared_bytes) != len(image):
            raise AIProtocolError(f"Taille artifact runtime {stage} invalide.")
        from PIL import Image
        import io
        try:
            decoded = Image.open(io.BytesIO(image))
            decoded.load()
        except Exception as exc:
            raise AIProtocolError(f"Image artifact runtime {stage} invalide.") from exc
        declared_width = artifact.get("width")
        declared_height = artifact.get("height")
        if declared_width is not None and int(declared_width) != decoded.width:
            raise AIProtocolError(f"Largeur artifact runtime {stage} invalide.")
        if declared_height is not None and int(declared_height) != decoded.height:
            raise AIProtocolError(f"Hauteur artifact runtime {stage} invalide.")
        return {
            "stage": stage,
            "status": str(value.get("status") or payload.get("status") or "ready"),
            "image_bytes": image,
            "sha256": checksum,
            "mime": str(artifact.get("mime") or "image/png"),
            "job_id": payload.get("job_id") or value.get("job_id"),
            "provider_request_id": payload.get("provider_request_id") or value.get("provider_request_id"),
            "model": value.get("model") or artifact.get("model") or payload.get("model"),
            "model_revision": value.get("model_revision") or artifact.get("model_revision") or payload.get("model_revision"),
            "seed": value.get("seed") if value.get("seed") is not None else artifact.get("seed") if artifact.get("seed") is not None else payload.get("seed"),
            "prompt_sha256": value.get("prompt_sha256") or artifact.get("prompt_sha256") or payload.get("prompt_sha256"),
            "metadata": value.get("metadata") or artifact.get("metadata") or payload.get("metadata") or {},
            "qa": value.get("qa") or artifact.get("qa") or payload.get("qa"),
            "artifact_key": value.get("artifact_key") or artifact.get("artifact_key"),
            "width": decoded.width,
            "height": decoded.height,
            "bytes": len(image),
        }

    def _poll_job(self, job_id: str, *, deadline_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, deadline_seconds)
        while True:
            try:
                response = self._client.get(f"/v2/jobs/{quote(str(job_id), safe='')}")
                self._raise_for_status(response)
                payload = self._json(response)
            except AIClientError:
                raise
            except httpx.HTTPError as exc:
                raise AIRuntimeLost("Le job du runtime GPU a disparu.") from exc
            status = str(payload.get("status") or payload.get("lifecycle_status") or "").casefold()
            if status in {"ready", "succeeded", "completed", "failed", "needs_review", "unknown", "cancelled"}:
                return payload
            if time.monotonic() >= deadline:
                raise AIRuntimeLost("Le job du runtime GPU a dépassé son délai.")
            time.sleep(0.2)

    def generate_baseline(
        self,
        *,
        generation_id: str,
        lock_id: str,
        category: str,
        scene_prompt: str,
        negative_prompt: str | None,
        seed: int,
        variant_count: int,
        target_box: dict[str, Any] | None,
        prompt_sha256: str,
        request_hash: str,
        deadline_seconds: float = 1_800,
    ) -> dict[str, Any]:
        if not isinstance(scene_prompt, str) or len(scene_prompt.strip()) < 12:
            raise AIProtocolError("Background prompt runtime requis.")
        payload = {
            "generation_id": str(generation_id),
            "lock_id": str(lock_id),
            "category": str(category),
            "background_prompt": scene_prompt.strip(),
            "scene_prompt": scene_prompt.strip(),
            "negative_prompt": negative_prompt,
            "seed": int(seed),
            "variant_count": int(variant_count),
            "target_box": target_box,
            "prompt_sha256": str(prompt_sha256),
            "request_hash": str(request_hash),
            "quality": "high",
            "preserve_product_pixels": True,
        }
        try:
            response = self._client.post("/v2/generations", json=payload)
            self._raise_for_status(response)
            result = self._json(response)
            job_id = result.get("job_id") or result.get("id")
            status = str(result.get("status") or result.get("lifecycle_status") or "").casefold()
            if response.status_code == 202 or (
                job_id and status not in {"ready", "succeeded", "completed", "failed", "needs_review", "unknown"}
            ):
                if not job_id:
                    raise AIProtocolError("Runtime accepted a baseline without a job id.")
                result = self._poll_job(str(job_id), deadline_seconds=deadline_seconds)
            return self._stage_payload(result, "baseline")
        except AIClientError:
            raise
        except httpx.HTTPError as exc:
            raise AIServiceUnavailable("Génération de baseline indisponible.") from exc


__all__ = [
    "AIClient",
    "AIClientError",
    "AIProtocolError",
    "AIRemoteAuthFailed",
    "AIRuntimeClient",
    "AIRuntimeLost",
    "AIServiceUnavailable",
    "HealthContract",
    "RuntimeHealthContract",
]
