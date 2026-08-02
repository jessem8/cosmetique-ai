from __future__ import annotations

import io
import json
import math
import re
import textwrap
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


FORMATS: dict[str, tuple[int, int]] = {
    "instagram": (1080, 1080),
    "facebook": (1200, 630),
    "linkedin": (1200, 627),
}

CATEGORY_STYLES: dict[str, str] = {
    "deodorant": "wet pale-aqua bathroom surface, crisp water droplets, soft daylight, distant white towel texture, clean left-side negative space",
    "perfume": "champagne and rose-gold luxury fragrance scene, glossy reflective surface and warm diffused light",
    "makeup": "editorial beauty scene, satin surface, restrained rose-gold reflections and soft studio light",
    "skincare": "clean premium spa scene, pale marble, soft white daylight and calm neutral textures",
    "haircare": "premium clean bathroom with subtle botanical texture, silk-like highlights and fresh daylight",
    "sunscreen": "sunlit pale stone beside clear turquoise water, warm daylight and clean summer atmosphere",
    "nailcare": "minimal elegant nail salon scene, clean blue-white surface and soft daylight",
    "bodycare": "warm clean body-care studio, natural pale stone and soft comfortable light",
    "cosmetics": "minimal premium beauty studio, clean neutral surface and soft commercial light",
}

CATEGORY_ALIASES: dict[str, str] = {
    "déodorant": "deodorant",
    "deodorant": "deodorant",
    "hygiene": "deodorant",
    "hygiène": "deodorant",
    "parfum": "perfume",
    "fragrance": "perfume",
    "makeup": "makeup",
    "maquillage": "makeup",
    "skincare": "skincare",
    "soin visage": "skincare",
    "soin_visage": "skincare",
    "hair": "haircare",
    "haircare": "haircare",
    "cheveux": "haircare",
    "shampoo": "haircare",
    "shampooing": "haircare",
    "sunscreen": "sunscreen",
    "solaire": "sunscreen",
    "nail care": "nailcare",
    "nail_care": "nailcare",
    "dissolvant": "nailcare",
    "body care": "bodycare",
    "bodycare": "bodycare",
    "soin corps": "bodycare",
    "soin_corps": "bodycare",
}

PLACEHOLDERS = {
    "maison exemple",
    "example brand",
    "hydra glow serum",
    "votre rituel beaute",
    "votre rituel beauté",
    "produit cosmetique",
    "produit cosmétique",
}

CLAIM_TERMS = {
    "clinique",
    "cliniques",
    "dermatologique",
    "dermatologiquement",
    "guérit",
    "guérir",
    "traite",
    "traitement",
    "résultat clinique",
    "resultat clinique",
}

_BUNDLED_FONT_ROOT = Path(__file__).resolve().parent
_FONT_CANDIDATES = {
    # Keep every candidate inside the repository.  A host/system font can
    # differ across Colab, Windows, and Docker and may not contain French
    # accents, so silently falling back to one would make campaign output
    # non-reproducible.
    "regular": [
        str(_BUNDLED_FONT_ROOT / "assets" / "fonts" / "Manrope-VariableFont_wght.ttf"),
    ],
    "bold": [
        str(_BUNDLED_FONT_ROOT / "assets" / "fonts" / "Manrope-VariableFont_wght.ttf"),
    ],
    "serif": [
        str(_BUNDLED_FONT_ROOT / "assets" / "fonts" / "CormorantGaramond-VariableFont_wght.ttf"),
    ],
}

_ENGLISH_COPY_TERMS = {
    "all",
    "adopt",
    "and",
    "beauty",
    "care",
    "clean",
    "daily",
    "day",
    "discover",
    "every",
    "feel",
    "fresh",
    "freshness",
    "for",
    "glow",
    "hair",
    "lasting",
    "light",
    "long",
    "more",
    "new",
    "protect",
    "pure",
    "skin",
    "soft",
    "the",
    "this",
    "try",
    "with",
    "your",
}


