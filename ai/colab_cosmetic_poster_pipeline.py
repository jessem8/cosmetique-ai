# %% [markdown]
# # Colab - Pipeline IA affiches cosmetiques
#
# Execution conseillee:
# 1. Runtime > Change runtime type > T4 GPU.
# 2. Executer les cellules dans l'ordre.
# 3. Pour une demo web, lancer la derniere cellule FastAPI + ngrok.

# %%
!pip install -q pillow rembg[gpu] onnxruntime-gpu paddleocr paddlepaddle
!pip install -q diffusers==0.27.2 transformers==4.40.2 accelerate==0.29.3 safetensors==0.4.3
!pip install -q fastapi uvicorn python-multipart nest-asyncio pyngrok requests

import io
import json
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT = Path("/content/cosmetic_ai")
INPUT_DIR = ROOT / "inputs"
OUTPUT_DIR = ROOT / "outputs"
for folder in [INPUT_DIR, OUTPUT_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# %%
def preprocess_image(image_path: str | Path, max_side: int = 1536) -> Image.Image:
    image = Image.open(image_path)
    image = ImageOps.exif_transpose(image).convert("RGB")
    if max(image.size) > max_side:
        ratio = max_side / max(image.size)
        image = image.resize((round(image.width * ratio), round(image.height * ratio)), Image.LANCZOS)
    return image


def remove_product_background(image: Image.Image) -> Image.Image:
    from rembg import new_session, remove

    session = new_session("isnet-general-use")
    return remove(
        image,
        session=session,
        alpha_matting=True,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=10,
        alpha_matting_erode_size=5,
    ).convert("RGBA")


def extract_label_text(image_path: str | Path) -> str:
    try:
        from paddleocr import PaddleOCR

        ocr = PaddleOCR(use_angle_cls=True, lang="fr", show_log=False)
        result = ocr.ocr(str(image_path), cls=True)
        lines = []
        for page in result or []:
            for line in page or []:
                lines.append(line[1][0])
        return " | ".join(lines)
    except Exception as exc:
        print("OCR indisponible:", exc)
        return ""

# %%
CATEGORY_STYLES = {
    "soin_visage": "pristine white spa setting, marble surface, soft pearl tones, clinical premium skincare mood",
    "soin_corps": "warm natural spa, wooden surface, shea butter texture, botanical leaves, soft warm light",
    "cheveux": "clean bathroom or salon setting, silk texture, botanical oils, fresh diffused daylight",
    "maquillage": "high-end beauty editorial set, satin fabric, rose gold highlights, glamorous studio lighting",
    "solaire": "sunny beach setting, turquoise water blur, sand texture, tropical leaves, golden hour light",
    "hygiene_deo": "fresh clean bathroom, eucalyptus leaves, water droplets, cool blue and white tones",
    "nail_care": "fresh Atlantic cosmetic nail care campaign, sea water reflections, white stone, blue and white palette",
}


def detect_category(text: str, fallback: str = "soin_visage") -> str:
    normalized = text.lower()
    rules = {
        "nail_care": ["dissolvant", "ongles", "vernis", "nail"],
        "solaire": ["spf", "solaire", "sun", "uva", "uvb"],
        "cheveux": ["shampoing", "shampoo", "cheveux", "hair", "keratine"],
        "maquillage": ["mascara", "rouge", "blush", "makeup", "fond de teint"],
        "hygiene_deo": ["deodorant", "deo", "gel douche", "savon"],
        "soin_corps": ["lait corps", "karite", "body", "corps"],
    }
    for category, keywords in rules.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return fallback


def build_image_prompt(category: str, palette: str = "premium brand colors") -> tuple[str, str]:
    style = CATEGORY_STYLES.get(category, CATEGORY_STYLES["soin_visage"])
    prompt = (
        f"professional cosmetic product advertisement background, {style}, "
        f"{palette}, realistic commercial lighting, elegant surface, "
        f"clean negative space for text, photorealistic, high-end advertising photography"
    )
    negative = (
        "text, letters, watermark, logo, fake product, extra bottle, deformed object, "
        "distorted packaging, blurry, low quality, cartoon, illustration, messy background"
    )
    return prompt, negative

# %%
def prepare_inpaint_canvas(product_rgba: Image.Image, size: tuple[int, int] = (1024, 1024)):
    canvas = Image.new("RGBA", size, (255, 255, 255, 255))
    ratio = min(size[0] * 0.48 / product_rgba.width, size[1] * 0.70 / product_rgba.height)
    product_size = (round(product_rgba.width * ratio), round(product_rgba.height * ratio))
    product = product_rgba.resize(product_size, Image.LANCZOS)
    x = (size[0] - product.width) // 2
    y = size[1] - product.height - round(size[1] * 0.08)
    canvas.alpha_composite(product, (x, y))

    product_alpha = Image.new("L", size, 0)
    product_alpha.paste(product.getchannel("A"), (x, y))
    product_alpha = product_alpha.filter(ImageFilter.MaxFilter(9))
    mask = Image.new("L", size, 255)
    mask.paste(0, mask=product_alpha)
    return canvas.convert("RGB"), mask, product, (x, y)


def gradient_fallback(size: tuple[int, int], category: str) -> Image.Image:
    palettes = {
        "nail_care": ((210, 236, 255), (22, 87, 172)),
        "solaire": ((255, 225, 143), (44, 164, 208)),
        "soin_corps": ((250, 230, 200), (166, 123, 82)),
        "maquillage": ((255, 220, 232), (161, 72, 108)),
        "cheveux": ((244, 232, 246), (164, 190, 176)),
        "hygiene_deo": ((225, 245, 255), (98, 180, 220)),
    }
    top, bottom = palettes.get(category, ((245, 244, 239), (205, 198, 188)))
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        t = y / max(size[1] - 1, 1)
        color = tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line([(0, y), (size[0], y)], fill=color)
    return image

# %%
def load_sdxl_inpaint():
    import torch
    from diffusers import AutoPipelineForInpainting, DPMSolverMultistepScheduler

    pipe = AutoPipelineForInpainting.from_pretrained(
        "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
        torch_dtype=torch.float16,
        variant="fp16",
        use_safetensors=True,
    ).to("cuda")
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
    pipe.enable_vae_slicing()
    return pipe


def generate_decor(pipe, canvas: Image.Image, mask: Image.Image, prompt: str, negative: str, seed: int = 42):
    import torch

    generator = torch.Generator("cuda").manual_seed(seed)
    return pipe(
        prompt=prompt,
        negative_prompt=negative,
        image=canvas,
        mask_image=mask,
        num_inference_steps=30,
        guidance_scale=6.8,
        strength=0.99,
        generator=generator,
    ).images[0]

# %%
def add_shadow(layer: Image.Image, product: Image.Image, xy: tuple[int, int]) -> Image.Image:
    x, y = xy
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    alpha = product.getchannel("A").filter(ImageFilter.GaussianBlur(22))
    dark = Image.new("RGBA", product.size, (0, 0, 0, 110))
    dark.putalpha(alpha)
    shadow.alpha_composite(dark, (x + 16, y + 24))
    return Image.alpha_composite(layer.convert("RGBA"), shadow)


def compose_scene(decor: Image.Image, product: Image.Image, xy: tuple[int, int]) -> Image.Image:
    layer = decor.convert("RGBA")
    layer = add_shadow(layer, product, xy)
    layer.alpha_composite(product, xy)
    layer = ImageEnhance.Contrast(layer.convert("RGB")).enhance(1.05)
    layer = ImageEnhance.Color(layer).enhance(1.04)
    return layer


def load_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def fallback_marketing_text(product_name: str, ocr_text: str) -> dict:
    title = product_name[:34] if product_name else "Votre rituel beaute"
    bullets = []
    for token in ["soin", "douceur", "provitamine", "hydrat", "frais"]:
        if token in ocr_text.lower():
            bullets.append(token.capitalize())
    bullets = (bullets + ["Soin quotidien", "Texture agreable", "Finition premium"])[:3]
    return {
        "titre": title,
        "sous_titre": "Une presentation elegante, prete pour vos reseaux.",
        "bullets": bullets,
        "cta": "Decouvrir",
        "hashtags": ["#beaute", "#cosmetique", "#skincare", "#beauty", "#routine"],
    }


def render_social_poster(scene: Image.Image, text: dict, size: tuple[int, int]) -> Image.Image:
    poster = ImageOps.fit(scene, size, method=Image.LANCZOS, centering=(0.5, 0.5)).convert("RGBA")
    draw = ImageDraw.Draw(poster)
    w, h = size
    band_h = round(h * 0.24)
    band = Image.new("RGBA", (w, band_h), (255, 255, 255, 225))
    poster.alpha_composite(band, (0, h - band_h))

    title_font = load_font(max(32, round(h * 0.055)), bold=True)
    sub_font = load_font(max(18, round(h * 0.027)))
    bullet_font = load_font(max(16, round(h * 0.022)))

    x = round(w * 0.06)
    y = h - band_h + round(h * 0.035)
    draw.text((x, y), text["titre"], font=title_font, fill=(20, 35, 60))
    draw.text((x, y + round(h * 0.065)), text["sous_titre"], font=sub_font, fill=(55, 70, 90))
    for index, bullet in enumerate(text.get("bullets", [])[:3]):
        draw.text((x, y + round(h * 0.11) + index * round(h * 0.033)), f"- {bullet}", font=bullet_font, fill=(35, 60, 100))

    cta = text.get("cta", "Decouvrir").upper()
    cta_font = load_font(max(18, round(h * 0.026)), bold=True)
    box_w = min(round(w * 0.30), 260)
    box = [w - x - box_w, h - round(h * 0.08), w - x, h - round(h * 0.035)]
    draw.rounded_rectangle(box, radius=10, fill=(22, 87, 172))
    draw.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), cta, font=cta_font, fill="white", anchor="mm")
    return poster.convert("RGB")

