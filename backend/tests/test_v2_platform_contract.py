from __future__ import annotations

from app.core.config import settings
from app.services.provider_registry import provider_profiles, require_provider_profile
from ai_core.schemas import ProviderSelection


def test_extraction_profile_is_the_only_supported_v2_profile():
    profiles = provider_profiles()

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.selection.provider == "product-extraction"
    assert profile.selection.profile == "native-sam2"
    assert profile.selection.model is None
    assert profile.capabilities.supports_product_lock is True
    assert profile.capabilities.supports_mask_preservation is True
    assert profile.capabilities.supports_scene_generation is False
    assert profile.capabilities.supports_custom_direction is False
    assert "Background generation is disabled" in profile.capabilities.description


def test_extraction_profile_matches_runtime_defaults():
    profile = provider_profiles()[0]

    assert settings.V2_DEFAULT_PROVIDER == profile.selection.provider
    assert settings.V2_DEFAULT_PROVIDER_PROFILE == profile.selection.profile
    assert settings.V2_PROVIDER_ALLOWLIST == "product-extraction:native-sam2"


def test_generation_profile_selection_is_resolved_without_external_provider():
    selection = ProviderSelection(
        provider="product-extraction",
        profile="native-sam2",
        model=None,
    )

    resolved = require_provider_profile(selection)

    assert resolved.selection == selection
    assert resolved.capabilities.supports_scene_generation is False