class CopyContractError(ValueError):
    """Raised when model-generated copy is unsafe or structurally invalid."""


def normalize_category(category: str | None) -> str:
    value = (category or "").strip().lower().replace("-", " ")
    value = re.sub(r"\s+", " ", value)
    return CATEGORY_ALIASES.get(value, "cosmetics")


def normalize_ocr_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\n", " ").strip())


def _word_count(value: str) -> int:
    return len(re.findall(r"\b\w+[’']?\w*\b", value, flags=re.UNICODE))


def _contains_placeholder(value: str) -> bool:
    normalized = value.casefold().strip()
    return any(item in normalized for item in PLACEHOLDERS)


def _contains_clearly_english_creative(
    values: list[str],
    ignored_tokens: set[str] | None = None,
) -> bool:
    """Detect unmistakably English creative phrases without judging OCR names."""

    tokens = {
        token
        for value in values
        for token in re.findall(r"[a-z]+", value.casefold())
    }
    # Product and brand names can legitimately be English (for example,
    # ``Shower Fresh``), so callers pass only generated creative fields here.
    return bool((tokens - (ignored_tokens or set())) & _ENGLISH_COPY_TERMS)


def _numeric_claims(value: str) -> set[str]:
    """Return canonical duration, percentage, and SPF claims from text."""

    claims: set[str] = set()
    for match in re.finditer(
        r"\b(\d{1,4})\s*(h|hr|hrs|hour|hours|heure|heures|j|jour|jours|mois|an|ans)\b",
        value,
        flags=re.IGNORECASE,
    ):
        unit = match.group(2).casefold()
        unit = {
            "hr": "h",
            "hrs": "h",
            "hour": "h",
            "hours": "h",
            "heure": "h",
            "heures": "h",
            "jour": "j",
            "jours": "j",
            "ans": "an",
        }.get(unit, unit)
        claims.add(f"{match.group(1)}{unit}")
    for match in re.finditer(
        r"\b(\d{1,3})(?:\s*%|\s*(?:percent|pourcent)(?![a-z]))",
        value,
        flags=re.IGNORECASE,
    ):
        claims.add(f"{match.group(1)}%")
    for match in re.finditer(r"\b(?:spf|fps)\s*(\d{1,3})\b", value, flags=re.IGNORECASE):
        claims.add(f"spf{match.group(1)}")
    return claims


def _verified_claim_text(metadata: Mapping[str, Any]) -> str:
    values = metadata.get("verified_claims", ())
    if isinstance(values, str):
        return values
    if isinstance(values, Mapping):
        values = (*values.keys(), *values.values())
    if isinstance(values, (list, tuple, set, frozenset)):
        return " ".join(str(value) for value in values)
    return ""


