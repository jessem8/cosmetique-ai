"""Validated application configuration.

Credentials are read only from the process environment (or an uncommitted
``.env`` file during local development).  They are never sent to the browser or
the Colab request body.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_ENV: Literal["development", "production", "test"] = "development"
    DATABASE_URL: str

    SECRET_KEY: str = Field(min_length=32)
    ALGORITHM: Literal["HS256"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=5, le=1440)

    CORS_ORIGINS: str = "http://localhost"

    MAX_UPLOAD_SIZE_MB: int = Field(default=10, ge=1, le=50)
    MAX_IMAGE_PIXELS: int = Field(default=40_000_000, ge=1_000_000, le=100_000_000)
    ALLOWED_MIME_TYPES: str = "image/jpeg,image/png,image/webp"
    ARTIFACT_ROOT: Path = Path("/app/artifacts")

    COLAB_AI_URL: str = ""
    AI_SERVICE_TOKEN: str = ""
    AI_CONNECT_TIMEOUT_SECONDS: float = Field(default=5.0, ge=0.5, le=30)
    AI_READ_TIMEOUT_SECONDS: float = Field(default=30.0, ge=1, le=120)
    AI_HEALTH_TIMEOUT_SECONDS: float = Field(default=5.0, ge=0.5, le=15)

    WORKER_ID: str = "worker-1"
    WORKER_POLL_SECONDS: float = Field(default=2.0, ge=0.2, le=30)
    WORKER_REMOTE_POLL_SECONDS: float = Field(default=2.5, ge=0.5, le=30)
    WORKER_LEASE_SECONDS: int = Field(default=90, ge=30, le=900)
    WORKER_RETRY_DELAY_SECONDS: int = Field(default=10, ge=1, le=300)
    WORKER_JOB_DEADLINE_SECONDS: int = Field(default=1800, ge=60, le=7200)

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins or len(origins) != len(set(origins)):
            raise ValueError("CORS_ORIGINS must contain unique explicit origins")
        for origin in origins:
            if "*" in origin:
                raise ValueError("wildcard CORS origins are forbidden")
            parsed = urlsplit(origin)
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("invalid CORS origin port") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS_ORIGINS must contain exact HTTP origins")
        return ",".join(origins)

    @field_validator("SECRET_KEY")
    @classmethod
    def reject_placeholder_secret(cls, value: str) -> str:
        lowered = value.lower()
        if "changeme" in lowered or "replace_with" in lowered:
            raise ValueError("SECRET_KEY must be a generated secret")
        return value

    @model_validator(mode="after")
    def validate_colab_service(self) -> "Settings":
        if not self.COLAB_AI_URL:
            if self.APP_ENV == "production":
                raise ValueError("COLAB_AI_URL is required in production")
            return self
        parsed = urlsplit(self.COLAB_AI_URL)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("COLAB_AI_URL has an invalid port") from exc
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("COLAB_AI_URL must be an origin URL")
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "https":
            pass
        elif (
            parsed.scheme == "http"
            and self.APP_ENV != "production"
            and parsed.hostname in local_hosts
        ):
            pass
        else:
            raise ValueError("COLAB_AI_URL must use HTTPS")
        if len(self.AI_SERVICE_TOKEN) < 32:
            raise ValueError("AI_SERVICE_TOKEN must contain at least 32 characters")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def allowed_mime_list(self) -> list[str]:
        return [mime.strip() for mime in self.ALLOWED_MIME_TYPES.split(",") if mime.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
