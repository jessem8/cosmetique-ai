"""Local, product-safe premium finishes for completed campaign visuals."""
from __future__ import annotations

import io
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageEnhance, UnidentifiedImageError

from app.services.artifacts import ArtifactContractError


_SIZES = {
    "instagram": (1080, 1080),
    "facebook": (1200, 630),
    "linkedin": (1200, 627),
}


def _rgb(value: Any, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        value = str(value).strip().lstrip("#")
        if len(value) != 6:
            raise ValueError
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    except (TypeError, ValueError):
        return fallback


def premium_finish(
    payload: bytes,
    *,
    platform: str,
    copy: Mapping[str, Any],
    art_direction: Mapping[str, Any] | None,
) -> bytes:
    """Polish only the editorial side; the right product region is byte-derived intact."""
    expected = _SIZES.get(platform)
    if expected is None:
        raise ArtifactContractError("unsupported enhanced platform")
    try:
        source = Image.open(io.BytesIO(payload)).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ArtifactContractError("source poster is not readable") from exc
    if source.size != expected:
        raise ArtifactContractError("source poster has invalid dimensions")

    width, height = source.size
    protected_x = int(width * 0.60)
    accent = _rgb((art_direction or {}).get("accent"), (37, 169, 184))
    ink = _rgb((art_direction or {}).get("ink"), (23, 49, 94))

    # Work on a cropped editorial pane.  Pixels in the retained product region
    # (x >= 60%) are copied from the accepted poster without any treatment.
    pane = source.crop((0, 0, protected_x, height))
    pane = ImageEnhance.Contrast(pane).enhance(1.025)
    pane = ImageEnhance.Color(pane).enhance(1.035)
    overlay = Image.new("RGBA", pane.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    glow_size = int(min(width, height) * 0.28)
    draw.ellipse(
        (int(width * 0.26), int(height * 0.62), int(width * 0.26) + glow_size, int(height * 0.62) + glow_size),
        fill=(*accent, 24),
    )
    rule_y = int(height * 0.89)
    draw.rounded_rectangle(
        (int(width * 0.06), rule_y, int(width * 0.30), rule_y + max(3, height // 230)),
        radius=4,
        fill=(*accent, 170),
    )
    # A quiet signature is intentionally claim-free: it reuses only the
    # verified product identity already present in the campaign copy.
    identity = str(copy.get("brand") or copy.get("product_name") or "").strip().upper()
    if identity:
        from app.services.artifacts import _bundled_font

        font = _bundled_font(15 if height > 800 else 13)
        draw.text((int(width * 0.06), rule_y + max(12, height // 70)), identity, font=font, fill=(*ink, 145))
    pane = Image.alpha_composite(pane.convert("RGBA"), overlay).convert("RGB")
    output = Image.new("RGB", source.size)
    output.paste(pane, (0, 0))
    output.paste(source.crop((protected_x, 0, width, height)), (protected_x, 0))
    encoded = io.BytesIO()
    output.save(encoded, format="JPEG", quality=95, optimize=True)
    return encoded.getvalue()