def validate_marketing_copy(
    payload: Mapping[str, Any],
    ocr_text: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = metadata or {}
    required = ("brand", "product_name", "category", "titre", "sous_titre", "bullets", "cta", "hashtags")
    missing = [key for key in required if key not in payload]
    if missing:
        raise CopyContractError(f"missing fields: {', '.join(missing)}")

    normalized = {
        "brand": str(payload["brand"]).strip(),
        "product_name": str(payload["product_name"]).strip(),
        "category": normalize_category(str(payload["category"])),
        "titre": str(payload["titre"]).strip(),
        "sous_titre": str(payload["sous_titre"]).strip(),
        "bullets": [str(item).strip() for item in payload["bullets"] if str(item).strip()],
        "cta": str(payload["cta"]).strip(),
        "hashtags": [str(item).strip() for item in payload["hashtags"] if str(item).strip()],
        "_meta": dict(payload.get("_meta") or {}),
    }

    for field in ("brand", "product_name", "titre", "sous_titre", "cta"):
        if not normalized[field]:
            raise CopyContractError(f"empty field: {field}")
        if _contains_placeholder(normalized[field]):
            raise CopyContractError(f"placeholder copy in field: {field}")

    if not isinstance(payload["bullets"], list) or len(normalized["bullets"]) > 3:
        raise CopyContractError("bullets must contain at most three items")
    if not isinstance(payload["hashtags"], list) or len(normalized["hashtags"]) > 5:
        raise CopyContractError("hashtags must contain at most five items")
    if _word_count(normalized["titre"]) > 6:
        raise CopyContractError("titre exceeds six words")
    if _word_count(normalized["sous_titre"]) > 14:
        raise CopyContractError("sous_titre exceeds fourteen words")
    if _word_count(normalized["cta"]) > 4:
        raise CopyContractError("cta exceeds four words")

    all_copy = " ".join(
        [normalized["brand"], normalized["product_name"], normalized["titre"], normalized["sous_titre"]]
        + normalized["bullets"]
        + normalized["hashtags"]
    )
    normalized_ocr = normalize_ocr_text(ocr_text).casefold()
    ocr_tokens = set(re.findall(r"[a-z]+", normalized_ocr))
    requested_language = str(metadata.get("language", "")).strip().casefold().replace("_", "-").split("-", 1)[0]
    if requested_language == "fr" and _contains_clearly_english_creative(
        [normalized["titre"], normalized["sous_titre"], *normalized["bullets"], normalized["cta"]],
        ignored_tokens=ocr_tokens,
    ):
        raise CopyContractError("requested French copy contains clearly English creative language")

    verified_text = " ".join(
        value
        for value in (
            _verified_claim_text(metadata),
            _verified_claim_text(normalized["_meta"]),
            str(metadata.get("brand", "")),
            str(metadata.get("product_name", "")),
        )
        if value
    )
    evidence_claims = _numeric_claims(normalized_ocr) | _numeric_claims(verified_text)
    unsupported_numeric = _numeric_claims(all_copy) - evidence_claims
    if unsupported_numeric:
        claims = ", ".join(sorted(unsupported_numeric))
        raise CopyContractError(f"unsupported numeric claim without OCR or verified evidence: {claims}")

    for term in CLAIM_TERMS:
        if term in all_copy.casefold() and term not in normalized_ocr:
            raise CopyContractError(f"unsupported claim: {term}")

    normalized["_meta"]["ocr_text"] = normalize_ocr_text(ocr_text)
    raw_source = normalize_ocr_text(str(normalized["_meta"].get("source", "ocr+metadata"))).casefold()
    canonical_source = raw_source.replace(" ", "")
    if canonical_source != "ocr+metadata":
        raise CopyContractError("copy source must be ocr+metadata")
    # The source marker is contract metadata, not creative copy. Canonicalize
    # harmless casing/spacing variants returned by the language model while
    # keeping the ZIP contract exact for backend validation.
    normalized["_meta"]["source"] = "ocr+metadata"
    if not normalized["_meta"]["ocr_text"] and not metadata:
        raise CopyContractError("copy has no OCR or metadata evidence")

    return normalized


def build_inpaint_prompt(category: str) -> str:
    normalized = normalize_category(category)
    style = CATEGORY_STYLES[normalized]
    return (
        "Photorealistic premium cosmetic advertising environment, "
        f"{style}. Generate only the environment around a preserved product. "
        "Leave generous clean negative space for deterministic typography. "
        "Realistic commercial lighting, physically plausible surface contact, "
        "soft contact shadow, natural reflections, high-end campaign photography. "
        "No text, no letters, no logo, no watermark, no fake package, no extra product, "
        "no vase, no fruit, no flowers, no unrelated props, no people, no hands, "
        "no floating object, no magenta generic studio."
    )


def _load_font(size: int, style: str = "regular") -> ImageFont.ImageFont:
    candidates = tuple(_FONT_CANDIDATES.get(style, _FONT_CANDIDATES["regular"]))
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            font = ImageFont.truetype(str(path), size=size)
            if hasattr(font, "set_variation_by_name"):
                try:
                    names = set(font.get_variation_names())
                    preferred = {
                        "regular": b"Regular",
                        "serif": b"Regular",
                        "bold": b"SemiBold",
                    }.get(style)
                    if preferred in names:
                        font.set_variation_by_name(preferred)
                except (AttributeError, OSError):
                    # Weight axes are a visual enhancement; loading the
                    # bundled FreeType face remains the hard requirement.
                    pass
            return font
        except OSError:
            # A corrupt candidate should not make us silently use a system
            # fallback; try the next bundled file and fail explicitly below.
            continue
    attempted = ", ".join(candidates) or "(none)"
    raise FileNotFoundError(
        f"Bundled {style} font is missing or unreadable; tried: {attempted}"
    )


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, style: str) -> ImageFont.ImageFont:
    for size in range(start_size, 15, -2):
        font = _load_font(size, style)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _load_font(15, style)


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    gap: int = 8,
) -> int:
    x, y = xy
    lines = _wrap_lines(draw, text, font, max_width)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y = bbox[3] + gap
    return y