# %%
FORMATS = {
    "instagram": (1080, 1080),
    "facebook": (1200, 630),
    "linkedin": (1200, 627),
}


def run_one_product(image_path: str | Path, product_name: str = "", palette: str = "blue and white"):
    image = preprocess_image(image_path)
    cutout = remove_product_background(image)
    ocr_text = extract_label_text(image_path)
    category = detect_category(product_name + " " + ocr_text)
    prompt, negative = build_image_prompt(category, palette)
    canvas, mask, product, xy = prepare_inpaint_canvas(cutout)

    try:
        pipe = load_sdxl_inpaint()
        decor = generate_decor(pipe, canvas, mask, prompt, negative)
    except Exception as exc:
        print("SDXL indisponible, fallback gradient:", exc)
        decor = gradient_fallback((1024, 1024), category)

    scene = compose_scene(decor, product, xy)
    text = fallback_marketing_text(product_name or "Produit cosmetique", ocr_text)

    paths = {}
    for name, size in FORMATS.items():
        poster = render_social_poster(scene, text, size)
        output_path = OUTPUT_DIR / f"poster_{name}.jpg"
        poster.save(output_path, quality=95)
        paths[name] = str(output_path)

    return {"category": category, "ocr_text": ocr_text, "prompt": prompt, "text": text, "outputs": paths}

