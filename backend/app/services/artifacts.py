"""Validation and deterministic finalisation for Colab campaign bundles."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


REQUIRED_ARCHIVE_FILES = frozenset(
    {
        "instagram.jpg",
        "facebook.jpg",
        "linkedin.jpg",
        "cutout.png",
        "mask.png",
        "background.jpg",
        "copy.json",
        "ocr.json",
        "manifest.json",
    }
)
# Pipeline 1.2.0 promotes diagnostic images to evidence-bearing members.
OPTIONAL_ARCHIVE_FILES = frozenset()
ARTIFACT_NAMES = REQUIRED_ARCHIVE_FILES | OPTIONAL_ARCHIVE_FILES
DERIVED_ARTIFACT_NAMES = frozenset(
    f"{platform}-enhanced.jpg" for platform in ("instagram", "facebook", "linkedin")
)
PUBLIC_ARTIFACT_NAMES = ARTIFACT_NAMES | DERIVED_ARTIFACT_NAMES
CHECKSUM_MEMBER_NAMES = ARTIFACT_NAMES
PIPELINE_VERSION = "1.2.0"
MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 150 * 1024 * 1024


class ArtifactContractError(RuntimeError):
    """The remote ZIP does not satisfy the trusted Colab contract."""


class ArtifactChecksumMismatch(ArtifactContractError):
    """A member checksum or manifest checksum is invalid."""


@dataclass(frozen=True)
class ValidatedBundle:
    manifest: dict[str, Any]
    copy: dict[str, Any]
    ocr: dict[str, Any]
    members: dict[str, bytes]
    records: dict[str, dict[str, Any]]
    checksum: str
    finalized_bundle: bytes = b""

    @property
    def bundle(self) -> bytes:
        """Compatibility alias for callers that call the result ``bundle``."""
        return self.finalized_bundle


def _strict_json(payload: bytes, name: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ArtifactContractError(f"{name} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError(f"{name} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactContractError(f"{name} must contain an object")
    return value


_ENGLISH_CREATIVE_TERMS = {"activated", "acivated", "all", "and", "care", "clean", "daily", "discover", "every", "feel", "for", "lasting", "motion", "protect", "with", "your"}
_UNTRUSTED_OCR_FRAGMENTS = ("acivated", "oz alco", "o2 alco", "ocr typo")
_CLAIM_TERMS = ("clinique", "dermatologique", "dermatologiquement", "guéri", "guÃ©ri", "guérit", "traite", "traitement")
_CATEGORY_ALIASES = {
    "dÃ©odorant": "deodorant", "déodorant": "deodorant", "deodorant": "deodorant",
    "hygiène": "deodorant", "hygiene": "deodorant", "parfum": "perfume", "fragrance": "perfume",
    "soin visage": "skincare", "soin_visage": "skincare", "skincare": "skincare",
    "soin corps": "bodycare", "soin_corps": "bodycare", "body care": "bodycare", "bodycare": "bodycare",
    "cheveux": "haircare", "haircare": "haircare", "hair": "haircare", "solaire": "sunscreen", "sunscreen": "sunscreen",
}


def _normalise_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\n", " ").strip())


def _category_key(value: Any) -> str:
    raw = _normalise_text(value).casefold().replace("-", " ")
    return _CATEGORY_ALIASES.get(raw, raw)


def _numeric_claims(value: str) -> set[str]:
    claims: set[str] = set()
    for match in re.finditer(r"\b(\d{1,4})\s*(h|hr|hrs|hour|hours|heure|heures|j|jour|jours|mois|an|ans)\b", value, flags=re.IGNORECASE):
        unit = {"hr": "h", "hrs": "h", "hour": "h", "hours": "h", "heure": "h", "heures": "h", "jour": "j", "jours": "j", "ans": "an"}.get(match.group(2).casefold(), match.group(2).casefold())
        claims.add(f"{match.group(1)}{unit}")
    for match in re.finditer(r"\b(\d{1,3})\s*%", value):
        claims.add(f"{match.group(1)}%")
    for match in re.finditer(r"\b(?:spf|fps)\s*(\d{1,3})\b", value, flags=re.IGNORECASE):
        claims.add(f"spf{match.group(1)}")
    return claims


def _copy_contract(copy: dict[str, Any], *, ocr: dict[str, Any] | None = None, expected_product: dict[str, Any] | None = None, expected_language: Any | None = None, expected_verified_claims: Any | None = None) -> None:
    required = {
        "brand",
        "product_name",
        "category",
        "titre",
        "sous_titre",
        "bullets",
        "cta",
        "hashtags",
        "_meta",
    }
    if set(copy) != required:
        raise ArtifactContractError("copy.json fields do not match the strict contract")
    for field in ("brand", "product_name", "category", "titre", "sous_titre", "cta"):
        if not isinstance(copy[field], str) or not copy[field].strip():
            raise ArtifactContractError(f"copy.json {field} must be non-empty text")
    if not isinstance(copy["bullets"], list) or len(copy["bullets"]) > 3:
        raise ArtifactContractError("copy.json bullets are invalid")
    if not all(isinstance(item, str) and item.strip() for item in copy["bullets"]):
        raise ArtifactContractError("copy.json bullets must be non-empty strings")
    if not isinstance(copy["hashtags"], list) or len(copy["hashtags"]) > 5:
        raise ArtifactContractError("copy.json hashtags are invalid")
    if not all(isinstance(item, str) and item.strip() for item in copy["hashtags"]):
        raise ArtifactContractError("copy.json hashtags must be non-empty strings")
    meta = copy["_meta"]
    if not isinstance(meta, dict):
        raise ArtifactContractError("copy.json _meta must be an object")
    if not isinstance(meta.get("ocr_text"), str) or not meta["ocr_text"].strip():
        raise ArtifactContractError("copy.json _meta.ocr_text is required")
    if meta.get("source") != "ocr+metadata":
        raise ArtifactContractError("copy.json _meta.source must be ocr+metadata")
    if ocr is not None:
        ocr_text = _normalise_text(ocr.get("text"))
        if not ocr_text or _normalise_text(meta["ocr_text"]) != ocr_text:
            raise ArtifactContractError("copy.json OCR provenance does not match ocr.json")
        items = ocr.get("items", [])
        if not isinstance(items, list):
            raise ArtifactContractError("ocr.json items must be an array")
        for item in items:
            confidence = item.get("confidence", 0.0) if isinstance(item, dict) else -1
            if not isinstance(item, dict) or not _normalise_text(item.get("text")) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
                raise ArtifactContractError("ocr.json contains an invalid evidence item")
    if expected_product:
        expected_brand = expected_product.get("brand")
        expected_name = expected_product.get("name", expected_product.get("product_name"))
        expected_category = expected_product.get("category")
        if expected_brand and copy["brand"].casefold() != str(expected_brand).strip().casefold():
            raise ArtifactContractError("copy.json brand does not match the product")
        if expected_name and copy["product_name"].casefold() != str(expected_name).strip().casefold():
            raise ArtifactContractError("copy.json product name does not match the product")
        if expected_category and _category_key(copy["category"]) != _category_key(expected_category):
            raise ArtifactContractError("copy.json category does not match the product")
    lowered = json.dumps(copy, ensure_ascii=False).casefold()
    for placeholder in ("maison exemple", "example brand", "hydra glow serum"):
        if placeholder in lowered:
            raise ArtifactContractError("copy.json contains placeholder copy")
    creative = " ".join([copy["titre"], copy["sous_titre"], *copy["bullets"], copy["cta"]])
    creative_lowered = creative.casefold()
    if any(fragment in creative_lowered for fragment in _UNTRUSTED_OCR_FRAGMENTS):
        raise ArtifactContractError("copy.json contains OCR garbage in creative text")
    # The poster header already renders brand/product. Repeating the product
    # name in the title is a known model failure and causes visual duplication.
    if copy["product_name"].casefold() in copy["titre"].casefold():
        raise ArtifactContractError("copy.json title repeats the product identity")
    language = getattr(expected_language, "value", expected_language)
    if str(language or "").casefold().split("-", 1)[0] == "fr" and set(re.findall(r"[a-z]+", creative_lowered)) & _ENGLISH_CREATIVE_TERMS:
        raise ArtifactContractError("copy.json French creative contains English OCR text")
    evidence_text = str(meta["ocr_text"]).casefold()
    if expected_verified_claims:
        if isinstance(expected_verified_claims, (list, tuple, set, frozenset)):
            evidence_text += " " + " ".join(str(item) for item in expected_verified_claims)
        else:
            evidence_text += " " + str(expected_verified_claims)
    for claim in _CLAIM_TERMS:
        if claim in lowered and claim not in evidence_text:
            raise ArtifactContractError("copy.json contains an unsupported claim")
    if _numeric_claims(creative_lowered) - _numeric_claims(evidence_text):
        raise ArtifactContractError("copy.json contains an unsupported numeric claim")
    for claim in ("clinique", "dermatologique", "guérit", "traite"):
        if claim in lowered and claim not in meta["ocr_text"].casefold():
            raise ArtifactContractError("copy.json contains an unsupported claim")


_SAFE_FRENCH_COPY = {
    "deodorant": ("Fraîcheur au quotidien", "Une sensation propre et légère.", "Découvrir"),
    "perfume": ("Une signature au quotidien", "Un geste parfumé, simple et élégant.", "Découvrir"),
    "skincare": ("Un geste essentiel", "Une routine soin simple et agréable.", "Découvrir"),
    "haircare": ("Soin au quotidien", "Un geste simple pour votre routine.", "Découvrir"),
    "bodycare": ("Confort au quotidien", "Un geste soin simple et agréable.", "Découvrir"),
}


def finalize_copy(copy: dict[str, Any], ocr: dict[str, Any], *, expected_product: dict[str, Any] | None = None, expected_language: Any | None = None, expected_verified_claims: Any | None = None) -> dict[str, Any]:
    """Repair only unsafe creative text using a local, deterministic template."""
    try:
        _copy_contract(copy, ocr=ocr, expected_product=expected_product, expected_language=expected_language, expected_verified_claims=expected_verified_claims)
        checked = dict(copy)
        checked["_meta"] = {**dict(copy["_meta"]), "finalized_by": "backend-deterministic-v1"}
        return checked
    except ArtifactContractError as exc:
        message = str(exc)
        if not any(marker in message for marker in ("OCR garbage", "repeats the product", "French creative", "unsupported claim", "unsupported numeric")):
            raise
    expected_product = expected_product or {}
    brand = str(expected_product.get("brand") or copy.get("brand") or "").strip()
    product_name = str(expected_product.get("name") or expected_product.get("product_name") or copy.get("product_name") or "").strip()
    category = _category_key(expected_product.get("category") or copy.get("category") or "cosmetics")
    language = getattr(expected_language, "value", expected_language)
    if str(language or "fr").casefold().split("-", 1)[0] != "fr":
        raise ArtifactContractError("copy.json creative cannot be repaired for this language")
    title, subtitle, cta = _SAFE_FRENCH_COPY.get(category.casefold(), ("Un geste au quotidien", "Une routine simple et agréable.", "Découvrir"))
    repaired = {
        "brand": brand,
        "product_name": product_name,
        "category": category,
        "titre": title,
        "sous_titre": subtitle,
        "bullets": [],
        "cta": cta,
        "hashtags": [tag for tag in copy.get("hashtags", []) if isinstance(tag, str) and tag.strip()][:5],
        "_meta": {
            "ocr_text": _normalise_text(ocr.get("text")),
            "source": "ocr+metadata",
            "finalized_by": "backend-deterministic-v1",
            "finalization_reason": "unsafe_creative_repaired",
        },
    }
    if not repaired["hashtags"] and brand:
        repaired["hashtags"] = [f"#{re.sub(r'[^A-Za-z0-9]', '', brand)}"]
    _copy_contract(repaired, ocr=ocr, expected_product=expected_product, expected_language=expected_language, expected_verified_claims=expected_verified_claims)
    return repaired


def _image_record(name: str, payload: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            image_format = image.format
            size = image.size
            mode = image.mode
    except (OSError, UnidentifiedImageError) as exc:
        raise ArtifactContractError(f"{name} is not a readable image") from exc

    expected = {
        "instagram.jpg": ((1080, 1080), "JPEG", "RGB"),
        "facebook.jpg": ((1200, 630), "JPEG", "RGB"),
        "linkedin.jpg": ((1200, 627), "JPEG", "RGB"),
        "background.jpg": ((1024, 1024), "JPEG", "RGB"),
        "cutout.png": (None, "PNG", "RGBA"),
        "mask.png": (None, "PNG", "L"),
    }.get(name)
    if expected is None:
        raise ArtifactContractError(f"unknown image artifact {name}")
    expected_size, expected_format, expected_mode = expected
    if expected_size is not None and size != expected_size:
        raise ArtifactContractError(
            f"{name} must be exactly {expected_size[0]}x{expected_size[1]}"
        )
    if image_format != expected_format or mode != expected_mode:
        raise ArtifactContractError(f"{name} has the wrong image format or mode")
    return {
        "name": name,
        "mime": "image/png" if expected_format == "PNG" else "image/jpeg",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "width": size[0],
        "height": size[1],
    }


_POSTER_FORMATS = {
    "instagram.jpg": (1080, 1080),
    "facebook.jpg": (1200, 630),
    "linkedin.jpg": (1200, 627),
}


def _bundled_font(size: int, *, serif: bool = False) -> ImageFont.FreeTypeFont:
    filename = "CormorantGaramond-VariableFont_wght.ttf" if serif else "Manrope-VariableFont_wght.ttf"
    roots = (
        Path(__file__).resolve().parents[1] / "assets" / "fonts",
        Path(__file__).resolve().parents[3] / "ai" / "assets" / "fonts",
    )
    for root in roots:
        candidate = root / filename
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    raise ArtifactContractError(f"bundled typography font is unavailable: {filename}")


def _fit_bundled_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, *, serif: bool = False) -> ImageFont.FreeTypeFont:
    for size in range(start_size, 13, -2):
        font = _bundled_font(size, serif=serif)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _bundled_font(13, serif=serif)


def _draw_wrapped_text(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], font: ImageFont.FreeTypeFont, fill: str, max_width: int, max_lines: int = 3) -> int:
    x, y = xy
    lines: list[str] = []
    current = ""
    for word in str(text).split():
        candidate = word if not current else f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    for line in lines[:max_lines]:
        draw.text((x, y), line, font=font, fill=fill)
        y = draw.textbbox((x, y), line, font=font)[3] + 6
    return y


def _theme_color(value: Any, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return fallback
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def _validated_art_direction(manifest: dict[str, Any]) -> dict[str, Any]:
    """Accept a small, deterministic palette contract from the Colab runtime."""
    raw = manifest.get("art_direction")
    if raw is None:
        return {
            "layout": "halo",
            "panel": "#F6FAF7",
            "accent": "#25A9B8",
            "ink": "#17315E",
        }
    if not isinstance(raw, dict):
        raise ArtifactContractError("manifest art_direction is invalid")
    layout = raw.get("layout")
    if layout not in {"halo", "ribbons", "pedestal", "orbit"}:
        raise ArtifactContractError("manifest art_direction layout is invalid")
    result = {"layout": layout}
    for key, fallback in (
        ("panel", "#F6FAF7"),
        ("accent", "#25A9B8"),
        ("ink", "#17315E"),
        ("line", "#8FB2AE"),
    ):
        value = raw.get(key, fallback)
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ArtifactContractError(f"manifest art_direction {key} is invalid")
        result[key] = value.upper()
    return result


def _overlay_poster_typography(
    payload: bytes,
    copy: dict[str, Any],
    name: str,
    art_direction: dict[str, Any],
) -> bytes:
    expected_size = _POSTER_FORMATS[name]
    try:
        image = Image.open(io.BytesIO(payload)).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ArtifactContractError(f"{name} is not a readable poster") from exc
    if image.size != expected_size:
        raise ArtifactContractError(f"{name} must be exactly {expected_size[0]}x{expected_size[1]}")

    width, height = image.size
    # Colab's renderer reserves the left editorial veil for copy and anchors
    # the untouched product on the right. Paint only this veil; x >= 60% is a
    # protected product region and is never painted by finalisation.
    panel_width = int(width * 0.58)
    # Reconstruct the copy zone rather than painting a translucent wash over
    # stale glyphs. The first 46% is opaque so old identity/headline/CTA pixels
    # cannot ghost through; only the final 12% feathers back into the scene.
    hard_edge = int(width * 0.46)
    veil = Image.new("RGBA", (panel_width, height), (0, 0, 0, 0))
    veil_pixels = veil.load()
    panel = _theme_color(art_direction.get("panel"), (246, 250, 247))
    ink = _theme_color(art_direction.get("ink"), (23, 49, 94))
    accent = _theme_color(art_direction.get("accent"), (37, 169, 184))
    for x in range(panel_width):
        if x <= hard_edge:
            alpha = 255
        else:
            progress = (x - hard_edge) / max(1, panel_width - hard_edge - 1)
            alpha = round(255 * ((1 - progress) ** 1.8))
        x_progress = x / max(1, panel_width - 1)
        for y in range(height):
            y_progress = y / max(1, height - 1)
            # Product-conditioned panel palette prevents every campaign being
            # reconstructed with the old universal pale-aqua veil.
            veil_pixels[x, y] = (
                round(panel[0] - 7 * y_progress + 3 * x_progress),
                round(panel[1] - 5 * y_progress + 2 * x_progress),
                round(panel[2] - 4 * y_progress + 4 * x_progress),
                alpha,
            )
    composited = image.convert("RGBA")
    composited.alpha_composite(veil, (0, 0))
    image = composited.convert("RGB")
    draw = ImageDraw.Draw(image)
    padding = int(width * 0.06)
    max_text_width = max(80, int(width * 0.42) - padding * 2)
    y = padding
    identity = f"{copy['brand'].upper()} / {copy['product_name']}"
    identity_font = _fit_bundled_font(draw, identity, max_text_width, 31 if height > 800 else 28)
    draw.text((padding, y), identity, font=identity_font, fill=ink)
    y = draw.textbbox((padding, y), identity, font=identity_font)[3] + (22 if height > 800 else 16)
    title_font = _fit_bundled_font(draw, copy["titre"], max_text_width, 68 if height > 800 else 48, serif=copy.get("category") in {"perfume", "makeup"})
    y = _draw_wrapped_text(draw, copy["titre"], (padding, y), title_font, ink, max_text_width)
    y += 8
    body_font = _fit_bundled_font(draw, copy["sous_titre"], max_text_width, 27 if height > 800 else 22)
    body_ink = tuple(round(component * 0.78 + 255 * 0.22) for component in ink)
    y = _draw_wrapped_text(draw, copy["sous_titre"], (padding, y), body_font, body_ink, max_text_width, max_lines=3)
    for bullet in copy.get("bullets", [])[:3]:
        y += 6
        draw.ellipse((padding, y + 7, padding + 8, y + 15), fill=accent)
        y = _draw_wrapped_text(draw, bullet, (padding + 19, y), body_font, body_ink, max_text_width - 19, max_lines=2)
    cta_height = 48 if height > 800 else 42
    cta_width = min(max_text_width, 224 if height > 800 else 208)
    cta_y = min(y + 14, height - padding - cta_height)
    draw.rounded_rectangle((padding, cta_y, padding + cta_width, cta_y + cta_height), radius=14, fill=ink)
    cta_font = _fit_bundled_font(draw, copy["cta"].upper(), max(1, cta_width - 32), 21 if height > 800 else 18)
    draw.text((padding + cta_width // 2, cta_y + cta_height // 2), copy["cta"].upper(), font=cta_font, fill="white", anchor="mm")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95, optimize=True)
    return output.getvalue()


def finalize_posters(
    members: dict[str, bytes], copy: dict[str, Any], art_direction: dict[str, Any]
) -> None:
    """Replace untrusted baked text while preserving the right product region."""
    for name in _POSTER_FORMATS:
        members[name] = _overlay_poster_typography(members[name], copy, name, art_direction)


def _build_finalized_zip(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o600 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, members[name])
    finalized = output.getvalue()
    if len(finalized) > MAX_BUNDLE_BYTES:
        raise ArtifactContractError("finalized ZIP size is invalid")
    return finalized


def validate_bundle(
    bundle: bytes,
    *,
    expected_runtime_id: str | None = None,
    expected_generation_id: str | None = None,
    expected_request_id: str | None = None,
    expected_input_snapshot_hash: str | None = None,
    expected_seed: int | None = None,
    expected_language: Any | None = None,
    expected_product: dict[str, Any] | None = None,
    expected_verified_claims: Any | None = None,
    expected_source_size: tuple[int, int] | None = None,
    expected_target_selection: Any = None,
    require_target_selection: bool = False,
) -> ValidatedBundle:
    if not isinstance(bundle, bytes) or not bundle or len(bundle) > MAX_BUNDLE_BYTES:
        raise ArtifactContractError("ZIP size is invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            allowed = REQUIRED_ARCHIVE_FILES | OPTIONAL_ARCHIVE_FILES
            if len(names) != len(set(names)) or any(name not in allowed for name in names):
                raise ArtifactContractError("ZIP contains unknown or duplicate members")
            if not REQUIRED_ARCHIVE_FILES.issubset(names):
                missing = sorted(REQUIRED_ARCHIVE_FILES.difference(names))
                raise ArtifactContractError("ZIP is missing: " + ", ".join(missing))
            total = sum(info.file_size for info in infos)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ArtifactContractError("ZIP uncompressed size is too large")
            for info in infos:
                if info.filename != info.filename.replace("\\", "/") or "/" in info.filename or ".." in info.filename:
                    raise ArtifactContractError("ZIP member path is unsafe")
                if info.flag_bits & 0x1:
                    raise ArtifactContractError("encrypted ZIP members are forbidden")
            members = {name: archive.read(name) for name in names}
    except ArtifactContractError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArtifactContractError("response is not a readable ZIP") from exc

    manifest = _strict_json(members["manifest.json"], "manifest.json")
    art_direction = _validated_art_direction(manifest)
    copy = _strict_json(members["copy.json"], "copy.json")
    ocr = _strict_json(members["ocr.json"], "ocr.json")
    copy = finalize_copy(
        copy,
        ocr,
        expected_product=expected_product,
        expected_language=expected_language,
        expected_verified_claims=expected_verified_claims,
    )
    # Store the canonical bytes, not the untrusted model JSON.  This is the
    # deterministic finalisation seam used by the existing worker/storage flow.
    members["copy.json"] = json.dumps(copy, ensure_ascii=False, indent=2).encode("utf-8")
    finalize_posters(members, copy, art_direction)
    if manifest.get("pipeline_version") != PIPELINE_VERSION:
        raise ArtifactContractError("unsupported Colab pipeline version")
    if manifest.get("preserves_product_pixels") is not True:
        raise ArtifactContractError("manifest does not prove product-pixel preservation")
    if manifest.get("copy_language") is not None and manifest.get("copy_language") != manifest.get("language"):
        raise ArtifactContractError("manifest copy language does not match result language")
    if manifest.get("text_rendering") not in {None, "Pillow"}:
        raise ArtifactContractError("manifest does not prove deterministic text rendering")
    target_source = manifest.get("target_box_source")
    if target_source not in {None, "automatic", "manual"}:
        raise ArtifactContractError("manifest target selection provenance is invalid")
    target_box = manifest.get("target_box")
    if target_box is not None:
        if not isinstance(target_box, dict) or target_box.get("type") != "box":
            raise ArtifactContractError("manifest target_box is invalid")
        try:
            coordinates = [float(target_box[key]) for key in ("x", "y", "width", "height")]
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactContractError("manifest target_box is invalid") from exc
        if not all(math.isfinite(value) for value in coordinates) or coordinates[0] < 0 or coordinates[1] < 0 or coordinates[2] <= 0 or coordinates[3] <= 0 or coordinates[0] + coordinates[2] > 1 or coordinates[1] + coordinates[3] > 1:
            raise ArtifactContractError("manifest target_box is invalid")
    for key, expected in (
        ("runtime_id", expected_runtime_id),
        ("generation_id", expected_generation_id),
        ("request_id", expected_request_id),
        ("input_snapshot_hash", expected_input_snapshot_hash),
        ("seed", expected_seed),
    ):
        if expected is not None and manifest.get(key) != expected:
            raise ArtifactContractError(f"manifest {key} does not match the database request")
    if expected_language is not None and manifest.get("language") not in (expected_language, getattr(expected_language, "value", None)):
        raise ArtifactContractError("manifest language does not match the request")
    if expected_target_selection is not None and manifest.get("target_box") != expected_target_selection:
        raise ArtifactContractError("manifest target selection does not match the request")
    # Automatic rembg selection is valid without a normalized box; an explicit
    # manual hint, however, must round-trip in the manifest exactly.
    if require_target_selection and manifest.get("target_box") is None and target_source != "automatic":
        raise ArtifactContractError("manifest is missing target selection evidence")

    # Persist an explicit local-finalization provenance marker in the stored
    # manifest. This records that no remote model or external image service was
    # used to repair copy/typography after the Colab response arrived.
    manifest["backend_finalization"] = {
        "version": "deterministic-v1",
        "copy": "canonicalized",
        "typography": "Pillow",
    }
    members["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    records: dict[str, dict[str, Any]] = {}
    for name in sorted(REQUIRED_ARCHIVE_FILES | (OPTIONAL_ARCHIVE_FILES & set(members))):
        if name.endswith((".jpg", ".png")):
            records[name] = _image_record(name, members[name])
        else:
            records[name] = {
                "name": name,
                "mime": "application/json",
                "sha256": hashlib.sha256(members[name]).hexdigest(),
                "bytes": len(members[name]),
                "width": None,
                "height": None,
            }
    if records["cutout.png"]["width"] != records["mask.png"]["width"] or records["cutout.png"]["height"] != records["mask.png"]["height"]:
        raise ArtifactContractError("cutout.png and mask.png dimensions must match")
    # Newer runtimes may include a self-described artifact table.  If present,
    # compare it against bytes just read; never trust a remote checksum.
    declared = manifest.get("artifacts")
    if declared is not None:
        if not isinstance(declared, list):
            raise ArtifactContractError("manifest artifacts must be an array")
        by_name = {item.get("name"): item for item in declared if isinstance(item, dict)}
        if set(by_name) != set(records):
            raise ArtifactChecksumMismatch("manifest artifact list does not match ZIP members")
        for name, record in records.items():
            remote = by_name[name]
            if remote.get("sha256") not in {None, record["sha256"]} or remote.get("bytes") not in {None, record["bytes"]}:
                raise ArtifactChecksumMismatch(f"manifest checksum mismatch for {name}")
    # Include every finalized artifact. The manifest entry intentionally omits
    # sha256/bytes because either value would be self-referential after update.
    manifest_artifacts = [records[name] for name in sorted(records) if name != "manifest.json"]
    manifest_artifacts.append({"name": "manifest.json", "mime": "application/json", "width": None, "height": None})
    manifest = {
        **manifest,
        "artifacts": manifest_artifacts,
    }
    members["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    records["manifest.json"] = {
        **records["manifest.json"],
        "sha256": hashlib.sha256(members["manifest.json"]).hexdigest(),
        "bytes": len(members["manifest.json"]),
    }
    finalized_bundle = _build_finalized_zip(members)
    return ValidatedBundle(
        manifest=manifest,
        copy=copy,
        ocr=ocr,
        members=members,
        records=records,
        checksum=hashlib.sha256(finalized_bundle).hexdigest(),
        finalized_bundle=finalized_bundle,
    )
