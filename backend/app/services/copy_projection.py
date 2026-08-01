"""Map the strict Colab copy contract to the existing browser response shape."""
from __future__ import annotations

from typing import Any, Mapping


PLATFORMS = ("instagram", "facebook", "linkedin")


def _publication_text(copy: Mapping[str, Any]) -> str:
    lines = [
        str(copy["titre"]).strip(),
        str(copy["sous_titre"]).strip(),
        *[f"• {item.strip()}" for item in copy.get("bullets", []) if str(item).strip()],
        str(copy["cta"]).strip(),
    ]
    return "\n".join(line for line in lines if line)


def project_copy_for_browser(copy: Mapping[str, Any] | None) -> dict[str, dict[str, Any]] | None:
    if not copy:
        return None
    text = _publication_text(copy)
    hashtags = [str(item).strip() for item in copy.get("hashtags", []) if str(item).strip()]
    return {
        platform: {
            "text": text,
            "hashtags": hashtags,
            "claims": [],
        }
        for platform in PLATFORMS
    }