# %%
# Exemple:
# from google.colab import files
# uploaded = files.upload()
# image_path = next(iter(uploaded.keys()))
# result = run_one_product(image_path, product_name="Nihel Dissolvant doux a l'Atlantic", palette="blue, white, ocean fresh")
# result

# %%
# Serveur demo optionnel pour connecter ton backend/frontend via ngrok.
# Remplace TON_TOKEN_NGROK avant d'executer.
"""
!pip install -q fastapi uvicorn pyngrok nest-asyncio python-multipart
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
from pyngrok import ngrok
import nest_asyncio
import uvicorn

app = FastAPI()

@app.post("/generer-affiche")
async def generer_affiche(
    fichier: UploadFile = File(...),
    nom_produit: str = Form("Produit cosmetique"),
    palette: str = Form("premium brand colors"),
    format_sortie: str = Form("instagram"),
):
    input_path = INPUT_DIR / fichier.filename
    input_path.write_bytes(await fichier.read())
    result = run_one_product(input_path, product_name=nom_produit, palette=palette)
    return FileResponse(result["outputs"][format_sortie], media_type="image/jpeg")

ngrok.set_auth_token("TON_TOKEN_NGROK")
public_url = ngrok.connect(8000).public_url
print("URL Colab:", public_url)
nest_asyncio.apply()
uvicorn.run(app, port=8000)
"""
