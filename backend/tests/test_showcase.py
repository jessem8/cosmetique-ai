from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import GenerationLifecycleStatus
from app.routers import showcase


def _generation(*, status="ready", passed=True, checksum="a" * 64):
    return SimpleNamespace(
        id="generation-public",
        lifecycle_status=GenerationLifecycleStatus.READY.value,
        artifact_manifest={
            "stages": {
                "final_candidate": {
                    "status": status,
                    "storage_key": "generations/private/final.png",
                    "sha256": checksum,
                    "mime": "image/png",
                    "qa": {"passed": passed},
                }
            }
        },
        product=SimpleNamespace(name="Public product"),
    )


def test_showcase_returns_only_latest_accepted_final_candidate(monkeypatch):
    monkeypatch.setattr(
        showcase,
        "_ready_generations",
        lambda _db: [_generation(status="needs_review"), _generation()],
    )

    result = showcase.get_showcase(object())

    assert result.final_candidate.filename == f"showcase-{'a' * 64}.png"
    assert result.final_candidate.image_url.endswith(result.final_candidate.filename)
    assert "storage_key" not in result.final_candidate.model_dump()
    assert "private" not in result.final_candidate.image_url


@pytest.mark.parametrize(
    "generation",
    [
        _generation(status="failed"),
        _generation(passed=False),
        SimpleNamespace(lifecycle_status=GenerationLifecycleStatus.READY.value, artifact_manifest={}),
    ],
)
def test_showcase_ignores_unaccepted_or_incomplete_candidates(monkeypatch, generation):
    monkeypatch.setattr(showcase, "_ready_generations", lambda _db: [generation])

    with pytest.raises(HTTPException) as error:
        showcase.get_showcase(object())

    assert error.value.status_code == 404


def test_showcase_artifact_uses_checksum_allowlist_and_never_accepts_arbitrary_basename(monkeypatch):
    generation = _generation()
    monkeypatch.setattr(showcase, "_find_ready_generation", lambda _db, _checksum: (generation, {
        "storage_key": "generations/private/final.png",
        "mime": "image/png",
        "checksum": "a" * 64,
    }))
    monkeypatch.setattr(showcase.storage, "path_for", lambda _key: Path(__file__))

    response = showcase.get_showcase_artifact(f"showcase-{'a' * 64}.png", object())
    assert Path(response.path) == Path(__file__)

    with pytest.raises(HTTPException) as error:
        showcase.get_showcase_artifact("source.png", object())
    assert error.value.status_code == 404
