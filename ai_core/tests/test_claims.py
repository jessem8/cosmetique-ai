from __future__ import annotations

import pytest

from ai_core.claims import (
    EvidenceLedger,
    assemble_copy_payload,
    validate_copy_payload,
)
from ai_core.errors import ClaimSafetyError
from ai_core.schemas import (
    CampaignLanguage,
    ClaimUsage,
    CopyPlan,
    CopyPayload,
    GenerationRequest,
    PlatformCopySelection,
    PlatformCopy,
)


def make_ledger() -> EvidenceLedger:
    request = GenerationRequest(
        language=CampaignLanguage.FR,
        seed=42,
        benefits=["Hydrate la peau"],
        ingredients=["Aloe vera"],
        verified_claims=["Testé sous contrôle dermatologique"],
        cta="Découvrir",
    )
    return EvidenceLedger.from_generation_request(
        product_name="Sérum Éclat",
        category="Sérum",
        brand="Maison Exemple",
        request=request,
    )


def make_copy(text: str, claims: tuple[ClaimUsage, ...]) -> CopyPayload:
    platform = PlatformCopy(text=text, hashtags=("#serum",), claims=claims)
    return CopyPayload(
        language=CampaignLanguage.FR,
        instagram=platform,
        facebook=platform,
        linkedin=platform,
    )


def test_evidence_ledger_assigns_stable_source_ids() -> None:
    ledger = make_ledger()

    assert tuple(item.id for item in ledger.items) == (
        "product_name-1",
        "category-1",
        "brand-1",
        "benefit-1",
        "ingredient-1",
        "verified_claim-1",
        "cta-1",
    )


def test_copy_with_declared_verified_evidence_is_accepted() -> None:
    ledger = make_ledger()
    selection = PlatformCopySelection(
        evidence_ids=("benefit-1", "verified_claim-1")
    )
    copy = assemble_copy_payload(
        CopyPlan(
            language=CampaignLanguage.FR,
            instagram=selection,
            facebook=selection,
            linkedin=selection,
        ),
        ledger,
    )

    validate_copy_payload(copy, ledger)


def test_copy_rejects_unknown_evidence_reference() -> None:
    ledger = make_ledger()
    copy = make_copy(
        "Une efficacité prouvée.",
        (ClaimUsage(evidence_id="verified_claim-99", rendered_text="efficacité prouvée"),),
    )

    with pytest.raises(ClaimSafetyError, match="unknown evidence"):
        validate_copy_payload(copy, ledger)


def test_copy_rejects_claim_receipt_not_present_in_rendered_text() -> None:
    ledger = make_ledger()
    copy = make_copy(
        "Une texture légère.",
        (ClaimUsage(evidence_id="benefit-1", rendered_text="Hydrate la peau"),),
    )

    with pytest.raises(ClaimSafetyError, match="rendered text"):
        validate_copy_payload(copy, ledger)


def test_copy_rejects_high_risk_claim_without_matching_verified_input() -> None:
    ledger = make_ledger()
    copy = make_copy("Guérit l’acné en 24 heures.", ())

    with pytest.raises(ClaimSafetyError, match="deterministic"):
        validate_copy_payload(copy, ledger)


@pytest.mark.parametrize(
    "language,text",
    [
        (CampaignLanguage.FR, "Efface les rides."),
        (CampaignLanguage.FR, "Enrichi en rétinol."),
        (CampaignLanguage.FR, "Soigne l’acné."),
        (CampaignLanguage.FR, "90 % de résultats en 24 heures."),
        (CampaignLanguage.FR, "Certifié bio et vegan."),
        (CampaignLanguage.FR, "Emballage écologique et recyclable."),
        (CampaignLanguage.EN, "Erases wrinkles."),
        (CampaignLanguage.EN, "Infused with retinol."),
        (CampaignLanguage.EN, "Treats acne."),
        (CampaignLanguage.EN, "90% results in 24 hours."),
        (CampaignLanguage.EN, "Certified organic and vegan."),
        (CampaignLanguage.EN, "Eco-friendly recyclable packaging."),
    ],
)
def test_copy_rejects_any_untagged_model_prose(
    language: CampaignLanguage, text: str
) -> None:
    ledger = make_ledger()
    platform = PlatformCopy(text=text, hashtags=("#serum",), claims=())
    copy = CopyPayload(
        language=language,
        instagram=platform,
        facebook=platform,
        linkedin=platform,
    )

    with pytest.raises(ClaimSafetyError, match="deterministic"):
        validate_copy_payload(copy, ledger)


def test_copy_plan_rejects_evidence_not_safe_for_model_selection() -> None:
    ledger = make_ledger()
    selection = PlatformCopySelection(evidence_ids=("product_name-1",))
    plan = CopyPlan(
        language=CampaignLanguage.FR,
        instagram=selection,
        facebook=selection,
        linkedin=selection,
    )

    with pytest.raises(ClaimSafetyError, match="not selectable"):
        assemble_copy_payload(plan, ledger)


def test_platform_templates_are_language_specific_but_facts_remain_verbatim() -> None:
    ledger = make_ledger()
    selection = PlatformCopySelection(evidence_ids=("benefit-1",))
    french = assemble_copy_payload(
        CopyPlan(
            language=CampaignLanguage.FR,
            instagram=selection,
            facebook=selection,
            linkedin=selection,
        ),
        ledger,
    )
    english = assemble_copy_payload(
        CopyPlan(
            language=CampaignLanguage.EN,
            instagram=selection,
            facebook=selection,
            linkedin=selection,
        ),
        ledger,
    )

    assert french.instagram.text.startswith(
        "Maison Exemple présente Sérum Éclat."
    )
    assert french.facebook.text.startswith(
        "Découvrez Sérum Éclat, par Maison Exemple."
    )
    assert english.instagram.text.startswith(
        "Maison Exemple presents Sérum Éclat."
    )
    assert "Hydrate la peau" in french.instagram.text
    assert "Hydrate la peau" in english.instagram.text
