from __future__ import annotations

import io
import json
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
    "deodorant": "fresh aqua bathroom, clean white surface, water droplets and soft blue light",
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

_FONT_CANDIDATES = {
    "regular": [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "bold": [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "serif": [
        "C:/Windows/Fonts/times.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ],
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
    for term in CLAIM_TERMS:
        if term in all_copy.casefold() and term not in normalized_ocr:
            raise CopyContractError(f"unsupported claim: {term}")

    normalized["_meta"]["ocr_text"] = normalize_ocr_text(ocr_text)
    normalized["_meta"]["source"] = normalized["_meta"].get("source", "ocr+metadata")
    if normalized["_meta"]["source"] != "ocr+metadata":
        raise CopyContractError("copy source must be ocr+metadata")
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
    for candidate in _FONT_CANDIDATES.get(style, _FONT_CANDIDATES["regular"]):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


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
    shadow = Image.new("RGBA", product.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(18)))
    shadow = Image.eval(shadow, lambda value: int(value * 0.28))
    canvas.alpha_composite(shadow, (position[0] + 14, position[1] + 18))


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
        panel_width = int(width * 0.47)
        product_box = (int(width * 0.38), int(height * 0.78))
        product_anchor = (int(width * 0.76), int(height * 0.56))
        padding = int(width * 0.055)
    else:
        panel_width = int(width * 0.49)
        product_box = (int(width * 0.42), int(height * 0.78))
        product_anchor = (int(width * 0.77), int(height * 0.53))
        padding = int(width * 0.065)

    panel = Image.new("RGBA", (panel_width, height), (248, 248, 244, 238))
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
    _add_contact_shadow(canvas, resized_product, (product_x, product_y))
    canvas.alpha_composite(resized_product, (product_x, product_y))

    y = padding
    eyebrow = _fit_font(draw, copy["brand"].upper(), max_text_width, 38 if is_landscape else 44, "bold")
    draw.text((padding, y), copy["brand"].upper(), font=eyebrow, fill="#17315E")
    y += draw.textbbox((padding, y), copy["brand"].upper(), font=eyebrow)[3] + (28 if is_landscape else 36)

    title_font = _fit_font(draw, copy["titre"], max_text_width, 70 if is_landscape else 76, "serif")
    y = _draw_wrapped(draw, copy["titre"], (padding, y), title_font, "#172B4D", max_text_width, 8)
    y += 14

    subtitle_font = _fit_font(draw, copy["sous_titre"], max_text_width, 32 if is_landscape else 34, "regular")
    y = _draw_wrapped(draw, copy["sous_titre"], (padding, y), subtitle_font, "#334E68", max_text_width, 8)
    y += 18

    bullet_font = _load_font(22 if is_landscape else 25, "regular")
    for bullet in copy.get("bullets", [])[:3]:
        bullet_lines = _wrap_lines(draw, bullet, bullet_font, max_text_width - 28)
        draw.ellipse((padding, y + 8, padding + 9, y + 17), fill="#25A9B8")
        y = _draw_wrapped(draw, " ".join(bullet_lines), (padding + 22, y), bullet_font, "#334E68", max_text_width - 22, 5)
        y += 5

    cta_font = _load_font(22 if is_landscape else 24, "bold")
    cta_width = min(max_text_width, 260 if is_landscape else 300)
    cta_height = 54 if is_landscape else 60
    cta_y = min(y + 16, height - padding - cta_height)
    draw.rounded_rectangle((padding, cta_y, padding + cta_width, cta_y + cta_height), radius=16, fill="#17315E")
    draw.text((padding + cta_width // 2, cta_y + cta_height // 2), copy["cta"].upper(), font=cta_font, fill="white", anchor="mm")

    return canvas.convert("RGB")


def _image_bytes(image: Image.Image, format_name: str) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format=format_name, quality=95, optimize=True)
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
