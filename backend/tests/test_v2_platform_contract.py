from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_core.schemas import (
    BudgetEnvelope,
    GenerationRequestV2,
    ProductLockPrompts,
    ProductLockRevisionCreate,
    ProductLockStatus,
    ProviderSelection,
    SceneSpec,
)
from app.database import Base
from app.models import (
    CampaignLanguage,
    GenerationLifecycleStatus,
    Product,
    User,
)
from app.services.generation_v2 import (
    create_v2_generation,
    transition_v2,
)
from app.services.product_locks import create_revision, transition_revision


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _product(db: Session):
    user = User(
        id=uuid.uuid4(), email="v2@example.com", hashed_password="test-password"
    )
    product = Product(
        id=uuid.uuid4(),
        user=user,
        name="Sérum",
        brand="Maison",
        category="Soin",
        original_storage_key=f"products/{user.id}/original.png",
        original_mime="image/png",
        original_sha256="a" * 64,
        original_width=640,
        original_height=800,
        original_exif_orientation=1,
    )
    db.add(product)
    db.flush()
    return user, product


def _lock(db: Session, user: User, product: Product):
    created = create_revision(
        db,
        product=product,
        owner_id=user.id,
        request=ProductLockRevisionCreate(
            source_sha256=product.original_sha256,
            target_box={"type": "box", "x": 0.1, "y": 0.2, "width": 0.5, "height": 0.6},
            prompts=ProductLockPrompts(
                audience="peau sensible",
                benefits=["Hydrate"],
                ingredients=["Aloe vera"],
                verified_claims=["Testé"],
                cta="Découvrir",
                creative_direction="Studio minéral",
                scene_prompt="Fond nacré",
            ),
            scene=SceneSpec(scene_prompt="Fond nacré", target_box={"type": "box", "x": 0.1, "y": 0.2, "width": 0.5, "height": 0.6}),
        ),
    )
    db.flush()
    transition_revision(db, created, ProductLockStatus.VALIDATED)
    db.flush()
    return created


def test_v2_request_is_frozen_and_provider_endpoint_fields_are_forbidden():
    request = GenerationRequestV2(
        product_lock_revision_id=uuid.uuid4(),
        language=CampaignLanguage.FR,
        provider={"provider": "closerouter", "profile": "openai", "model": "openai/gpt-image-2"},
        budget={"max_cost_micros": 1000, "max_attempts": 2},
        scene={"scene_prompt": "Fond nacré"},
    )
    with pytest.raises((TypeError, ValueError)):
        request.seed = 7
    with pytest.raises(ValueError):
        ProviderSelection(
            provider="https://evil.example", profile="campaign-v2"
        )


def test_accepted_v2_generation_persists_complete_snapshot_before_worker_readiness(db):
    user, product = _product(db)
    lock = _lock(db, user, product)
    request = GenerationRequestV2(
        product_lock_revision_id=lock.id,
        language=CampaignLanguage.EN,
        seed=987,
        audience="peau sensible",
        benefits=["Hydrate"],
        ingredients=["Aloe vera"],
        verified_claims=["Testé"],
        cta="Discover",
        creative_direction="Pearl studio",
        scene_prompt="A pearl-white studio",
        provider={"provider": "closerouter", "profile": "openai", "model": "openai/gpt-image-2"},
        variant_count=2,
        budget=BudgetEnvelope(max_cost_micros=1000, max_attempts=2),
    )
    generation = create_v2_generation(
        db,
        product=product,
        owner_id=user.id,
        lock=lock,
        body=request,
        idempotency_key="v2-contract-key-0001",
    )
    db.commit()

    assert generation.lifecycle_status == GenerationLifecycleStatus.ACCEPTED.value
    assert generation.provider_execution_plan["selection"]["model"] == "openai/gpt-image-2"
    snapshot = generation.input_snapshot["v2"]
    assert snapshot["generation"]["audience"] == "peau sensible"
    assert snapshot["generation"]["benefits"] == ["Hydrate"]
    assert snapshot["generation"]["ingredients"] == ["Aloe vera"]
    assert snapshot["generation"]["verified_claims"] == ["Testé"]
    assert snapshot["generation"]["cta"] == "Discover"
    assert snapshot["generation"]["creative_direction"] == "Pearl studio"
    assert snapshot["generation"]["scene_prompt"] == "A pearl-white studio"
    assert generation.product_lock_revision_id == lock.id
    assert generation.v2_variants and len(generation.v2_variants) == 2


def test_v2_lifecycle_is_provider_neutral_and_terminal_transitions_are_guarded(db):
    user, product = _product(db)
    lock = _lock(db, user, product)
    request = GenerationRequestV2(
        product_lock_revision_id=lock.id,
        language="fr",
        provider={"provider": "closerouter", "profile": "openai", "model": "openai/gpt-image-2"},
        budget={"max_cost_micros": 1000, "max_attempts": 2},
    )
    generation = create_v2_generation(
        db,
        product=product,
        owner_id=user.id,
        lock=lock,
        body=request,
        idempotency_key="v2-lifecycle-key-0001",
    )
    transition_v2(db, generation, GenerationLifecycleStatus.QUEUED)
    transition_v2(db, generation, GenerationLifecycleStatus.WAITING_FOR_PROVIDER)
    transition_v2(db, generation, GenerationLifecycleStatus.PROCESSING_LOCK)
    transition_v2(db, generation, GenerationLifecycleStatus.PLANNING_SCENE)
    transition_v2(db, generation, GenerationLifecycleStatus.GENERATING)
    transition_v2(db, generation, GenerationLifecycleStatus.COMPOSITING)
    transition_v2(db, generation, GenerationLifecycleStatus.QUALITY_REVIEW)
    transition_v2(db, generation, GenerationLifecycleStatus.READY)
    with pytest.raises(ValueError):
        transition_v2(db, generation, GenerationLifecycleStatus.FAILED)


def test_lock_refinement_creates_revision_and_inherits_immutable_prompt_snapshot(db):
    user, product = _product(db)
    parent = _lock(db, user, product)
    refined = create_revision(
        db,
        product=product,
        owner_id=user.id,
        parent=parent,
        request=ProductLockRevisionCreate(
            target_box={"type": "box", "x": 0.2, "y": 0.2, "width": 0.4, "height": 0.5}
        ),
    )
    assert refined.id != parent.id
    assert refined.parent_revision_id == parent.id
    assert refined.prompts == parent.prompts
    assert parent.status == ProductLockStatus.SUPERSEDED.value
