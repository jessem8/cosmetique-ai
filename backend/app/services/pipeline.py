"""
AI Pipeline Service — runs on Google Colab (GPU T4).

Architecture:
  - Models are loaded ONCE into global variables at startup.
  - A threading.Semaphore(1) prevents concurrent GPU usage.
  - Each step updates `generations.status` in the DB.
  - Imports of AI libs are deferred to avoid import errors on Windows dev.

Pipeline steps:
  1. remove_background()     → rembg
  2. detect_category()       → keyword map + Qwen2.5 fallback
  3. generate_decor()        → SDXL Inpainting + IP-Adapter
  4. compose_final()         → Pillow (product + shadow + decor)
  5. generate_marketing_text() → Ollama / Qwen2.5 → JSON
  6. render_poster()         → Pillow (3 templates)
  7. export_formats()        → resize to 3 social dimensions
  8. upload & save assets    → Cloudinary + DB
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import traceback
import uuid
from datetime import datetime, timezone
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlalchemy.orm import Session

from app.models import Asset, AssetFormat, Generation, GenerationStatus, Product
from app.services.storage import upload_pil_image

logger = logging.getLogger(__name__)

# ── GPU semaphore (1 concurrent generation) ────────────────────────────────
_gpu_semaphore = threading.Semaphore(1)

# ── Global model cache (loaded once per Colab session) ────────────────────
_models: dict = {}
_models_loaded = False


# ══════════════════════════════════════════════════════════════════════════
#  Category detection
# ══════════════════════════════════════════════════════════════════════════

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "solaire": [
        "solaire", "spf", "ecran", "sun", "bronzant", "uva", "uvb",
        "sunscreen", "tanning", "autobronzant",
    ],
    "cheveux": [
        "shampoing", "shampooing", "conditioner", "apres-shampoing",
        "apres shampoing", "masque capillaire", "serum capillaire",
        "cheveux", "hair", "keratine", "baume", "soin cheveux",
    ],
    "maquillage": [
        "fond de teint", "mascara", "rouge a levres", "rouge à lèvres",
        "blush", "fard", "correcteur", "concealer", "poudre", "eyeliner",
        "makeup", "maquillage", "gloss", "highlighter", "contour",
    ],
    "hygiene_deo": [
        "deodorant", "déodorant", "deo", "gel douche", "savon",
        "bain douche", "hygiene", "hygiène", "antisudoral", "roll-on",
    ],
    "soin_visage": [
        "serum", "sérum", "creme visage", "crème visage", "soin visage",
        "anti-age", "anti-rides", "contour yeux", "gommage visage",
        "masque visage", "hydratant visage", "toner", "lotion visage",
        "micellair", "demaquillant", "démaquillant",
    ],
    "soin_corps": [
        "lait corps", "huile corps", "beurre", "karité", "karite",
        "creme corps", "crème corps", "soin corps", "body lotion",
        "exfoliant corps", "gommage corps", "vergetures", "cellulit",
    ],
}

CATEGORY_PROMPTS: dict[str, str] = {
    "solaire": (
        "sun-drenched tropical beach setting, golden sand, turquoise ocean waves, "
        "warm sunlight rays, palm leaves, seashells, bright summer atmosphere, "
        "vibrant coral and golden tones, luxury vacation aesthetic"
    ),
    "cheveux": (
        "clean minimalist bathroom with botanical plants, silk fabric texture, "
        "pastel pink and white marble surface, natural light, fresh flowers, "
        "elegant premium hair care aesthetic, soft diffused lighting"
    ),
    "maquillage": (
        "luxurious vanity table, rose gold and pearl white tones, soft bokeh background, "
        "scattered petals, elegant brushes, mirror reflections, "
        "high-end beauty editorial style, glamorous soft lighting"
    ),
    "hygiene_deo": (
        "fresh clean bathroom, crisp white tiles, eucalyptus and mint leaves, "
        "water droplets, cool blue and white tones, "
        "modern minimalist hygiene product photography, refreshing atmosphere"
    ),
    "soin_visage": (
        "pristine white spa setting, jade stone, dropper bottle reflections, "
        "soft pearl and cream tones, subtle bokeh, serene luxury skincare photography, "
        "dewy glass skin texture background, clinical elegance"
    ),
    "soin_corps": (
        "warm natural spa, wooden surface, shea butter texture, "
        "tropical leaves, amber and beige tones, terracotta accents, "
        "earthy premium body care aesthetic, soft warm lighting"
    ),
}


# Auto-select best template per category
CATEGORY_TEMPLATE_MAP: dict[str, str] = {
    "solaire":      "bold",       # Vibrant, sunny → bold headline
    "cheveux":      "minimal",    # Clean beauty → minimal
    "maquillage":   "bold",       # Glamour → bold
    "hygiene_deo":  "classic",    # Functional → classic
    "soin_visage":  "classic",    # Premium skincare → classic
    "soin_corps":   "minimal",    # Natural → minimal
}

TONE_STYLES: dict[str, dict] = {
    "luxe": {
        "adjectives": ["précieux", "exclusif", "raffiné", "prestige"],
        "cta_verbs": ["Découvrez", "Offrez-vous"],
    },
    "naturel": {
        "adjectives": ["naturel", "bio", "pur", "authentique"],
        "cta_verbs": ["Adoptez", "Essayez"],
    },
    "dynamique": {
        "adjectives": ["efficace", "puissant", "révolutionnaire", "innovant"],
        "cta_verbs": ["Boostez", "Transformez"],
    },
    "frais": {
        "adjectives": ["frais", "léger", "vivifiant", "énergisant"],
        "cta_verbs": ["Ressentez", "Vivez"],
    },
    "scientifique": {
        "adjectives": ["cliniquement prouvé", "dermatologique", "formulé", "testé"],
        "cta_verbs": ["Agissez", "Protégez"],
    },
}


def detect_category(name: str, description: Optional[str] = None) -> str:
    """
    Detect product category from name + optional description.

    Strategy:
      1. Keyword matching (fast, deterministic)
      2. Qwen2.5 LLM fallback if ambiguous
    """
    text = (name + " " + (description or "")).lower()

    best_cat = "soin_visage"
    best_score = 0

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_cat = category

    if best_score >= 1:
        logger.info(f"Category detected by keyword: {best_cat} (score={best_score})")
        return best_cat

    # ── LLM fallback ────────────────────────────────────────────────────────
    try:
        import ollama
        cats = list(CATEGORY_KEYWORDS.keys())
        response = ollama.chat(
            model="qwen2.5",
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this cosmetic product into exactly one of these categories: "
                    f"{', '.join(cats)}.\n"
                    f"Product name: {name}\n"
                    f"Description: {description or 'N/A'}\n"
                    f"Reply with ONLY the category name, nothing else."
                ),
            }],
        )
        detected = response["message"]["content"].strip().lower().replace("-", "_")
        if detected in cats:
            logger.info(f"Category detected by LLM: {detected}")
            return detected
    except Exception as e:
        logger.warning(f"LLM category detection failed: {e}")

    logger.info(f"Defaulting to soin_visage for: {name}")
    return "soin_visage"


# ══════════════════════════════════════════════════════════════════════════
#  Background removal
# ══════════════════════════════════════════════════════════════════════════

def remove_background(image_bytes: bytes) -> Image.Image:
    """Remove background from image bytes. Returns RGBA PIL image."""
    try:
        from rembg import remove as rembg_remove
        result_bytes = rembg_remove(image_bytes)
        return Image.open(io.BytesIO(result_bytes)).convert("RGBA")
    except ImportError:
        logger.warning("rembg not installed — returning original image with white background removed (dev mode)")
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        return img


# ══════════════════════════════════════════════════════════════════════════
#  Model loading (Colab only)
# ══════════════════════════════════════════════════════════════════════════

def load_models() -> None:
    """
    Load SDXL Inpainting + IP-Adapter into GPU memory (fp16).
    Call once at Colab startup before accepting requests.
    """
    global _models, _models_loaded
    if _models_loaded:
        return

    try:
        import torch
        from diffusers import AutoPipelineForInpainting

        logger.info("Loading SDXL Inpainting model...")
        pipe = AutoPipelineForInpainting.from_pretrained(
            "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
            torch_dtype=torch.float16,
            variant="fp16",
        )
        pipe.enable_model_cpu_offload()
        pipe.enable_xformers_memory_efficient_attention()

        _models["inpaint"] = pipe
        _models_loaded = True
        logger.info("✅ Models loaded successfully.")
    except Exception as e:
        logger.error(f"Model loading failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  Decor generation (SDXL Inpainting)
# ══════════════════════════════════════════════════════════════════════════

def generate_decor(product_rgba: Image.Image, category: str) -> Image.Image:
    """
    Generate premium background decor using SDXL Inpainting.

    The product (alpha mask) is preserved; the background is inpainted.
    Falls back to a gradient background in dev mode.
    """
    prompt = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["soin_visage"])

    if not _models_loaded or "inpaint" not in _models:
        logger.warning("SDXL not loaded — using gradient fallback")
        return _gradient_fallback(category, product_rgba.size)

    try:
        import torch
        from PIL import ImageChops

        # Resize to SDXL-friendly dimensions
        target_size = (1024, 1024)
        product_resized = product_rgba.resize(target_size, Image.LANCZOS)

        # Create inpainting mask: white = regions to generate, black = keep
        alpha = product_resized.split()[3]
        mask = Image.new("L", target_size, 255)
        mask.paste(0, mask=alpha)  # black where product is (preserve)
        mask_rgb = mask.convert("RGB")

        # White base image (product composited on white)
        base = Image.new("RGB", target_size, (255, 255, 255))
        base.paste(product_resized, mask=product_resized.split()[3])

        pipe = _models["inpaint"]

        negative_prompt = (
            "text, watermark, logo, ugly, deformed, blurry, low quality, "
            "nsfw, people, faces, hands"
        )

        with torch.inference_mode():
            result = pipe(
                prompt=f"professional cosmetic product photography, {prompt}, "
                       f"studio lighting, ultra high quality, 4k, commercial photography",
                negative_prompt=negative_prompt,
                image=base,
                mask_image=mask_rgb,
                num_inference_steps=30,
                guidance_scale=7.5,
                strength=0.99,
            ).images[0]

        # Clear GPU cache
        torch.cuda.empty_cache()
        return result

    except Exception as e:
        logger.error(f"Decor generation error: {e}")
        return _gradient_fallback(category, product_rgba.size)


def _gradient_fallback(category: str, size: tuple[int, int]) -> Image.Image:
    """Create a beautiful gradient background as fallback."""
    GRADIENTS = {
        "solaire": [(255, 200, 50), (255, 140, 0)],
        "cheveux": [(240, 220, 240), (200, 180, 220)],
        "maquillage": [(255, 220, 230), (230, 180, 200)],
        "hygiene_deo": [(200, 230, 255), (150, 200, 240)],
        "soin_visage": [(245, 240, 235), (220, 210, 200)],
        "soin_corps": [(240, 220, 180), (200, 170, 130)],
    }
    colors = GRADIENTS.get(category, [(230, 230, 230), (200, 200, 200)])
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        t = y / size[1]
        r = int(colors[0][0] * (1 - t) + colors[1][0] * t)
        g = int(colors[0][1] * (1 - t) + colors[1][1] * t)
        b = int(colors[0][2] * (1 - t) + colors[1][2] * t)
        draw.line([(0, y), (size[0], y)], fill=(r, g, b))
    return img


# ══════════════════════════════════════════════════════════════════════════
#  Composition: product + shadow + decor
# ══════════════════════════════════════════════════════════════════════════

def compose_final(product_rgba: Image.Image, decor: Image.Image) -> Image.Image:
    """
    Composite product (with drop shadow) onto decor background.
    """
    target_size = (1080, 1080)
    decor_resized = decor.resize(target_size, Image.LANCZOS)

    # Scale product to ~55% of canvas height, centered horizontally
    max_h = int(target_size[1] * 0.55)
    aspect = product_rgba.width / product_rgba.height
    product_h = min(max_h, product_rgba.height)
    product_w = int(product_h * aspect)
    product_resized = product_rgba.resize((product_w, product_h), Image.LANCZOS)

    # ── Drop shadow ───────────────────────────────────────────────────────
    shadow_offset = (8, 12)
    shadow_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
    shadow_img = Image.new("RGBA", (product_w, product_h), (0, 0, 0, 0))
    # Draw shadow using alpha channel
    alpha_ch = product_resized.split()[3]
    dark = Image.new("RGBA", (product_w, product_h), (20, 20, 20, 140))
    dark.putalpha(alpha_ch)

    pos_x = (target_size[0] - product_w) // 2
    pos_y = (target_size[1] - product_h) // 2 + int(target_size[1] * 0.05)

    shadow_layer.paste(dark, (pos_x + shadow_offset[0], pos_y + shadow_offset[1]), dark)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=18))

    # ── Composite layers ─────────────────────────────────────────────────
    canvas = decor_resized.convert("RGBA")
    canvas.paste(shadow_layer, (0, 0), shadow_layer)
    canvas.paste(product_resized, (pos_x, pos_y), product_resized)
    return canvas.convert("RGB")


# ══════════════════════════════════════════════════════════════════════════
#  Marketing text generation (Qwen2.5 / Ollama)
# ══════════════════════════════════════════════════════════════════════════

def generate_marketing_text(
    name: str,
    category: str,
    tone: Optional[str] = "luxe",
) -> dict:
    """
    Generate marketing text using Qwen2.5 via Ollama.

    Returns:
      {titre, sous_titre, bullets: [str, str, str], cta}

    Retries up to 3 times on invalid JSON. Falls back to template if all fail.
    """
    tone_info = TONE_STYLES.get(tone or "luxe", TONE_STYLES["luxe"])
    adj = ", ".join(tone_info["adjectives"])
    cta_verb = tone_info["cta_verbs"][0]

    system_prompt = (
        "Tu es un expert en marketing cosmétique haut de gamme. "
        "Tu réponds TOUJOURS en JSON valide, sans aucun texte avant ou après. "
        "Le JSON doit avoir exactement ces 4 clés: titre, sous_titre, bullets, cta. "
        "bullets est une liste de 3 chaînes de caractères maximum."
    )

    user_prompt = (
        f"Génère le texte marketing pour ce produit cosmétique:\n"
        f"Nom: {name}\n"
        f"Catégorie: {category}\n"
        f"Ton souhaité: {tone} (adjectifs: {adj})\n\n"
        f"Format de réponse OBLIGATOIRE (JSON strict):\n"
        f'{{"titre": "...", "sous_titre": "...", "bullets": ["...", "...", "..."], '
        f'"cta": "{cta_verb} maintenant"}}'
    )

    for attempt in range(3):
        try:
            import ollama
            response = ollama.chat(
                model="qwen2.5",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": 0.7},
            )
            raw = response["message"]["content"].strip()

            # Extract JSON even if there's surrounding text
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                if all(k in parsed for k in ["titre", "sous_titre", "bullets", "cta"]):
                    logger.info(f"Marketing text generated (attempt {attempt + 1})")
                    return parsed
        except Exception as e:
            logger.warning(f"Marketing text attempt {attempt + 1} failed: {e}")

    # ── Template fallback ─────────────────────────────────────────────────
    logger.warning("Using template fallback for marketing text")
    return {
        "titre": f"{name} — Excellence Cosmétique",
        "sous_titre": f"La formule {adj.split(',')[0]} pour votre beauté",
        "bullets": [
            "✨ Formule haute performance",
            "🌿 Ingrédients sélectionnés",
            "💎 Résultats visibles dès la 1ère utilisation",
        ],
        "cta": f"{cta_verb} la collection",
    }


# ══════════════════════════════════════════════════════════════════════════
#  Poster rendering (Pillow — 3 templates)
# ══════════════════════════════════════════════════════════════════════════

def _load_font(size: int, bold: bool = False) -> ImageFont:
    """Load DejaVu font or fall back to PIL default."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/content/fonts/Outfit-Bold.ttf" if bold else "/content/fonts/Outfit-Regular.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont, max_width: int, draw: ImageDraw) -> list[str]:
    """Wrap text to fit max_width pixels."""
    words = text.split()
    lines = []
    current = []
    for word in words:
        test_line = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def render_poster(
    composite: Image.Image,
    text: dict,
    template: str = "classic",
) -> Image.Image:
    """
    Overlay marketing text onto composite image.

    Templates:
      classic  → header (titre/sous_titre) top, bullets mid, CTA banner bottom
      minimal  → titre bottom-left, minimal bullets, clean layout
      bold     → large title center-bottom, strong CTA, high contrast
    """
    img = composite.copy().convert("RGBA")
    draw = ImageDraw.Draw(img)
    W, H = img.size  # 1080 x 1080

    titre = text.get("titre", "")
    sous_titre = text.get("sous_titre", "")
    bullets = text.get("bullets", [])
    cta = text.get("cta", "")

    if template == "classic":
        _render_classic(draw, img, W, H, titre, sous_titre, bullets, cta)
    elif template == "minimal":
        _render_minimal(draw, img, W, H, titre, sous_titre, bullets, cta)
    elif template == "bold":
        _render_bold(draw, img, W, H, titre, sous_titre, bullets, cta)
    else:
        _render_classic(draw, img, W, H, titre, sous_titre, bullets, cta)

    return img.convert("RGB")


