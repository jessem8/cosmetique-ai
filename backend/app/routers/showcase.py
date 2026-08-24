"""Public, privacy-safe showcase of the latest accepted final image."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dependencies import DBSession
from app.models import Generation, GenerationLifecycleStatus
from app.schemas import ShowcaseFinalCandidateOut, ShowcaseOut
from app.services.storage import StorageError, storage

router = APIRouter(prefix="/showcase", tags=["Public showcase"])

_CHECKSUM_FILENAME = re.compile(r"^showcase-([0-9a-f]{64})[.]png$")


def _accepted_stage(generation: Generation) -> dict[str, Any] | None:
    if generation.lifecycle_status != GenerationLifecycleStatus.READY.value:
        return None
    manifest = generation.artifact_manifest
    if not isinstance(manifest, dict):
        return None
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return None
    stage = stages.get("final_candidate")
    if not isinstance(stage, dict):
        return None
    if stage.get("status") != "ready":
        return None
    qa = stage.get("qa")
    if not isinstance(qa, dict) or qa.get("passed") is not True:
        return None
    checksum = str(stage.get("sha256") or stage.get("checksum") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        return None
    storage_key = stage.get("storage_key")
    if not isinstance(storage_key, str) or not storage_key:
        return None
    return {
        "checksum": checksum,
        "storage_key": storage_key,
        "mime": str(stage.get("mime") or "image/png"),
    }


def _ready_generations(db: DBSession) -> list[Generation]:
    return list(
        db.scalars(
            select(Generation)
            .options(selectinload(Generation.product))
            .where(Generation.lifecycle_status == GenerationLifecycleStatus.READY.value)
            .order_by(Generation.completed_at.desc(), Generation.created_at.desc())
            .limit(100)
        ).all()
    )


def _candidate_for(generation: Generation) -> ShowcaseFinalCandidateOut | None:
    stage = _accepted_stage(generation)
    if stage is None:
        return None
    filename = f"showcase-{stage['checksum']}.png"
    product_name = getattr(getattr(generation, "product", None), "name", None)
    return ShowcaseFinalCandidateOut(
        filename=filename,
        artifact_name=filename,
        image_url=f"/api/v1/showcase/artifacts/{filename}",
        alt="Rendu produit accepté et contrôlé",
        product_name=product_name if isinstance(product_name, str) else None,
    )


def _find_ready_generation(db: DBSession, checksum: str) -> tuple[Generation, dict[str, Any]] | None:
    for generation in _ready_generations(db):
        stage = _accepted_stage(generation)
        if stage is not None and stage["checksum"] == checksum:
            return generation, stage
    return None


@router.get("", response_model=ShowcaseOut)
def get_showcase(db: DBSession) -> ShowcaseOut:
    for generation in _ready_generations(db):
        candidate = _candidate_for(generation)
        if candidate is not None:
            return ShowcaseOut(final_candidate=candidate)
    raise HTTPException(status_code=404, detail="No accepted showcase artifact is available.")


@router.get("/artifacts/{filename}", response_class=FileResponse)
def get_showcase_artifact(filename: str, db: DBSession) -> FileResponse:
    match = _CHECKSUM_FILENAME.fullmatch(filename)
    if match is None:
        raise HTTPException(status_code=404, detail="Showcase artifact not found.")
    found = _find_ready_generation(db, match.group(1))
    if found is None:
        raise HTTPException(status_code=404, detail="Showcase artifact not found.")
    _generation, stage = found
    try:
        path = storage.path_for(stage["storage_key"])
        if not path.is_file() or path.is_symlink():
            raise StorageError("missing showcase artifact")
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="Showcase artifact not found.") from exc
    response = FileResponse(
        path,
        media_type=stage["mime"],
        filename=filename,
        content_disposition_type="inline",
    )
    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


__all__ = ["router", "_accepted_stage", "_candidate_for", "get_showcase", "get_showcase_artifact"]
