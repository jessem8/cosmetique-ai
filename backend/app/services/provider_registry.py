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
    # The supported V2 workflow has one server-owned local profile. Ignore
    # stale external-provider environment values so they cannot re-enter the
    # browser contract or worker execution path.
    selection = ProviderSelection(
        provider="product-extraction",
        profile="native-sam2",
        model=None,
    )
    return (
        ProviderProfile(
            selection=selection,
            capabilities=ProviderCapabilities(
                provider="product-extraction",
                profile="native-sam2",
                max_variants=1,
                max_budget_micros=0,
                estimated_cost_per_variant_micros=0,
                supports_async=True,
                supports_cancellation=True,
                supports_unknown_completion_reconciliation=True,
                supports_product_lock=True,
                supports_mask_preservation=True,
                supports_scene_generation=False,
                supports_custom_direction=False,
                supports_qa_provenance=False,
                pipeline_version="2.1.0",
                description="Native SAM2 product extraction with a canonical mask and transparent cutout. Background generation is disabled.",
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
