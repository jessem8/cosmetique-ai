"""Server-owned V2 provider profile and capability registry.

Only opaque ``provider:profile`` identifiers cross the browser boundary.  A
provider adapter can resolve the profile inside the worker, but neither its
host nor credentials are represented in an API request or response.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from ai_core.schemas import ProviderCapabilities, ProviderSelection

from app.core.config import settings


@dataclass(frozen=True)
class ProviderProfile:
    selection: ProviderSelection
    capabilities: ProviderCapabilities


def _configured_profiles() -> tuple[ProviderProfile, ...]:
    raw = [item.strip() for item in settings.V2_PROVIDER_ALLOWLIST.split(",") if item.strip()]
    profiles: list[ProviderProfile] = []
    for item in raw:
        parts = item.split(":")
        if len(parts) not in {2, 3}:
            continue
        provider, profile = parts[:2]
        model = parts[2] if len(parts) == 3 else None
        try:
            selection = ProviderSelection(provider=provider, profile=profile, model=model)
        except Exception:
            continue
        profiles.append(
            ProviderProfile(
                selection=selection,
                capabilities=ProviderCapabilities(
                    provider=provider,
                    profile=profile,
                    max_variants=settings.V2_MAX_VARIANTS,
                    max_budget_micros=settings.V2_MAX_BUDGET_MICROS,
                    estimated_cost_per_variant_micros=settings.V2_ESTIMATED_COST_PER_VARIANT_MICROS,
                    supports_async=True,
                    supports_cancellation=True,
                    supports_unknown_completion_reconciliation=True,
                    supports_product_lock=True,
                    supports_mask_preservation=True,
                    supports_scene_generation=True,
                    supports_custom_direction=True,
                    supports_qa_provenance=True,
                    pipeline_version="2.0.0",
                    description="Server-owned product-preserving Campaign Studio profile.",
                ),
            )
        )
    if profiles:
        return tuple(profiles)
    # Fail closed when a malformed environment variable is supplied.  The
    # local default keeps development/test imports usable and has no network
    # meaning by itself.
    selection = ProviderSelection(
        provider=settings.V2_DEFAULT_PROVIDER,
        profile=settings.V2_DEFAULT_PROVIDER_PROFILE,
    )
    return (
        ProviderProfile(
            selection=selection,
            capabilities=ProviderCapabilities(
                provider=selection.provider,
                profile=selection.profile,
                max_variants=settings.V2_MAX_VARIANTS,
                max_budget_micros=settings.V2_MAX_BUDGET_MICROS,
                estimated_cost_per_variant_micros=settings.V2_ESTIMATED_COST_PER_VARIANT_MICROS,
                supports_async=True,
                supports_cancellation=True,
                supports_unknown_completion_reconciliation=True,
                supports_product_lock=True,
                supports_mask_preservation=True,
                supports_scene_generation=True,
                supports_custom_direction=True,
                supports_qa_provenance=True,
                pipeline_version="2.0.0",
                description="Server-owned product-preserving Campaign Studio profile.",
            ),
        ),
    )


def provider_profiles() -> tuple[ProviderProfile, ...]:
    return _configured_profiles()


def provider_profile(selection: ProviderSelection) -> ProviderProfile | None:
    for profile in provider_profiles():
        if profile.selection == selection:
            return profile
        # A configured profile may omit a model; a request can select that
        # profile without inventing a model identifier.
        if (
            profile.selection.provider == selection.provider
            and profile.selection.profile == selection.profile
            and profile.selection.model is None
            and selection.model is None
        ):
            return profile
    return None


def require_provider_profile(selection: ProviderSelection) -> ProviderProfile:
    profile = provider_profile(selection)
    if profile is None:
        raise ValueError("provider profile is not allowlisted")
    if selection.model and profile.selection.model and selection.model != profile.selection.model:
        raise ValueError("provider model is not allowlisted")
    return profile


def safe_provider_host(value: str) -> str:
    """Validate a configured provider host without returning credentials.

    This helper is for adapters reading server configuration.  User input is
    never passed here; URL-like values are rejected by ``ProviderSelection``.
    """

    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("provider host must be an explicit HTTP origin")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("provider host has an invalid port") from exc
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("provider host must be an origin without credentials")
    allowed = {host.strip().lower() for host in settings.V2_PROVIDER_HOST_ALLOWLIST.split(",") if host.strip()}
    if allowed and parsed.hostname.lower() not in allowed:
        raise ValueError("provider host is not allowlisted")
    if parsed.scheme == "http" and settings.APP_ENV == "production":
        raise ValueError("provider host must use HTTPS in production")
    return parsed.geturl().rstrip("/")
