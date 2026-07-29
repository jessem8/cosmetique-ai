from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .errors import ClaimSafetyError
from .schemas import (
    CampaignLanguage,
    ClaimUsage,
    CopyPayload,
    CopyPlan,
    EvidenceItem,
    EvidenceKind,
    GenerationRequest,
    PlatformCopy,
    PlatformCopySelection,
)


_SELECTABLE_KINDS = {
    EvidenceKind.AUDIENCE,
    EvidenceKind.BENEFIT,
    EvidenceKind.INGREDIENT,
    EvidenceKind.VERIFIED_CLAIM,
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("’", "'")
    return " ".join(value.split())


def _hashtag(value: str) -> str | None:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    compact = re.sub(r"[^A-Za-z0-9]+", "", ascii_value).lower()
    if len(compact) < 1:
        return None
    return f"#{compact[:79]}"


@dataclass(frozen=True, slots=True)
class EvidenceLedger:
    items: tuple[EvidenceItem, ...]

    @classmethod
    def from_generation_request(
        cls,
        *,
        product_name: str,
        category: str,
        brand: str | None,
        request: GenerationRequest,
    ) -> EvidenceLedger:
        source_groups: tuple[tuple[EvidenceKind, tuple[str, ...]], ...] = (
            (EvidenceKind.PRODUCT_NAME, (product_name,)),
            (EvidenceKind.CATEGORY, (category,)),
            (EvidenceKind.BRAND, (brand,) if brand else ()),
            (EvidenceKind.AUDIENCE, (request.audience,) if request.audience else ()),
            (EvidenceKind.BENEFIT, request.benefits),
            (EvidenceKind.INGREDIENT, request.ingredients),
            (EvidenceKind.VERIFIED_CLAIM, request.verified_claims),
            (EvidenceKind.CTA, (request.cta,) if request.cta else ()),
        )
        items: list[EvidenceItem] = []
        for kind, values in source_groups:
            for index, value in enumerate(values, start=1):
                items.append(
                    EvidenceItem(id=f"{kind.value}-{index}", kind=kind, text=value)
                )
        return cls(tuple(items))

    def by_id(self) -> dict[str, EvidenceItem]:
        return {item.id: item for item in self.items}

    def first(self, kind: EvidenceKind) -> EvidenceItem | None:
        return next((item for item in self.items if item.kind is kind), None)


def _assemble_platform(
    selection: PlatformCopySelection,
    ledger: EvidenceLedger,
    *,
    language: CampaignLanguage,
    platform: str,
) -> PlatformCopy:
    evidence = ledger.by_id()
    selected: list[EvidenceItem] = []
    for evidence_id in selection.evidence_ids:
        item = evidence.get(evidence_id)
        if item is None:
            raise ClaimSafetyError(f"unknown evidence reference {evidence_id}")
        if item.kind not in _SELECTABLE_KINDS:
            raise ClaimSafetyError(
                f"evidence reference {evidence_id} is not selectable copy evidence"
            )
        selected.append(item)

    product = ledger.first(EvidenceKind.PRODUCT_NAME)
    category = ledger.first(EvidenceKind.CATEGORY)
    brand = ledger.first(EvidenceKind.BRAND)
    cta = ledger.first(EvidenceKind.CTA)
    if product is None or category is None:
        raise ClaimSafetyError("product name and category evidence are required")

    if language is CampaignLanguage.FR:
        introductions = {
            "instagram": (
                f"{brand.text} présente {product.text}."
                if brand
                else f"Découvrez {product.text}."
            ),
            "facebook": (
                f"Découvrez {product.text}, par {brand.text}."
                if brand
                else f"Découvrez {product.text}."
            ),
            "linkedin": (
                f"{brand.text} présente {product.text}."
                if brand
                else f"Présentation de {product.text}."
            ),
        }
    else:
        introductions = {
            "instagram": (
                f"{brand.text} presents {product.text}."
                if brand
                else f"Discover {product.text}."
            ),
            "facebook": (
                f"Discover {product.text} by {brand.text}."
                if brand
                else f"Discover {product.text}."
            ),
            "linkedin": (
                f"{brand.text} presents {product.text}."
                if brand
                else f"Introducing {product.text}."
            ),
        }
    lines = [
        introductions[platform],
        *(item.text for item in selected),
        *(item.text for item in (cta,) if item is not None),
    ]
    hashtags: list[str] = []
    for item in (category, brand):
        if item is None:
            continue
        tag = _hashtag(item.text)
        if tag is not None and tag.casefold() not in {
            existing.casefold() for existing in hashtags
        }:
            hashtags.append(tag)

    return PlatformCopy(
        text="\n".join(lines),
        hashtags=tuple(hashtags),
        claims=tuple(
            ClaimUsage(evidence_id=item.id, rendered_text=item.text)
            for item in selected
        ),
    )


def assemble_copy_payload(
    plan: CopyPlan,
    ledger: EvidenceLedger,
) -> CopyPayload:
    """Turn evidence-ID selections into copy without accepting model-authored prose."""

    if not ledger.items:
        raise ClaimSafetyError("evidence ledger must not be empty")
    return CopyPayload(
        language=plan.language,
        instagram=_assemble_platform(
            plan.instagram,
            ledger,
            language=plan.language,
            platform="instagram",
        ),
        facebook=_assemble_platform(
            plan.facebook,
            ledger,
            language=plan.language,
            platform="facebook",
        ),
        linkedin=_assemble_platform(
            plan.linkedin,
            ledger,
            language=plan.language,
            platform="linkedin",
        ),
    )


def _selection_from_copy(
    platform_copy: PlatformCopy,
    ledger: EvidenceLedger,
) -> PlatformCopySelection:
    evidence = ledger.by_id()
    normalized_text = _normalize(platform_copy.text)
    for usage in platform_copy.claims:
        item = evidence.get(usage.evidence_id)
        if item is None:
            raise ClaimSafetyError(
                f"unknown evidence reference {usage.evidence_id}"
            )
        if _normalize(usage.rendered_text) != _normalize(item.text):
            raise ClaimSafetyError(
                "claim rendered text must exactly match its verified evidence"
            )
        if _normalize(usage.rendered_text) not in normalized_text:
            raise ClaimSafetyError(
                "claim rendered text is absent from platform copy"
            )
    return PlatformCopySelection(
        evidence_ids=tuple(usage.evidence_id for usage in platform_copy.claims)
    )


def validate_copy_payload(copy: CopyPayload, ledger: EvidenceLedger) -> None:
    """
    Fail closed: valid copy must equal the deterministic rendering of its receipts.

    This prevents an LLM from smuggling an unsupported claim into untagged prose or
    from attaching a valid evidence ID to different wording.
    """

    try:
        plan = CopyPlan(
            language=copy.language,
            instagram=_selection_from_copy(copy.instagram, ledger),
            facebook=_selection_from_copy(copy.facebook, ledger),
            linkedin=_selection_from_copy(copy.linkedin, ledger),
        )
        expected = assemble_copy_payload(plan, ledger)
    except (ValueError, ClaimSafetyError) as exc:
        if isinstance(exc, ClaimSafetyError):
            raise
        raise ClaimSafetyError("copy receipts violate the deterministic contract") from exc
    if copy != expected:
        raise ClaimSafetyError(
            "copy differs from deterministic evidence rendering; arbitrary model prose is forbidden"
        )
