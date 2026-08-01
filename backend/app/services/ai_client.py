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
    # Colab may add informational model fields without breaking the stable contract.\n    model_config = ConfigDict(extra="ignore")


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
            raise AIServiceUnavailable("Service Colab indisponible.")
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
        if not payload.ready:
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
