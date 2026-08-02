"""Safe, platform-aware campaign captions for the browser response."""
from __future__ import annotations

import re
from typing import Any, Mapping


PLATFORMS = ("instagram", "facebook", "linkedin")
_FORBIDDEN_PROMISES = (
    "gu\u00e9rit",
    "gu\u00e9rison",
    "m\u00e9dical",
    "cliniquement",
    "dermatologiquement",
)


def _clean_hashtags(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = str(item).strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + re.sub(r"[^A-Za-z0-9À-ÿ_]", "", tag)
        if len(tag) < 2 or len(tag) > 64 or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        result.append(tag)
        if len(result) == limit:
            break
    return result


def _safe_text(value: Any, *, limit: int = 900) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > limit:
        return None
    lowered = text.casefold()
    if any(term in lowered for term in _FORBIDDEN_PROMISES):
        return None
    return text


def caption_from_response(value: Any, *, platform: str) -> dict[str, Any] | None:
    """Validate the bounded Qwen response before it can reach a user."""
    if platform not in PLATFORMS or not isinstance(value, Mapping):
        return None
    text = _safe_text(value.get("text"))
    if text is None:
        return None
    return {
        "text": text,
        "hashtags": _clean_hashtags(value.get("hashtags"), limit=5 if platform == "instagram" else 2),
        "source": "qwen",
    }


def fallback_caption(copy: Mapping[str, Any], platform: str) -> dict[str, Any]:
    """Create a platform-specific, fact-safe caption without contacting Colab."""
    brand = str(copy.get("brand") or "").strip()
    product = str(copy.get("product_name") or "").strip()
    title = str(copy.get("titre") or "").strip()
    subtitle = str(copy.get("sous_titre") or "").strip()
    cta = str(copy.get("cta") or "Découvrir").strip()
    bullets = [str(item).strip() for item in copy.get("bullets", []) if str(item).strip()]
    hashtags = _clean_hashtags(copy.get("hashtags"), limit=5 if platform == "instagram" else 2)
    identity = " ".join(part for part in (brand, product) if part)

    if platform == "instagram":
        text = "\n".join(part for part in (title, subtitle, cta) if part)
    elif platform == "facebook":
        detail = bullets[0] if bullets else subtitle
        text = "\n\n".join(
            part
            for part in (
                f"Découvrez {identity}." if identity else title,
                detail,
                cta,
            )
            if part
        )
    else:
        detail = bullets[0] if bullets else subtitle
        text = "\n\n".join(
            part
            for part in (
                f"{identity} : {title}" if identity else title,
                detail,
                cta,
            )
            if part
        )
        hashtags = hashtags[:2]
    return {"text": text, "hashtags": hashtags, "source": "library"}


def project_copy_for_browser(
    copy: Mapping[str, Any] | None,
    captions: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]] | None:
    if not copy:
        return None
    stored = captions if isinstance(captions, Mapping) else {}
    result: dict[str, dict[str, Any]] = {}
    for platform in PLATFORMS:
        saved = caption_from_response(stored.get(platform), platform=platform)
        result[platform] = {
            **(saved or fallback_caption(copy, platform)),
            "generated": bool(saved),
            "claims": [],
        }
    return result