def _fit_product(product: Image.Image, max_width: int, max_height: int) -> Image.Image:
    product = product.convert("RGBA")
    scale = min(max_width / product.width, max_height / product.height)
    return product.resize(
        (max(1, int(product.width * scale)), max(1, int(product.height * scale))),
        Image.Resampling.LANCZOS,
    )


def _add_contact_shadow(canvas: Image.Image, product: Image.Image, position: tuple[int, int]) -> None:
    alpha = product.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return

    # A contact shadow belongs to the surface footprint, not to the whole
    # bottle silhouette.  Build a small, flattened ellipse at the lower edge
    # of the alpha bounds so shoulder/neck cut-outs cannot cast a floating
    # shadow above the product.
    left, _top, right, bottom = bbox
    footprint_width = max(8, int((right - left) * 0.72))
    footprint_height = max(6, int(product.height * 0.12))
    center_x = (left + right) // 2
    shadow_extra = footprint_height * 2 + 8
    shadow_mask = Image.new("L", (product.width, product.height + shadow_extra), 0)
    mask_draw = ImageDraw.Draw(shadow_mask)
    center_y = bottom + footprint_height // 2 + 4
    mask_draw.ellipse(
        (
            center_x - footprint_width // 2,
            center_y - footprint_height // 2,
            center_x + footprint_width // 2,
            center_y + footprint_height // 2,
        ),
        fill=82,
    )
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(max(3, footprint_height // 3)))
    shadow = Image.new("RGBA", shadow_mask.size, (18, 29, 48, 0))
    shadow.putalpha(shadow_mask)
    canvas.alpha_composite(shadow, position)


def _add_surface_reflection(canvas: Image.Image, product: Image.Image, position: tuple[int, int]) -> None:
    """Add a restrained, vertically compressed reflection below the footprint."""

    alpha = product.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return
    left, _top, right, bottom = bbox
    reflection_height = max(1, min(int(product.height * 0.10), product.height))
    source_height = max(1, min(bottom - max(0, bottom - reflection_height * 3), product.height))
    source = product.crop((left, bottom - source_height, right, bottom))
    reflection = source.transpose(Image.Transpose.FLIP_TOP_BOTTOM).resize(
        (max(1, right - left), reflection_height),
        Image.Resampling.LANCZOS,
    )
    reflection_alpha = reflection.getchannel("A").point(lambda value: int(value * 0.10))
    reflection.putalpha(reflection_alpha)
    canvas.alpha_composite(reflection, (position[0] + left, position[1] + bottom))


def _fit_inside_safe_margins(
    product: Image.Image,
    position: tuple[int, int],
    canvas_size: tuple[int, int],
    margin_ratio: float = 0.05,
) -> tuple[Image.Image, tuple[int, int]]:
    """Scale and clamp a product so every opaque pixel clears the safe margin."""

    width, height = canvas_size
    margin_x = max(1, math.ceil(width * margin_ratio))
    margin_y = max(1, math.ceil(height * margin_ratio))
    max_width = max(1, width - margin_x * 2)
    max_height = max(1, height - margin_y * 2)
    if product.width > max_width or product.height > max_height:
        scale = min(max_width / product.width, max_height / product.height)
        product = product.resize(
            (max(1, round(product.width * scale)), max(1, round(product.height * scale))),
            Image.Resampling.LANCZOS,
        )

    x = min(max(position[0], margin_x), width - margin_x - product.width)
    y = min(max(position[1], margin_y), height - margin_y - product.height)
    return product, (x, y)


def render_poster(
    background: Image.Image,
    product: Image.Image,
    copy: Mapping[str, Any],
    platform: str,
    *,
    source_product_position: tuple[int, int] | None = None,
) -> Image.Image:
    if platform not in FORMATS:
        raise ValueError(f"unsupported platform: {platform}")

    width, height = FORMATS[platform]
    canvas = ImageOps.fit(background.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS).convert("RGBA")

    is_landscape = platform in {"facebook", "linkedin"}
    if is_landscape:
        panel_width = int(width * 0.42)
        product_box = (int(width * 0.36), int(height * 0.74))
        product_anchor = (int(width * 0.78), int(height * 0.55))
        padding = int(width * 0.055)
        brand_size, title_size, body_size = 28, 64, 24
    else:
        panel_width = int(width * 0.44)
        product_box = (int(width * 0.38), int(height * 0.74))
        product_anchor = (int(width * 0.77), int(height * 0.54))
        padding = int(width * 0.06)
        brand_size, title_size, body_size = 31, 72, 27

    # A feathered editorial veil keeps type readable while preserving the
    # generated photography—there is no hard split-panel boundary.
    veil_width = min(width, round(width * 0.58))
    panel = Image.new("RGBA", (veil_width, height), (248, 248, 244, 0))
    alpha_ramp = Image.new("L", (veil_width, 1))
    for x in range(veil_width):
        progress = x / max(1, veil_width - 1)
        alpha_ramp.putpixel((x, 0), round(112 * ((1 - progress) ** 1.8)))
    panel.putalpha(alpha_ramp.resize((veil_width, height)))
    canvas.alpha_composite(panel, (0, 0))
    draw = ImageDraw.Draw(canvas)
    max_text_width = panel_width - (padding * 2)

    if source_product_position is None:
        resized_product = _fit_product(product, *product_box)
        product_x = product_anchor[0] - resized_product.width // 2
        product_y = product_anchor[1] - resized_product.height // 2
    else:
        # The inpaint scene already contains the preserved product. Reuse the
        # exact same transformed coordinates so the final untouched cutout
        # replaces those pixels instead of creating a second product.
        source_width, source_height = background.size
        scale = max(width / source_width, height / source_height)
        scaled_size = (
            max(1, round(product.width * scale)),
            max(1, round(product.height * scale)),
        )
        resized_product = product.resize(scaled_size, Image.Resampling.LANCZOS)
        crop_x = max(0, round((source_width * scale - width) / 2))
        crop_y = max(0, round((source_height * scale - height) / 2))
        product_x = round(source_product_position[0] * scale - crop_x)
        product_y = round(source_product_position[1] * scale - crop_y)
    resized_product, (product_x, product_y) = _fit_inside_safe_margins(
        resized_product,
        (product_x, product_y),
        (width, height),
    )

    y = padding
    identity = f"{copy['brand'].upper()} / {copy['product_name']}"
    identity_font = _fit_font(draw, identity, max_text_width, brand_size, "bold")
    draw.text((padding, y), identity, font=identity_font, fill="#17315E")
    y += draw.textbbox((padding, y), identity, font=identity_font)[3] + (20 if is_landscape else 24)

    title_style = (
        "serif"
        if normalize_category(str(copy.get("category", ""))) in {"perfume", "makeup"}
        else "bold"
    )
    title_font = _fit_font(draw, copy["titre"], max_text_width, title_size, title_style)
    y = _draw_wrapped(draw, copy["titre"], (padding, y), title_font, "#172B4D", max_text_width, 6)
    y += 10

    body_font = _fit_font(draw, copy["sous_titre"], max_text_width, body_size, "regular")
    y = _draw_wrapped(draw, copy["sous_titre"], (padding, y), body_font, "#334E68", max_text_width, 7)
    y += 12

    for bullet in copy.get("bullets", [])[:3]:
        draw.ellipse((padding, y + 7, padding + 8, y + 15), fill="#25A9B8")
        y = _draw_wrapped(draw, bullet, (padding + 19, y), body_font, "#334E68", max_text_width - 19, 4)
        y += 4

    cta_width = min(max_text_width, 208 if is_landscape else 224)
    cta_height = 44 if is_landscape else 48
    cta_y = y + 14
    if cta_y + cta_height > height - padding:
        raise ValueError(f"copy does not fit the {platform} safe area")
    cta_text = copy["cta"].upper()
    cta_font = _fit_font(
        draw,
        cta_text,
        max(1, cta_width - 32),
        19 if is_landscape else 21,
        "bold",
    )
    draw.rounded_rectangle(
        (padding, cta_y, padding + cta_width, cta_y + cta_height),
        radius=14,
        fill="#17315E",
    )
    draw.text(
        (padding + cta_width // 2, cta_y + cta_height // 2),
        cta_text,
        font=cta_font,
        fill="white",
        anchor="mm",
    )

    # Keep the untouched cutout as the final composited layer.  The shadow is
    # intentionally added first so neither copy nor panel paint can overwrite
    # opaque package pixels.
    _add_surface_reflection(canvas, resized_product, (product_x, product_y))
    _add_contact_shadow(canvas, resized_product, (product_x, product_y))
    canvas.alpha_composite(resized_product, (product_x, product_y))

    return canvas.convert("RGB")


def _image_bytes(image: Image.Image, format_name: str) -> bytes:
    output = io.BytesIO()
    if format_name == "PNG":
        if image.mode not in {"RGBA", "L"}:
            image = image.convert("RGBA")
        image.save(output, format="PNG", optimize=True)
    else:
        image.convert("RGB").save(
            output,
            format=format_name,
            quality=95,
            optimize=True,
        )
    return output.getvalue()


def build_campaign_zip(
    posters: Mapping[str, Image.Image],
    copy: Mapping[str, Any],
    ocr: Mapping[str, Any],
    manifest: Mapping[str, Any],
    diagnostics: Mapping[str, Image.Image] | None = None,
) -> bytes:
    from zipfile import ZIP_DEFLATED, ZipFile

    missing = [name for name in FORMATS if name not in posters]
    if missing:
        raise ValueError(f"missing posters: {', '.join(missing)}")

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for platform in FORMATS:
            archive.writestr(f"{platform}.jpg", _image_bytes(posters[platform], "JPEG"))
        archive.writestr("copy.json", json.dumps(dict(copy), ensure_ascii=False, indent=2))
        archive.writestr("ocr.json", json.dumps(dict(ocr), ensure_ascii=False, indent=2))
        archive.writestr("manifest.json", json.dumps(dict(manifest), ensure_ascii=False, indent=2))
        for name, image in (diagnostics or {}).items():
            suffix = Path(name).suffix.casefold()
            image_format = "JPEG" if suffix in {".jpg", ".jpeg"} else "PNG"
            archive.writestr(name, _image_bytes(image, image_format))
    return buffer.getvalue()