def _render_classic(draw, img, W, H, titre, sous_titre, bullets, cta):
    """Classic layout: header top, bullets centered, CTA banner bottom."""
    # ── Top header band ───────────────────────────────────────────────────
    header_h = 130
    overlay = Image.new("RGBA", (W, header_h), (15, 15, 25, 200))
    img.paste(overlay, (0, 0), overlay)

    # Titre
    f_title = _load_font(42, bold=True)
    draw.text((W // 2, 40), titre, font=f_title, fill=(255, 255, 255), anchor="mm")

    # Sous-titre
    f_sub = _load_font(22)
    draw.text((W // 2, 95), sous_titre, font=f_sub, fill=(200, 200, 200), anchor="mm")

    # ── Bullets (right side overlay) ──────────────────────────────────────
    f_bullet = _load_font(20)
    bullet_start_y = H // 2 - 40
    for i, bullet in enumerate(bullets[:3]):
        by = bullet_start_y + i * 44
        # Semi-transparent pill behind bullet
        pill = Image.new("RGBA", (min(len(bullet) * 13 + 30, W - 40), 36), (0, 0, 0, 140))
        img.paste(pill, (30, by - 5), pill)
        draw.text((45, by), bullet, font=f_bullet, fill=(255, 255, 255))

    # ── CTA bottom banner ─────────────────────────────────────────────────
    cta_h = 80
    cta_overlay = Image.new("RGBA", (W, cta_h), (220, 170, 80, 230))
    img.paste(cta_overlay, (0, H - cta_h), cta_overlay)
    f_cta = _load_font(32, bold=True)
    draw.text((W // 2, H - cta_h // 2), cta.upper(), font=f_cta, fill=(15, 15, 25), anchor="mm")


def _render_minimal(draw, img, W, H, titre, sous_titre, bullets, cta):
    """Minimal layout: clean bottom-left title block, subtle bullets."""
    # ── Bottom text block ─────────────────────────────────────────────────
    block_h = 200
    overlay = Image.new("RGBA", (W, block_h), (255, 255, 255, 220))
    img.paste(overlay, (0, H - block_h), overlay)

    f_title = _load_font(38, bold=True)
    draw.text((40, H - block_h + 25), titre, font=f_title, fill=(20, 20, 20))

    f_sub = _load_font(20)
    draw.text((40, H - block_h + 80), sous_titre, font=f_sub, fill=(80, 80, 80))

    # Bullets as dots
    f_bullet = _load_font(16)
    bx = 40
    for i, bullet in enumerate(bullets[:3]):
        draw.ellipse([(bx, H - block_h + 120 + i * 28), (bx + 6, H - block_h + 126 + i * 28)], fill=(180, 140, 60))
        draw.text((bx + 14, H - block_h + 112 + i * 28), bullet, font=f_bullet, fill=(60, 60, 60))

    # CTA as right-aligned text
    f_cta = _load_font(22, bold=True)
    draw.text((W - 40, H - 45), f"→ {cta}", font=f_cta, fill=(180, 140, 60), anchor="rm")


def _render_bold(draw, img, W, H, titre, sous_titre, bullets, cta):
    """Bold layout: large title, high-contrast CTA, strong presence."""
    # ── Large title centered (2/3 down) ───────────────────────────────────
    title_y = int(H * 0.68)

    # Text shadow
    f_title = _load_font(52, bold=True)
    for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
        draw.text((W // 2 + dx, title_y + dy), titre, font=f_title, fill=(0, 0, 0, 180), anchor="mm")
    draw.text((W // 2, title_y), titre, font=f_title, fill=(255, 255, 255), anchor="mm")

    # Sous-titre
    f_sub = _load_font(24)
    draw.text((W // 2, title_y + 60), sous_titre, font=f_sub, fill=(230, 200, 150), anchor="mm")

    # ── CTA pill button ───────────────────────────────────────────────────
    cta_y = int(H * 0.87)
    cta_w = min(len(cta) * 20 + 80, W - 100)
    cta_x = (W - cta_w) // 2
    pill = Image.new("RGBA", (cta_w, 55), (220, 170, 60, 240))
    img.paste(pill, (cta_x, cta_y - 27), pill)
    f_cta = _load_font(28, bold=True)
    draw.text((W // 2, cta_y), cta.upper(), font=f_cta, fill=(20, 20, 20), anchor="mm")

    # ── Minimal bullets top-left ──────────────────────────────────────────
    f_bullet = _load_font(18)
    for i, bullet in enumerate(bullets[:3]):
        draw.text((30, 30 + i * 35), f"• {bullet}", font=f_bullet, fill=(255, 255, 255))


# ══════════════════════════════════════════════════════════════════════════
#  Export to 3 social formats
# ══════════════════════════════════════════════════════════════════════════

FORMAT_DIMENSIONS: dict[str, tuple[int, int]] = {
    "instagram": (1080, 1080),
    "facebook": (1200, 630),
    "linkedin": (1200, 627),
}


def export_formats(poster: Image.Image) -> dict[str, Image.Image]:
    """
    Resize/crop poster to the 3 social media dimensions.
    Uses COVER crop strategy: fill the frame without distortion.
    """
    results = {}
    for fmt_name, (tw, th) in FORMAT_DIMENSIONS.items():
        img = poster.copy()
        ow, oh = img.size

        # Scale to cover target
        scale = max(tw / ow, th / oh)
        new_w = int(ow * scale)
        new_h = int(oh * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Center crop
        left = (new_w - tw) // 2
        top = (new_h - th) // 2
        img = img.crop((left, top, left + tw, top + th))
        results[fmt_name] = img

    return results


# ══════════════════════════════════════════════════════════════════════════
#  Main orchestrator
# ══════════════════════════════════════════════════════════════════════════

def run_pipeline(generation_id: str, db: Session) -> None:
    """
    Full pipeline orchestrator — runs as a FastAPI BackgroundTask.

    Steps:
      1. Load generation + product from DB
      2. Remove background from product image
      3. Detect / confirm category
      4. Generate decor (SDXL)
      5. Compose product + decor
      6. Generate marketing text (Qwen2.5)
      7. Render poster (3 templates)
      8. Export to 3 social formats
      9. Upload to Cloudinary + save Assets in DB
    """
    import httpx

    gen_uuid = uuid.UUID(generation_id)

    def _update_status(status: GenerationStatus, error: Optional[str] = None):
        db.query(Generation).filter(Generation.id == gen_uuid).update(
            {"status": status, "error_message": error, "updated_at": datetime.now(timezone.utc)}
        )
        db.commit()

    with _gpu_semaphore:
        try:
            # ── Load from DB ────────────────────────────────────────────────
            generation: Generation = db.get(Generation, gen_uuid)
            if not generation:
                logger.error(f"Generation {generation_id} not found in DB")
                return

            product: Product = generation.product
            _update_status(GenerationStatus.PROCESSING)

            tone = generation.tone or "luxe"
            template = generation.template or "classic"

            # ── Step 1: Download product image ─────────────────────────────
            logger.info(f"[{generation_id}] Step 1: Downloading product image")
            image_resp = httpx.get(product.original_image_url, timeout=30)
            image_resp.raise_for_status()
            image_bytes = image_resp.content

            # ── Step 2: Remove background ──────────────────────────────────
            logger.info(f"[{generation_id}] Step 2: Removing background")
            product_rgba = remove_background(image_bytes)

            # Upload cutout
            cutout_url = upload_pil_image(
                product_rgba, folder="cosmetique_ai/cutouts",
                public_id=f"cutout_{product.id}"
            )
            db.query(Product).filter(Product.id == product.id).update(
                {"cutout_image_url": cutout_url}
            )
            db.commit()

            # ── Step 3: Detect category ────────────────────────────────────
            logger.info(f"[{generation_id}] Step 3: Detecting category")
            category = product.category or detect_category(product.name)
            if not product.category:
                db.query(Product).filter(Product.id == product.id).update(
                    {"category": category}
                )
                db.commit()

            # ── Step 4: Generate decor ─────────────────────────────────────
            logger.info(f"[{generation_id}] Step 4: Generating decor")
            decor_img = generate_decor(product_rgba, category)

            # ── Step 5: Compose final ──────────────────────────────────────
            logger.info(f"[{generation_id}] Step 5: Compositing")
            composite = compose_final(product_rgba, decor_img)

            # ── Step 6: Marketing text ─────────────────────────────────────
            logger.info(f"[{generation_id}] Step 6: Generating marketing text")
            marketing_text = generate_marketing_text(product.name, category, tone)

            # Save text to DB
            db.query(Generation).filter(Generation.id == gen_uuid).update(
                {"marketing_text": marketing_text, "updated_at": datetime.now(timezone.utc)}
            )
            db.commit()

            # ── Step 7: Render poster ──────────────────────────────────────
            logger.info(f"[{generation_id}] Step 7: Rendering poster ({template})")
            poster = render_poster(composite, marketing_text, template)

            # ── Step 8: Export formats ─────────────────────────────────────
            logger.info(f"[{generation_id}] Step 8: Exporting 3 formats")
            format_images = export_formats(poster)

            # ── Step 9: Upload + save assets ──────────────────────────────
            logger.info(f"[{generation_id}] Step 9: Uploading to Cloudinary")
            for fmt_name, fmt_img in format_images.items():
                url = upload_pil_image(
                    fmt_img,
                    folder="cosmetique_ai/generations",
                    public_id=f"gen_{generation_id}_{fmt_name}",
                )
                w, h = FORMAT_DIMENSIONS[fmt_name]
                asset = Asset(
                    generation_id=gen_uuid,
                    format=AssetFormat(fmt_name),
                    url=url,
                    width=w,
                    height=h,
                )
                db.add(asset)

            db.commit()
            _update_status(GenerationStatus.DONE)
            logger.info(f"[{generation_id}] ✅ Pipeline complete!")

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"[{generation_id}] Pipeline error:\n{tb}")
            _update_status(GenerationStatus.ERROR, error=str(e)[:500])
        finally:
            # Always release GPU memory
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
