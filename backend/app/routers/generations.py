"""
Generations router: launch pipeline + status polling + regen endpoints.

Fixed:
  - regenerate_text uses proper FastAPI Depends (not None defaults)
  - Removed unused AI lib imports (breaks on Windows without GPU deps)
  - Added GET /products/{product_id}/generations for dashboard
  - Added GET /generations/{id}/download-zip for ZIP export
"""

import io
import uuid
import zipfile
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.dependencies import CurrentUser, DBSession
from app.models import Asset, Generation, GenerationStatus, Product
from app.schemas import AssetOut, GenerationCreate, GenerationOut
from app.services.pipeline import generate_marketing_text, run_pipeline

router = APIRouter(tags=["Generations"])


def _assert_owns_generation(generation: Optional[Generation], user_id: uuid.UUID) -> Generation:
    """Raise 404 or 403 if generation is invalid or not owned by user."""
    if not generation:
        raise HTTPException(status_code=404, detail="Génération introuvable.")
    if generation.product.user_id != user_id:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    return generation


# ══════════════════════════════════════════════════════════════════════════
#  Generations per product  (used by Dashboard)
# ══════════════════════════════════════════════════════════════════════════


@router.get(
    "/products/{product_id}/generations",
    response_model=list[GenerationOut],
    summary="Liste des générations d'un produit",
)
def list_product_generations(
    product_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    limit: int = Query(5, ge=1, le=20),
) -> list[Generation]:
    """Returns the N most recent generations for a product (newest first)."""
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    if product.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    return db.scalars(
        select(Generation)
        .where(Generation.product_id == product_id)
        .order_by(Generation.created_at.desc())
        .limit(limit)
    ).all()


# ══════════════════════════════════════════════════════════════════════════
#  Create generation
# ══════════════════════════════════════════════════════════════════════════


@router.post(
    "/generations/{product_id}",
    response_model=GenerationOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Lancer la génération IA pour un produit",
)
def create_generation(
    product_id: uuid.UUID,
    body: GenerationCreate,
    background_tasks: BackgroundTasks,
    db: DBSession,
    current_user: CurrentUser,
) -> Generation:
    """
    Start the AI pipeline as a background task.
    Returns immediately with generation_id and status=pending.
    Poll GET /generations/{id} to track progress.
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produit introuvable.")
    if product.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    # Auto-select template by category if not specified
    from app.services.pipeline import CATEGORY_TEMPLATE_MAP
    auto_template = CATEGORY_TEMPLATE_MAP.get(product.category or "", "classic")

    generation = Generation(
        product_id=product_id,
        status=GenerationStatus.PENDING,
        tone=body.tone,
        template=body.template or auto_template,
    )
    db.add(generation)
    db.commit()
    db.refresh(generation)

    background_tasks.add_task(run_pipeline, str(generation.id), db)
    return generation


# ══════════════════════════════════════════════════════════════════════════
#  Get / Poll generation
# ══════════════════════════════════════════════════════════════════════════


@router.get(
    "/generations/{generation_id}",
    response_model=GenerationOut,
    summary="Statut et résultat d'une génération",
)
def get_generation(
    generation_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> Generation:
    """Poll every 2-3 seconds until status is done or error."""
    gen = db.get(Generation, generation_id)
    return _assert_owns_generation(gen, current_user.id)


@router.get(
    "/generations/{generation_id}/assets",
    response_model=list[AssetOut],
    summary="Liste des 3 assets (Instagram, Facebook, LinkedIn)",
)
def get_assets(
    generation_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> list[Asset]:
    gen = db.get(Generation, generation_id)
    _assert_owns_generation(gen, current_user.id)
    return db.scalars(
        select(Asset).where(Asset.generation_id == generation_id)
    ).all()


# ══════════════════════════════════════════════════════════════════════════
#  Download ZIP of all 3 formats
# ══════════════════════════════════════════════════════════════════════════


@router.get(
    "/generations/{generation_id}/download-zip",
    summary="Télécharger les 3 formats en un seul ZIP",
    response_class=StreamingResponse,
)
def download_zip(
    generation_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    """
    Fetch all 3 asset images (Instagram, Facebook, LinkedIn) and return
    them as a ZIP archive for one-click download.
    """
    gen = db.get(Generation, generation_id)
    _assert_owns_generation(gen, current_user.id)

    if gen.status != GenerationStatus.DONE:
        raise HTTPException(
            status_code=400,
            detail="La génération n'est pas encore terminée.",
        )

    assets = db.scalars(
        select(Asset).where(Asset.generation_id == generation_id)
    ).all()

    if not assets:
        raise HTTPException(status_code=404, detail="Aucun asset disponible.")

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    product_name = gen.product.name.replace(" ", "_")[:40]

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        with httpx.Client(timeout=30) as http:
            for asset in assets:
                try:
                    if asset.url.startswith("/uploads/"):
                        local_path = Path("uploads") / Path(asset.url).name
                        image_bytes = local_path.read_bytes()
                    else:
                        img_resp = http.get(asset.url)
                        img_resp.raise_for_status()
                        image_bytes = img_resp.content
                    filename = f"{product_name}_{asset.format.value}_{asset.width}x{asset.height}.png"
                    zf.writestr(filename, image_bytes)
                except Exception:
                    pass  # Skip failed downloads silently

    zip_buffer.seek(0)
    gen_id_short = str(generation_id)[:8]
    zip_name = f"cosmetique_ai_{product_name}_{gen_id_short}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# ══════════════════════════════════════════════════════════════════════════
#  Regenerate text only
# ══════════════════════════════════════════════════════════════════════════


@router.post(
    "/generations/{generation_id}/regenerate-text",
    response_model=GenerationOut,
    summary="Regénérer uniquement le texte marketing",
)
def regenerate_text(
    generation_id: uuid.UUID,
    db: DBSession,
    current_user: CurrentUser,
    tone: Optional[str] = Query(default=None, max_length=50),
) -> Generation:
    """Re-run only the Qwen2.5 text generation step (fast, no GPU needed)."""
    gen = db.get(Generation, generation_id)
    _assert_owns_generation(gen, current_user.id)

    category = gen.product.category or "soin_visage"
    effective_tone = tone or gen.tone or "luxe"

    new_text = generate_marketing_text(gen.product.name, category, effective_tone)
    gen.marketing_text = new_text
    gen.tone = effective_tone
    db.commit()
    db.refresh(gen)
    return gen


# ══════════════════════════════════════════════════════════════════════════
#  Regenerate decor
# ══════════════════════════════════════════════════════════════════════════


@router.post(
    "/generations/{generation_id}/regenerate-decor",
    response_model=GenerationOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Regénérer le décor IA et recomposer l'affiche",
)
def regenerate_decor(
    generation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: DBSession,
    current_user: CurrentUser,
) -> Generation:
    """Re-run the full pipeline (decor + composition + text + export + upload)."""
    gen = db.get(Generation, generation_id)
    _assert_owns_generation(gen, current_user.id)

    gen.status = GenerationStatus.PENDING
    gen.error_message = None
    db.commit()

    background_tasks.add_task(run_pipeline, str(gen.id), db)
    db.refresh(gen)
    return gen
